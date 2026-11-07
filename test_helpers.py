"""Shared test setup: a throwaway database, signed-in test users, and MCP client sessions.

Importing this module isolates the run *before* db/auth are imported:
- Database: a temp SQLite file, or, when TEST_DATABASE_URL is set (CI), a fresh
  PostgreSQL schema that is dropped at exit.
- Cache: in-process memory, or, when TEST_REDIS_URL is set, Redis under a unique key prefix.
Variables are set explicitly (even to "") so a developer's .env can never point tests at
real data: load_dotenv() does not override variables that already exist.
"""
import atexit
import json
import os
import sys
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from cryptography.fernet import Fernet

RUN_ID = uuid.uuid4().hex[:10]
os.environ["BOOKING_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "")
os.environ["DATABASE_SCHEMA"] = ""
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "")
os.environ["CACHE_PREFIX"] = f"test:{RUN_ID}:"
os.environ["MFA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["COOKIE_SECURE"] = "false"
os.environ.pop("BOOKING_SESSION_TOKEN", None)
for _kind in ("FLIGHT", "HOTEL", "CAR", "TRAIN"):
    os.environ[f"{_kind}_PROVIDER"] = "mock"

if os.environ["DATABASE_URL"]:
    import psycopg

    _schema = f"test_{RUN_ID}"
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as _c:
        _c.execute(f'CREATE SCHEMA "{_schema}"')
    os.environ["DATABASE_SCHEMA"] = _schema

    @atexit.register
    def _drop_schema():
        import database
        database.close_pool()
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{_schema}" CASCADE')

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402

PASSWORD = "correct horse battery staple"
HERE = Path(__file__).parent

db.init_db()
auth.init()


def make_user(email: str, name: str, role: str = "customer") -> str:
    """Register a user, enrol MFA with a computed TOTP code, and return a session token."""
    user = auth.register(email, PASSWORD, name)
    if role != "customer":
        auth.set_role(email, role)
    setup = auth.begin_mfa_enrollment(user["id"])
    auth.confirm_mfa_enrollment(user["id"], auth.totp_now(setup["secret"]))
    return auth.issue_session(user["id"], mfa_verified=True)


@asynccontextmanager
async def mcp_session(server: str, token: str | None):
    """Launch an MCP server over stdio signed in with `token` (or anonymously if None)."""
    env = {k: v for k, v in os.environ.items() if k != "BOOKING_SESSION_TOKEN"}
    if token:
        env["BOOKING_SESSION_TOKEN"] = token
    params = StdioServerParameters(command=sys.executable, args=[str(HERE / server)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call(session: ClientSession, name: str, **args) -> dict:
    result = await session.call_tool(name, args)
    text = result.content[0].text if result.content else ""
    if getattr(result, "is_error", None) or getattr(result, "isError", False):  # SDK 2.x / 1.x
        raise AssertionError(f"{name} raised a tool error: {text}")
    data = json.loads(text) if text else None
    shown = data
    if isinstance(data, dict) and data.get("offers"):
        shown = {**data, "offers": data["offers"][:1] + (["..."] if len(data["offers"]) > 1 else [])}
    print(f"\n> {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})\n{json.dumps(shown, indent=2)}")
    return data

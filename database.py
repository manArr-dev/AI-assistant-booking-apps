"""
Database access for SQLite (local dev, tests) and PostgreSQL (staging, production).

    DATABASE_URL=postgresql://user:pass@host:5432/booking   -> PostgreSQL (pooled, psycopg 3)
    DATABASE_URL unset                                      -> SQLite file BOOKING_DB (default bookings.db)

The application writes SQL once, with "?" placeholders and portable SQL. This module
translates placeholders for Postgres, returns rows addressable by column name, pools
Postgres connections, and runs versioned schema migrations (see MIGRATIONS).

    with connect() as conn:              # autocommit: each statement commits on its own
        conn.execute("SELECT ... WHERE id = ?", (x,)).fetchone()["col"]

    with transaction(lock="bookings") as conn:   # one atomic unit, serialised on a named lock
        ...

Timestamps are stored as ISO-8601 UTC text so the same queries work on both engines.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import zlib
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("database")

_pool = None
_pool_lock = threading.Lock()
_migrated: set[str] = set()


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def is_postgres() -> bool:
    return database_url().startswith(("postgres://", "postgresql://"))


def sqlite_path() -> str:
    return os.environ.get("BOOKING_DB", str(Path(__file__).with_name("bookings.db")))


def _target() -> str:
    """Identifies the current database (env can change between tests)."""
    if is_postgres():
        return f"pg:{database_url()}#{os.environ.get('DATABASE_SCHEMA', '')}"
    return f"sqlite:{sqlite_path()}"


# ---------------------------------------------------------------- connections

class Connection:
    """Same small API on both engines: execute / executemany / begin / commit / rollback / close."""

    def __init__(self, raw, release: Callable[[object], None], postgres: bool):
        self.raw = raw
        self._release = release
        self.postgres = postgres
        self._closed = False

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.postgres else sql

    def execute(self, sql: str, params: tuple | list = ()):
        return self.raw.execute(self._sql(sql), params)

    def executemany(self, sql: str, seq):
        if self.postgres:
            with self.raw.cursor() as cur:
                cur.executemany(self._sql(sql), seq)
                return cur
        return self.raw.executemany(sql, seq)

    def begin(self, lock: str | None = None) -> None:
        """Start a transaction. `lock` serialises writers that use the same name
        (SQLite: BEGIN IMMEDIATE takes the database write lock; Postgres: advisory lock)."""
        if self.postgres:
            self.raw.execute("BEGIN")
            if lock:
                self.raw.execute("SELECT pg_advisory_xact_lock(%s)", (zlib.crc32(lock.encode()),))
        else:
            self.raw.execute("BEGIN IMMEDIATE" if lock else "BEGIN")

    def commit(self) -> None:
        self.raw.execute("COMMIT")

    def rollback(self) -> None:
        try:
            self.raw.execute("ROLLBACK")
        except Exception as e:  # noqa: BLE001 - e.g. no transaction open, or connection already broken
            log.debug("rollback skipped: %s", e)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._release(self.raw)

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _pg_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            schema = os.environ.get("DATABASE_SCHEMA", "").strip()

            def configure(conn):
                if schema:  # used by tests to isolate runs in one database
                    conn.execute(f'SET search_path TO "{schema}"')

            _pool = ConnectionPool(
                database_url(),
                min_size=int(os.environ.get("DB_POOL_MIN", "1")),
                max_size=int(os.environ.get("DB_POOL_MAX", "10")),
                kwargs={"autocommit": True, "row_factory": dict_row},
                configure=configure,
                check=ConnectionPool.check_connection,
                timeout=float(os.environ.get("DB_POOL_TIMEOUT", "10")),
                name="booking",
                open=True,
            )
        return _pool


def connect() -> Connection:
    if is_postgres():
        pool = _pg_pool()
        return Connection(pool.getconn(), pool.putconn, postgres=True)
    raw = sqlite3.connect(sqlite_path(), timeout=10, isolation_level=None)  # autocommit
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Connection(raw, lambda c: c.close(), postgres=False)


@contextmanager
def transaction(lock: str | None = None):
    conn = connect()
    try:
        conn.begin(lock)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping() -> bool:
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception as e:  # noqa: BLE001 - readiness probe must never raise
        log.warning("database ping failed: %s", e)
        return False


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


# ---------------------------------------------------------------- migrations
# Append new migrations; never edit one that has shipped. "{pk}" becomes an
# auto-incrementing integer primary key for the engine. Statements use IF NOT EXISTS
# so databases created before migrations existed upgrade cleanly.

def _add_travel_offer_owner(conn: Connection) -> None:
    if conn.postgres:
        conn.execute("ALTER TABLE travel_offers ADD COLUMN IF NOT EXISTS user_id INTEGER")
    elif "user_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(travel_offers)")}:
        conn.execute("ALTER TABLE travel_offers ADD COLUMN user_id INTEGER")


MIGRATIONS: list[tuple[int, str, list]] = [
    (1, "initial schema", [
        """CREATE TABLE IF NOT EXISTS services (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            duration_min INTEGER NOT NULL,
            price DOUBLE PRECISION NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            service_id TEXT NOT NULL REFERENCES services(id),
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            start_ts TEXT NOT NULL,
            end_ts TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            created_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_bookings_start ON bookings(start_ts)",
        "CREATE INDEX IF NOT EXISTS idx_bookings_email ON bookings(customer_email)",
        """CREATE TABLE IF NOT EXISTS users (
            id {pk},
            email TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'customer',
            mfa_enabled INTEGER NOT NULL DEFAULT 0,
            mfa_secret TEXT,
            mfa_pending_secret TEXT,
            last_totp_step INTEGER NOT NULL DEFAULT 0,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until TEXT,
            created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
        """CREATE TABLE IF NOT EXISTS recovery_codes (
            user_id INTEGER NOT NULL REFERENCES users(id),
            code_hash TEXT NOT NULL,
            used_at TEXT,
            PRIMARY KEY (user_id, code_hash))""",
        """CREATE TABLE IF NOT EXISTS auth_events (
            id {pk},
            ts TEXT NOT NULL,
            user_id INTEGER,
            email TEXT,
            event TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS login_challenges (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            expires_at TEXT NOT NULL,
            used_at TEXT)""",
        """CREATE TABLE IF NOT EXISTS travel_offers (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            provider TEXT NOT NULL,
            summary TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            currency TEXT NOT NULL,
            raw TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            claimed INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS travel_bookings (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            provider TEXT NOT NULL,
            offer_id TEXT NOT NULL,
            reference TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            lead_traveller TEXT NOT NULL,
            summary TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            currency TEXT NOT NULL,
            raw TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            created_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_travel_bookings_email ON travel_bookings(customer_email)",
    ]),
    (2, "travel offers belong to the user who searched", [_add_travel_offer_owner]),
    (3, "indexes for expiry cleanup and admin listing", [
        "CREATE INDEX IF NOT EXISTS idx_travel_offers_expiry ON travel_offers(expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_travel_bookings_created ON travel_bookings(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_auth_events_ts ON auth_events(ts)",
    ]),
]

_PK = {
    False: "INTEGER PRIMARY KEY AUTOINCREMENT",
    True: "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
}


def migrate() -> list[int]:
    """Apply pending migrations once per process. Safe to run from many pods at once."""
    target = _target()
    if target in _migrated:
        return []
    applied: list[int] = []
    conn = connect()
    try:
        conn.begin(lock="schema-migrations")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                            version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL)""")
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for version, description, steps in MIGRATIONS:
            if version in done:
                continue
            for step in steps:
                if callable(step):
                    step(conn)
                else:
                    conn.execute(step.replace("{pk}", _PK[conn.postgres]))
            conn.execute("INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
                         (version, description, datetime.now(UTC).isoformat()))
            applied.append(version)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    _migrated.add(target)
    if applied:
        log.info("applied migrations %s", applied)
    return applied

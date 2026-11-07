"""
Who is calling an MCP tool, and are they allowed to?

Identity always comes from a session token issued by auth.py after login + MFA,
never from tool arguments, so the model (or a prompt injection) can't act as
someone else.

- stdio: the agent signs the user in, then launches the server with
  BOOKING_SESSION_TOKEN in its environment.
- HTTP (--http): every request must carry  Authorization: Bearer <session token>
  (get one with `python auth_cli.py login`). The MCP SDK rejects requests
  without a valid token before any tool runs.

The token is re-checked on every tool call, so logout, expiry and MFA resets
take effect immediately.
"""
from __future__ import annotations

import asyncio
import os
import sys

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

import auth

HTTP_MODE = "--http" in sys.argv
SCOPE = "bookings"

NOT_SIGNED_IN = {"ok": False, "error": "You need to sign in first (session missing or expired)."}
FORBIDDEN = {"ok": False, "error": "Your account is not allowed to do that."}


def current_user() -> dict | None:
    if HTTP_MODE:
        access = get_access_token()
        token = access.token if access else None
    else:
        token = os.environ.get("BOOKING_SESSION_TOKEN")
    return auth.user_for_token(token)


def require(role: str | None = None) -> tuple[dict | None, dict | None]:
    """Return (user, None) if allowed, else (None, error_result) to return from the tool."""
    user = current_user()
    if not user:
        return None, NOT_SIGNED_IN
    if role and user["role"] != role:
        return None, FORBIDDEN
    return user, None


class SessionTokenVerifier:
    """MCP TokenVerifier backed by auth.py sessions (used in HTTP mode)."""

    async def verify_token(self, token: str) -> AccessToken | None:
        user = await asyncio.to_thread(auth.user_for_token, token)
        if not user:
            return None
        return AccessToken(token=token, client_id="booking-assistant", scopes=[SCOPE, user["role"]],
                           expires_at=user["session_expires_at"], subject=str(user["id"]))


def server_options(port: int) -> dict:
    """Constructor kwargs that turn on bearer-token auth for the HTTP transport."""
    if not HTTP_MODE:
        return {}
    from mcp.server.auth.settings import AuthSettings
    base = os.environ.get("PUBLIC_BASE_URL", f"http://localhost:{port}").rstrip("/")
    return {
        "token_verifier": SessionTokenVerifier(),
        "auth": AuthSettings(issuer_url=os.environ.get("AUTH_ISSUER_URL", base),
                             resource_server_url=f"{base}/mcp", required_scopes=[SCOPE]),
    }

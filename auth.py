"""
Authentication: accounts, passwords, TOTP multi-factor auth, recovery codes,
sessions and roles. Stored in the same SQLite database as the bookings.

    register()                 create an account (password hashed with scrypt)
    check_password()           step 1 of login, with lockout after repeated failures
    begin/confirm_mfa_enrollment()  pair an authenticator app (TOTP, RFC 6238)
    verify_mfa()               step 2 of login: 6-digit code or one-time recovery code
    issue_session()            only after both steps; returns an opaque session token
    user_for_token()           what the MCP servers call to find out who is calling

Security notes:
- Passwords: scrypt (N=2^15, r=8, p=1) with a per-user salt, compared in constant time.
- MFA secrets are encrypted at rest with Fernet (MFA_ENCRYPTION_KEY, or a generated
  .mfa.key file next to the database). Each TOTP code works once (replay protection).
- Session tokens and recovery codes are stored only as SHA-256 hashes.
- 5 failed password or MFA attempts lock the account for 15 minutes.
- Login errors don't reveal whether an email is registered.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

import database
import db
from db import EMAIL_RE

ROLES = ("customer", "admin")
MIN_PASSWORD_LEN = 12
MAX_PASSWORD_LEN = 128
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
TOTP_PERIOD = 30
TOTP_DIGITS = 6
TOTP_DRIFT_STEPS = 1          # accept the previous/next 30 s window for clock drift
RECOVERY_CODE_COUNT = 8
GENERIC_LOGIN_ERROR = "Incorrect email or password."
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


class AuthError(Exception):
    """An authentication/authorization failure with a message safe to show the user."""


# ---------------------------------------------------------------- config (read lazily, after load_dotenv)

def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}


def require_mfa_for_all() -> bool:
    return _env_flag("REQUIRE_MFA", True)


def session_lifetime() -> timedelta:
    return timedelta(hours=float(os.environ.get("SESSION_HOURS", "8")))


def mfa_issuer() -> str:
    return os.environ.get("MFA_ISSUER") or os.environ.get("BUSINESS_NAME") or "Booking Assistant"


# ---------------------------------------------------------------- schema

def init() -> None:
    """Create/upgrade the auth tables (schema lives in database.MIGRATIONS)."""
    database.migrate()


# ---------------------------------------------------------------- helpers

def _now() -> datetime:
    return datetime.now(UTC)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _audit(conn, event: str, user_id: int | None = None, email: str | None = None) -> None:
    conn.execute("INSERT INTO auth_events (ts, user_id, email, event) VALUES (?, ?, ?, ?)",
                 (_now().isoformat(), user_id, email, event))


def _public_user(row) -> dict:
    return {"id": row["id"], "email": row["email"], "full_name": row["full_name"],
            "role": row["role"], "mfa_enabled": bool(row["mfa_enabled"])}


def _get_user_row(conn, *, email: str | None = None, user_id: int | None = None):
    if email is not None:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, expected = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                            p=int(p), dklen=32, maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, base64.b64decode(expected))


@cache
def _dummy_hash() -> str:
    # Verified against when the email doesn't exist, so response time doesn't reveal it.
    return hash_password(secrets.token_urlsafe(16))


def validate_password(password: str, email: str = "") -> None:
    if not MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN:
        raise AuthError(f"Password must be {MIN_PASSWORD_LEN}-{MAX_PASSWORD_LEN} characters.")
    if email and email.split("@")[0].lower() in password.lower():
        raise AuthError("Password must not contain your email name.")
    if len(set(password)) < 5:
        raise AuthError("Password is too repetitive.")


# ---------------------------------------------------------------- TOTP (RFC 6238)

def totp(secret_b32: str, step: int, digits: int = TOTP_DIGITS) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return str(code).zfill(digits)


def totp_now(secret_b32: str, now: float | None = None) -> str:
    return totp(secret_b32, int((now or time.time()) // TOTP_PERIOD))


def _matching_step(secret_b32: str, code: str, now: float | None = None) -> int | None:
    current = int((now or time.time()) // TOTP_PERIOD)
    for step in range(current - TOTP_DRIFT_STEPS, current + TOTP_DRIFT_STEPS + 1):
        if hmac.compare_digest(totp(secret_b32, step), code):
            return step
    return None


def provisioning_uri(secret_b32: str, email: str) -> str:
    issuer = mfa_issuer()
    return (f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret_b32}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD}")


@cache
def _fernet_for(key: str) -> Fernet:
    return Fernet(key.encode())


def _fernet() -> Fernet:
    key = os.environ.get("MFA_ENCRYPTION_KEY", "").strip()
    if not key:
        if database.is_postgres():
            # Several pods share one database, so they must share one key: never generate it per pod.
            raise RuntimeError("MFA_ENCRYPTION_KEY must be set when DATABASE_URL points to PostgreSQL.")
        path = Path(database.sqlite_path()).with_name(".mfa.key")
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(Fernet.generate_key().decode())
        key = path.read_text().strip()
    return _fernet_for(key)


def _encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def _decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise AuthError("MFA could not be verified (encryption key changed). Ask an admin to reset MFA.") from None


# ---------------------------------------------------------------- lockout

def _locked(row) -> bool:
    return bool(row["locked_until"]) and datetime.fromisoformat(row["locked_until"]) > _now()


def _record_failure(conn, row, event: str) -> None:
    attempts = row["failed_attempts"] + 1
    if attempts >= MAX_FAILED_ATTEMPTS:
        conn.execute("UPDATE users SET failed_attempts = 0, locked_until = ? WHERE id = ?",
                     ((_now() + LOCKOUT).isoformat(), row["id"]))
        _audit(conn, "account_locked", row["id"], row["email"])
    else:
        conn.execute("UPDATE users SET failed_attempts = ? WHERE id = ?", (attempts, row["id"]))
    _audit(conn, event, row["id"], row["email"])


def _check_not_locked(conn, row) -> None:
    if _locked(row):
        _audit(conn, "login_while_locked", row["id"], row["email"])
        raise AuthError("Too many failed attempts. The account is locked for 15 minutes.")


# ---------------------------------------------------------------- accounts

def register(email: str, password: str, full_name: str, role: str = "customer") -> dict:
    email = (email or "").strip().lower()
    full_name = " ".join((full_name or "").split())
    if not EMAIL_RE.match(email):
        raise AuthError("A valid email address is required.")
    if not full_name or len(full_name) > 100:
        raise AuthError("Your full name is required.")
    if role not in ROLES:
        raise AuthError(f"Role must be one of {ROLES}.")
    validate_password(password, email)
    with db._connect() as conn:
        if _get_user_row(conn, email=email):
            raise AuthError("An account with that email already exists.")
        try:
            user_id = conn.execute(
                """INSERT INTO users (email, full_name, password_hash, role, created_at)
                   VALUES (?, ?, ?, ?, ?) RETURNING id""",
                (email, full_name, hash_password(password), role, _now().isoformat())).fetchone()["id"]
        except Exception as e:  # unique violation from a concurrent sign-up with the same email
            if "unique" in str(e).lower():
                raise AuthError("An account with that email already exists.") from e
            raise
        _audit(conn, "registered", user_id, email)
        return _public_user(_get_user_row(conn, user_id=user_id))


def check_password(email: str, password: str) -> dict:
    """Login step 1. Returns the user; does NOT create a session."""
    with db._connect() as conn:
        row = _get_user_row(conn, email=email or "")
        if row is None:
            verify_password(password or "", _dummy_hash())
            _audit(conn, "login_unknown_email", email=(email or "").strip().lower()[:254])
            raise AuthError(GENERIC_LOGIN_ERROR)
        _check_not_locked(conn, row)
        if not verify_password(password or "", row["password_hash"]):
            _record_failure(conn, row, "password_failed")
            raise AuthError(GENERIC_LOGIN_ERROR)
        _audit(conn, "password_ok", row["id"], row["email"])
        return _public_user(row)


def mfa_required(user: dict) -> bool:
    return user["mfa_enabled"] or user["role"] == "admin" or require_mfa_for_all()


def change_password(user_id: int, current: str, new: str) -> None:
    with db._connect() as conn:
        row = _get_user_row(conn, user_id=user_id)
        if not row or not verify_password(current, row["password_hash"]):
            raise AuthError("Current password is incorrect.")
        validate_password(new, row["email"])
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id))
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                     (_now().isoformat(), user_id))
        _audit(conn, "password_changed", user_id, row["email"])


# ---------------------------------------------------------------- MFA

def begin_mfa_enrollment(user_id: int) -> dict:
    """Create a new TOTP secret (not active until confirmed with a valid code)."""
    secret = base64.b32encode(os.urandom(20)).decode().rstrip("=")
    with db._connect() as conn:
        row = _get_user_row(conn, user_id=user_id)
        if not row:
            raise AuthError("Unknown user.")
        conn.execute("UPDATE users SET mfa_pending_secret = ? WHERE id = ?", (_encrypt(secret), user_id))
        _audit(conn, "mfa_enrollment_started", user_id, row["email"])
    return {"secret": secret, "uri": provisioning_uri(secret, row["email"])}


def _new_recovery_codes(conn, user_id: int) -> list[str]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODE_COUNT)]
    conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    conn.executemany("INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)",
                     [(user_id, _sha256(c)) for c in codes])
    return codes


def confirm_mfa_enrollment(user_id: int, code: str, now: float | None = None) -> list[str]:
    """Activate MFA once the user proves their app works. Returns one-time recovery codes."""
    code = "".join((code or "").split())
    with db._connect() as conn:
        row = _get_user_row(conn, user_id=user_id)
        if not row or not row["mfa_pending_secret"]:
            raise AuthError("Start MFA setup first.")
        _check_not_locked(conn, row)
        secret = _decrypt(row["mfa_pending_secret"])
        step = _matching_step(secret, code, now)
        if step is None:
            _record_failure(conn, row, "mfa_enrollment_failed")
            raise AuthError("That code didn't match. Check your phone's clock and try again.")
        conn.execute(
            """UPDATE users SET mfa_secret = mfa_pending_secret, mfa_pending_secret = NULL,
                   mfa_enabled = 1, last_totp_step = ? WHERE id = ?""", (step, user_id))
        codes = _new_recovery_codes(conn, user_id)
        _audit(conn, "mfa_enabled", user_id, row["email"])
    return codes


def verify_mfa(user_id: int, code: str, now: float | None = None) -> None:
    """Login step 2. Accepts a current TOTP code (once) or an unused recovery code."""
    code = "".join((code or "").split()).lower()
    with db._connect() as conn:
        row = _get_user_row(conn, user_id=user_id)
        if not row or not row["mfa_enabled"]:
            raise AuthError("MFA is not set up for this account.")
        _check_not_locked(conn, row)

        if code.isdigit() and len(code) == TOTP_DIGITS:
            step = _matching_step(_decrypt(row["mfa_secret"]), code, now)
            # Conditional update = replay protection: a code (or older one) can't be used twice.
            if step is not None and conn.execute(
                    "UPDATE users SET last_totp_step = ? WHERE id = ? AND last_totp_step < ?",
                    (step, user_id, step)).rowcount == 1:
                _audit(conn, "mfa_ok", user_id, row["email"])
                return
        elif code:
            if conn.execute(
                    """UPDATE recovery_codes SET used_at = ?
                       WHERE user_id = ? AND code_hash = ? AND used_at IS NULL""",
                    (_now().isoformat(), user_id, _sha256(code))).rowcount == 1:
                _audit(conn, "recovery_code_used", user_id, row["email"])
                return

        _record_failure(conn, row, "mfa_failed")
    raise AuthError("Invalid or already-used authentication code.")


def remaining_recovery_codes(user_id: int) -> int:
    with db._connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
                            (user_id,)).fetchone()["n"]


def regenerate_recovery_codes(user_id: int) -> list[str]:
    with db._connect() as conn:
        codes = _new_recovery_codes(conn, user_id)
        _audit(conn, "recovery_codes_regenerated", user_id)
    return codes


# ---------------------------------------------------------------- sessions

def issue_session(user_id: int, mfa_verified: bool) -> str:
    """Create a session after check_password (+ verify_mfa when required)."""
    with db._connect() as conn:
        row = _get_user_row(conn, user_id=user_id)
        if not row:
            raise AuthError("Unknown user.")
        user = _public_user(row)
        if mfa_required(user) and not (mfa_verified and user["mfa_enabled"]):
            raise AuthError("Multi-factor authentication is required for this account.")
        token = "bs_" + secrets.token_urlsafe(32)
        now = _now()
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_sha256(token), user_id, now.isoformat(), (now + session_lifetime()).isoformat()))
        conn.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (user_id,))
        _audit(conn, "session_created", user_id, row["email"])
    return token


def login(email: str, password: str, code: str = "") -> str:
    """Password + MFA in one call (for auth_cli.py and tests). Returns a session token."""
    user = check_password(email, password)
    if mfa_required(user):
        if not user["mfa_enabled"]:
            raise AuthError("MFA setup is required. Run: python auth_cli.py setup-mfa")
        verify_mfa(user["id"], code)
        return issue_session(user["id"], mfa_verified=True)
    return issue_session(user["id"], mfa_verified=False)


def user_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    with db._connect() as conn:
        row = conn.execute(
            """SELECT u.*, s.expires_at AS session_expires_at FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?""",
            (_sha256(token), _now().isoformat())).fetchone()
    if not row:
        return None
    user = _public_user(row)
    if mfa_required(user) and not user["mfa_enabled"]:
        return None  # e.g. an admin reset MFA: old sessions stop working
    user["session_expires_at"] = int(datetime.fromisoformat(row["session_expires_at"]).timestamp())
    return user


def revoke_session(token: str) -> None:
    with db._connect() as conn:
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                     (_now().isoformat(), _sha256(token)))


# ---------------------------------------------------------------- login challenges (web two-step login)
# Between "password OK" and "MFA OK" a web client holds a short-lived, single-use
# challenge token. It grants nothing except the right to attempt the second factor
# (or first-time MFA setup), and MFA failures still count toward the account lockout.

CHALLENGE_TTL = timedelta(minutes=5)


def create_login_challenge(user_id: int) -> str:
    token = "ch_" + secrets.token_urlsafe(32)
    with db._connect() as conn:
        conn.execute("INSERT INTO login_challenges (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (_sha256(token), user_id, (_now() + CHALLENGE_TTL).isoformat()))
    return token


def challenge_user(challenge: str | None) -> dict | None:
    if not challenge:
        return None
    with db._connect() as conn:
        row = conn.execute(
            """SELECT u.* FROM login_challenges c JOIN users u ON u.id = c.user_id
               WHERE c.token_hash = ? AND c.used_at IS NULL AND c.expires_at > ?""",
            (_sha256(challenge), _now().isoformat())).fetchone()
    return _public_user(row) if row else None


def finish_login_challenge(challenge: str) -> None:
    with db._connect() as conn:
        conn.execute("UPDATE login_challenges SET used_at = ? WHERE token_hash = ?",
                     (_now().isoformat(), _sha256(challenge)))


def list_users() -> list[dict]:
    with db._connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [{**_public_user(r), "locked": _locked(r), "created_at": r["created_at"]} for r in rows]


# ---------------------------------------------------------------- admin operations

def set_role(email: str, role: str) -> None:
    if role not in ROLES:
        raise AuthError(f"Role must be one of {ROLES}.")
    with db._connect() as conn:
        if conn.execute("UPDATE users SET role = ? WHERE email = ?",
                        (role, email.strip().lower())).rowcount == 0:
            raise AuthError("No such user.")
        _audit(conn, f"role_set_{role}", email=email.strip().lower())


def reset_mfa(email: str) -> None:
    """For a user who lost their phone and recovery codes. Revokes all their sessions."""
    with db._connect() as conn:
        row = _get_user_row(conn, email=email)
        if not row:
            raise AuthError("No such user.")
        conn.execute("""UPDATE users SET mfa_enabled = 0, mfa_secret = NULL, mfa_pending_secret = NULL,
                            last_totp_step = 0 WHERE id = ?""", (row["id"],))
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (row["id"],))
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                     (_now().isoformat(), row["id"]))
        _audit(conn, "mfa_reset", row["id"], row["email"])


def unlock(email: str) -> None:
    with db._connect() as conn:
        conn.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE email = ?",
                     (email.strip().lower(),))
        _audit(conn, "unlocked", email=email.strip().lower())

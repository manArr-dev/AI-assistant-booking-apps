"""
Unit tests for auth.py: password hashing, TOTP (RFC 6238 vectors), MFA enrolment,
replay protection, recovery codes, lockout, sessions, roles and resets.
No network, no LLM; uses a throwaway database.

    python test_auth.py
"""

import test_helpers  # noqa: F401, I001  (must come first: isolates env + database)

# isort: split
import os
import time

import auth
import db
from auth import AuthError

PW = test_helpers.PASSWORD


def expect_error(fn, *args, contains: str = "", **kwargs) -> str:
    try:
        fn(*args, **kwargs)
    except AuthError as e:
        assert contains.lower() in str(e).lower(), f"unexpected message: {e}"
        return str(e)
    raise AssertionError(f"{fn.__name__} should have raised AuthError")


def enrolled(email: str, role: str = "customer") -> tuple[dict, str, float]:
    """Register + enable MFA. Returns (user, secret, time used for the enrolment code)."""
    user = auth.register(email, PW, "Test User")
    if role != "customer":
        auth.set_role(email, role)
    secret = auth.begin_mfa_enrollment(user["id"])["secret"]
    now = time.time()
    auth.confirm_mfa_enrollment(user["id"], auth.totp_now(secret, now), now=now)
    return user, secret, now


def test_totp_rfc6238_vectors():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32("12345678901234567890")
    for t, code in [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924")]:
        assert auth.totp(secret, t // 30, digits=8) == code


def test_password_hashing():
    h = auth.hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert auth.verify_password(PW, h)
    assert not auth.verify_password(PW + "x", h)
    assert auth.hash_password(PW) != h, "salted"


def test_registration_rules():
    expect_error(auth.register, "short@example.com", "short", "A", contains="characters")
    expect_error(auth.register, "bad-email", PW, "A", contains="email")
    expect_error(auth.register, "sam@example.com", "sam-is-my-password", "Sam", contains="email name")
    auth.register("dupe@example.com", PW, "Dupe")
    expect_error(auth.register, "DUPE@example.com", PW, "Dupe", contains="already exists")


def test_login_errors_dont_reveal_accounts():
    auth.register("known@example.com", PW, "Known")
    a = expect_error(auth.check_password, "known@example.com", "wrong password!!")
    b = expect_error(auth.check_password, "nobody@example.com", "wrong password!!")
    assert a == b == auth.GENERIC_LOGIN_ERROR


def test_mfa_required_before_session():
    user = auth.register("nomfa@example.com", PW, "No MFA")
    expect_error(auth.login, "nomfa@example.com", PW, contains="MFA setup is required")
    expect_error(auth.issue_session, user["id"], mfa_verified=False, contains="required")


def test_enrolment_and_secret_encrypted_at_rest():
    user = auth.register("enrol@example.com", PW, "Enrol")
    secret = auth.begin_mfa_enrollment(user["id"])["secret"]
    expect_error(auth.confirm_mfa_enrollment, user["id"], "000000", contains="didn't match")
    codes = auth.confirm_mfa_enrollment(user["id"], auth.totp_now(secret))
    assert len(codes) == auth.RECOVERY_CODE_COUNT
    with db._connect() as conn:
        row = conn.execute("SELECT mfa_secret, mfa_enabled FROM users WHERE id = ?", (user["id"],)).fetchone()
        stored_codes = [r["code_hash"] for r in conn.execute("SELECT code_hash FROM recovery_codes")]
    assert row["mfa_enabled"] == 1 and secret not in row["mfa_secret"], "secret must be encrypted"
    assert not set(codes) & set(stored_codes), "recovery codes must be hashed"


def test_totp_login_and_replay_protection():
    user, secret, enrolled_at = enrolled("totp@example.com")
    same_code = auth.totp_now(secret, enrolled_at)
    expect_error(auth.verify_mfa, user["id"], same_code, now=enrolled_at, contains="already-used")
    later = enrolled_at + 60
    code = auth.totp_now(secret, later)
    auth.verify_mfa(user["id"], code, now=later)
    expect_error(auth.verify_mfa, user["id"], code, now=later, contains="already-used")
    token = auth.issue_session(user["id"], mfa_verified=True)
    assert auth.user_for_token(token)["email"] == "totp@example.com"


def test_recovery_codes_work_once():
    user = auth.register("recover@example.com", PW, "Recover")
    secret = auth.begin_mfa_enrollment(user["id"])["secret"]
    codes = auth.confirm_mfa_enrollment(user["id"], auth.totp_now(secret))
    auth.verify_mfa(user["id"], codes[0].upper())
    expect_error(auth.verify_mfa, user["id"], codes[0], contains="already-used")
    assert auth.remaining_recovery_codes(user["id"]) == auth.RECOVERY_CODE_COUNT - 1


def test_lockout_after_failures():
    enrolled("locked@example.com")
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        expect_error(auth.check_password, "locked@example.com", "not the password")
    expect_error(auth.check_password, "locked@example.com", PW, contains="locked")
    auth.unlock("locked@example.com")
    auth.check_password("locked@example.com", PW)


def test_mfa_failures_also_lock():
    user, _, _ = enrolled("mfalock@example.com")
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        expect_error(auth.verify_mfa, user["id"], "123456")
    expect_error(auth.check_password, "mfalock@example.com", PW, contains="locked")


def test_sessions_expire_and_revoke():
    user, _, _ = enrolled("session@example.com")
    token = auth.issue_session(user["id"], mfa_verified=True)
    assert auth.user_for_token(token)
    assert auth.user_for_token(token + "x") is None
    auth.revoke_session(token)
    assert auth.user_for_token(token) is None

    os.environ["SESSION_HOURS"] = "-1"
    try:
        expired = auth.issue_session(user["id"], mfa_verified=True)
    finally:
        del os.environ["SESSION_HOURS"]
    assert auth.user_for_token(expired) is None


def test_password_change_and_mfa_reset_sign_out():
    user, _, _ = enrolled("change@example.com")
    t1 = auth.issue_session(user["id"], mfa_verified=True)
    auth.change_password(user["id"], PW, PW + " v2")
    assert auth.user_for_token(t1) is None
    t2 = auth.issue_session(user["id"], mfa_verified=True)
    auth.reset_mfa("change@example.com")
    assert auth.user_for_token(t2) is None


def test_admins_always_need_mfa():
    os.environ["REQUIRE_MFA"] = "false"
    try:
        cust = auth.register("relaxed@example.com", PW, "Relaxed")
        assert auth.user_for_token(auth.login("relaxed@example.com", PW))["role"] == "customer"
        auth.set_role("relaxed@example.com", "admin")
        expect_error(auth.login, "relaxed@example.com", PW, contains="MFA setup is required")
        expect_error(auth.issue_session, cust["id"], mfa_verified=False, contains="required")
    finally:
        del os.environ["REQUIRE_MFA"]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"\nAll {len(tests)} auth checks passed.")

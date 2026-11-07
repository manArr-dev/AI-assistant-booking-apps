"""Interactive terminal sign-in: create account, password, MFA enrolment and MFA challenge.

Used by agent.py and auth_cli.py. Returns (user, session_token).
Install the optional `qrcode` package to get a scannable QR code in the terminal.
"""
from __future__ import annotations

import getpass

import auth
from auth import AuthError

MAX_TRIES = 3


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def new_password(email: str) -> str:
    while True:
        pw = getpass.getpass(f"Choose a password ({auth.MIN_PASSWORD_LEN}+ characters): ")
        try:
            auth.validate_password(pw, email)
        except AuthError as e:
            print(f"  {e}")
            continue
        if getpass.getpass("Repeat password: ") == pw:
            return pw
        print("  Passwords didn't match.")


def _show_qr(uri: str) -> None:
    try:
        import qrcode
    except ImportError:
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.print_ascii(invert=True)


def enroll_mfa(user: dict) -> None:
    """Pair an authenticator app and print one-time recovery codes."""
    setup = auth.begin_mfa_enrollment(user["id"])
    print("\nSet up two-factor authentication")
    print("Add this account to an authenticator app (Google Authenticator, Microsoft")
    print("Authenticator, 1Password, Authy...) by scanning the QR code or typing the key.\n")
    _show_qr(setup["uri"])
    key = setup["secret"]
    print("Setup key:", " ".join(key[i:i + 4] for i in range(0, len(key), 4)))
    print("Or open:  ", setup["uri"], "\n")
    for _ in range(MAX_TRIES):
        try:
            codes = auth.confirm_mfa_enrollment(user["id"], _ask("Enter the 6-digit code from the app: "))
            break
        except AuthError as e:
            print(f"  {e}")
    else:
        raise AuthError("Two-factor setup was not completed.")
    print("\nTwo-factor authentication is on. Save these recovery codes somewhere safe.")
    print("Each one works once if you lose your phone:\n")
    for c in codes:
        print("   ", c)
    print()


def _second_factor(user: dict) -> None:
    for _ in range(MAX_TRIES):
        try:
            auth.verify_mfa(user["id"], _ask("Authentication code (or a recovery code): "))
            left = auth.remaining_recovery_codes(user["id"])
            if left < 3:
                print(f"  Only {left} recovery code(s) left. Run: python auth_cli.py recovery-codes")
            return
        except AuthError as e:
            print(f"  {e}")
    raise AuthError("Too many incorrect codes.")


def register_account() -> tuple[str, str]:
    name = _ask("Full name: ")
    email = _ask("Email: ")
    password = new_password(email)
    auth.register(email, password, name)
    print("Account created.")
    return email, password


def sign_in(allow_register: bool = True) -> tuple[dict, str]:
    """Run the full login (and optional sign-up). Raises AuthError if it fails."""
    for _ in range(MAX_TRIES):
        try:
            choice = _ask("[1] Sign in   [2] Create account: ") if allow_register else "1"
            if choice == "2":
                email, password = register_account()
            else:
                email = _ask("Email: ")
                password = getpass.getpass("Password: ")
            user = auth.check_password(email, password)
            break
        except AuthError as e:
            print(f"  {e}")
    else:
        raise AuthError("Sign-in failed.")

    if auth.mfa_required(user):
        if user["mfa_enabled"]:
            _second_factor(user)
        else:
            print("This account must use two-factor authentication.")
            enroll_mfa(user)  # proves possession of the authenticator
        token = auth.issue_session(user["id"], mfa_verified=True)
    else:
        token = auth.issue_session(user["id"], mfa_verified=False)

    print(f"Signed in as {user['full_name']} <{user['email']}> ({user['role']}).\n")
    return user, token

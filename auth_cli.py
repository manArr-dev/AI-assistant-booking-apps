"""
Account management from the terminal.

User commands:
    python auth_cli.py register              create an account (+ MFA setup)
    python auth_cli.py login                 sign in and print a session token for HTTP MCP clients
    python auth_cli.py logout TOKEN          revoke a session token
    python auth_cli.py setup-mfa             turn on / re-pair two-factor authentication
    python auth_cli.py recovery-codes        issue a fresh set of recovery codes
    python auth_cli.py change-password       change password (signs out all sessions)

Operator commands (anyone with access to the database file can already do anything,
so these are for whoever runs the server):
    python auth_cli.py set-role EMAIL admin|customer
    python auth_cli.py reset-mfa EMAIL       user lost their phone and recovery codes
    python auth_cli.py unlock EMAIL          clear a lockout after failed attempts
"""
import argparse
import getpass
import sys

from dotenv import load_dotenv

load_dotenv()

import auth  # noqa: E402
import db  # noqa: E402
import signin  # noqa: E402
from auth import AuthError  # noqa: E402


def _signed_in_user() -> tuple[dict, str]:
    return signin.sign_in(allow_register=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Booking assistant accounts")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("register", "login", "setup-mfa", "recovery-codes", "change-password"):
        sub.add_parser(name)
    sub.add_parser("logout").add_argument("token")
    p = sub.add_parser("set-role")
    p.add_argument("email")
    p.add_argument("role", choices=auth.ROLES)
    sub.add_parser("reset-mfa").add_argument("email")
    sub.add_parser("unlock").add_argument("email")
    args = parser.parse_args()

    db.init_db()
    auth.init()
    try:
        if args.cmd in ("register", "login"):
            if args.cmd == "register":
                email, password = signin.register_account()
                user = auth.check_password(email, password)
                if auth.mfa_required(user):
                    signin.enroll_mfa(user)
                token = auth.issue_session(user["id"], mfa_verified=auth.mfa_required(user))
            else:
                user, token = _signed_in_user()
            hours = auth.session_lifetime().total_seconds() / 3600
            print(f"Session token (valid {hours:g} h). Treat it like a password:\n\n{token}\n")
            print("Use it with the HTTP servers:  Authorization: Bearer <token>")
        elif args.cmd == "logout":
            auth.revoke_session(args.token)
            print("Session revoked.")
        elif args.cmd == "setup-mfa":
            user, token = _signed_in_user()
            signin.enroll_mfa(user)
            auth.revoke_session(token)
        elif args.cmd == "recovery-codes":
            user, token = _signed_in_user()
            if not user["mfa_enabled"]:
                raise AuthError("Turn on MFA first: python auth_cli.py setup-mfa")
            print("New recovery codes (the old ones no longer work):\n")
            for c in auth.regenerate_recovery_codes(user["id"]):
                print("   ", c)
            auth.revoke_session(token)
        elif args.cmd == "change-password":
            user, _ = _signed_in_user()
            current = getpass.getpass("Current password: ")
            auth.change_password(user["id"], current, signin.new_password(user["email"]))
            print("Password changed. All sessions were signed out.")
        elif args.cmd == "set-role":
            auth.set_role(args.email, args.role)
            print(f"{args.email} is now {args.role}. (Admins must use MFA.)")
        elif args.cmd == "reset-mfa":
            auth.reset_mfa(args.email)
            print(f"MFA reset for {args.email}; they will set it up again at next sign-in.")
        elif args.cmd == "unlock":
            auth.unlock(args.email)
            print(f"{args.email} unlocked.")
    except AuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

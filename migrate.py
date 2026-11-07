"""
Database maintenance commands (used by Docker/Kubernetes, also handy locally).

    python migrate.py                 # apply pending schema migrations + seed data, then exit
    python migrate.py --status        # list applied migrations
    python migrate.py --purge-offers  # delete expired, unbooked travel offers (CronJob)

Works against DATABASE_URL (PostgreSQL) or the SQLite file BOOKING_DB.
Safe to run concurrently: migrations take a database lock.
"""
import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()

import database  # noqa: E402
import db  # noqa: E402
from travel import store  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="show applied migrations")
    parser.add_argument("--purge-offers", action="store_true", help="delete expired travel offers")
    args = parser.parse_args()

    engine = "postgresql" if database.is_postgres() else f"sqlite ({database.sqlite_path()})"
    try:
        db.init_db()  # migrations + seed services
        if args.status:
            with database.connect() as conn:
                for r in conn.execute("SELECT version, description, applied_at FROM schema_migrations "
                                      "ORDER BY version"):
                    print(f"{r['version']:>3}  {r['applied_at'][:19]}  {r['description']}")
        if args.purge_offers:
            print(f"purged {store.purge_expired_offers()} expired offers")
        print(f"database ready: {engine}")
        return 0
    finally:
        database.close_pool()


if __name__ == "__main__":
    sys.exit(main())

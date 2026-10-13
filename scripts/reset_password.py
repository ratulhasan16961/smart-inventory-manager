"""Emergency password reset for someone who is locked out. Run it on the computer that holds the database.

    python3 -m scripts.reset_password --user admin
    python3 -m scripts.reset_password --user admin --db demo/demo.db

It asks for the new password (typing is hidden), unlocks the account and prints a new recovery key.
Anyone who can run this already has full access to the database file, which is why it is a command and not a screen.
"""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path

from pos_erp import config
from pos_erp.bootstrap import open_database
from pos_erp.errors import POSError
from pos_erp.services import recovery_service


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Emergency password reset (run on the computer with the database).")
    parser.add_argument("--user", required=True, help="username to reset, for example admin")
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file (default: the app's database)")
    args = parser.parse_args(argv)

    path = Path(args.db)
    if not path.is_file():
        print(f"Database not found: {path}")
        return 1
    print(f"Database: {path.resolve()}")
    print(f"User:     {args.user}")
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Repeat the new password: "):
        print("The two passwords do not match. Nothing was changed.")
        return 1

    conn = open_database(path)
    try:
        key = recovery_service.emergency_reset(conn, args.user, password)
    except POSError as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        conn.close()
    print("Password changed and the account is unlocked.")
    print(f"New recovery key (write it down, it is shown only once): {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

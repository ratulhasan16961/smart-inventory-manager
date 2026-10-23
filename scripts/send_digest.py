"""Send the daily summary email from the command line, so the computer can do it by itself (cron / Task Scheduler).

    python3 -m scripts.send_digest
    python3 -m scripts.send_digest --day 2026-10-22 --db /path/to/general_inventory.db

Needs the email settings (mail.env) and the summary address in Settings > Backup & Reminders.
Anyone who can run this already has the database file, as with scripts.reset_password.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pos_erp import config
from pos_erp.bootstrap import open_database
from pos_erp.errors import POSError
from pos_erp.services import digest_service


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Email the daily summary to the owner.")
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file (default: the app's database)")
    parser.add_argument("--day", default=None, help="day to report, YYYY-MM-DD (default: today)")
    args = parser.parse_args(argv)
    path = Path(args.db)
    if not path.is_file():
        print(f"Database not found: {path}")
        return 1
    conn = open_database(path)
    try:
        address = digest_service.send(conn, digest_service.system_session(conn), day=args.day)
    except POSError as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        conn.close()
    print(f"Daily summary sent to {address}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

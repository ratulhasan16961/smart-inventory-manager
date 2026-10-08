"""Numbered upgrades applied on top of the base schema.

The base schema (PRAGMA user_version = 1) is created or migrated by migrations.py. Every later change is a
numbered upgrade registered in UPGRADES and recorded in the `schema_migrations` table. To change the schema:
write one function, add one line to UPGRADES, add a test. A file backup is taken before any upgrade runs.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable

from .. import timeutil
from .connection import transaction

log = logging.getLogger(__name__)

BASE_VERSION = 1
DEFAULT_CATEGORIES = ("Electronics", "Grocery", "Clothing", "General")
DEFAULT_WAREHOUSES = ("Central Warehouse", "Branch Warehouse")

_LIST_TABLE = """CREATE TABLE {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(name)) > 0)
)"""


def _upgrade_v2(conn: sqlite3.Connection) -> None:
    """Settings table, managed category/warehouse lists, normalised expiry dates ('2027-5' -> '2027-05')."""
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for table, column, defaults in (("categories", "category", DEFAULT_CATEGORIES),
                                    ("warehouses", "warehouse", DEFAULT_WAREHOUSES)):
        conn.execute(_LIST_TABLE.format(table=table))
        conn.executemany(f"INSERT OR IGNORE INTO {table} (name) VALUES (?)", [(name,) for name in defaults])
        # keep every value the existing products already use
        conn.execute(f"INSERT OR IGNORE INTO {table} (name) SELECT DISTINCT trim({column}) FROM products "
                     f"WHERE trim({column}) <> ''")
    conn.execute("UPDATE products SET expiry = substr(expiry, 1, 5) || '0' || substr(expiry, 6) "
                 "WHERE expiry GLOB '[0-9][0-9][0-9][0-9]-[0-9]'")


UPGRADES: dict[int, Callable[[sqlite3.Connection], None]] = {2: _upgrade_v2}
LATEST_VERSION = max(UPGRADES)


def applied_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                 "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    return conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] or BASE_VERSION


def _backup_before_upgrade(conn: sqlite3.Connection, version: int) -> str | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row["file"] if row else ""
    if not path:  # in-memory database
        return None
    dest = f"{path}.pre-upgrade-v{version}-{timeutil.now():%Y%m%d%H%M%S}.bak"
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
    finally:
        target.close()
    return dest


def apply_upgrades(conn: sqlite3.Connection) -> list[int]:
    """Apply every pending upgrade, each in its own transaction. Returns the versions applied."""
    current = applied_version(conn)
    if current > LATEST_VERSION:
        raise RuntimeError(f"Database upgrade v{current} is newer than this application (v{LATEST_VERSION}).")
    pending = [version for version in sorted(UPGRADES) if version > current]
    if pending:
        log.info("Applying upgrades %s (backup: %s)", pending, _backup_before_upgrade(conn, pending[0]))
    for version in pending:
        with transaction(conn):
            UPGRADES[version](conn)
            conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                         (version, timeutil.stamp()))
    return pending

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
        conn.execute(f"INSERT OR IGNORE INTO {table} (name) SELECT DISTINCT trim({column}) FROM products "
                     f"WHERE trim({column}) <> ''")
    conn.execute("UPDATE products SET expiry = substr(expiry, 1, 5) || '0' || substr(expiry, 6) "
                 "WHERE expiry GLOB '[0-9][0-9][0-9][0-9]-[0-9]'")


def _upgrade_v3(conn: sqlite3.Connection) -> None:
    """Cash handling (amount received / change due) and voided invoices."""
    conn.execute("ALTER TABLE invoices ADD COLUMN tendered_cents INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE invoices ADD COLUMN change_cents INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE invoices ADD COLUMN status TEXT NOT NULL DEFAULT 'Completed' "
                 "CHECK (status IN ('Completed', 'Voided'))")
    conn.execute("ALTER TABLE invoices ADD COLUMN voided_at TEXT")
    conn.execute("ALTER TABLE invoices ADD COLUMN voided_by TEXT")
    conn.execute("ALTER TABLE invoices ADD COLUMN void_reason TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE invoices SET tendered_cents = total_cents")
    conn.execute("CREATE INDEX idx_invoices_status ON invoices (status)")


def _upgrade_v4(conn: sqlite3.Connection) -> None:
    """Loyalty points redeemed at the till (kept per invoice so a void can give them back)."""
    conn.execute("ALTER TABLE invoices ADD COLUMN points_redeemed INTEGER NOT NULL DEFAULT 0 "
                 "CHECK (points_redeemed >= 0)")
    conn.execute("ALTER TABLE invoices ADD COLUMN points_value_cents INTEGER NOT NULL DEFAULT 0 "
                 "CHECK (points_value_cents >= 0)")
    conn.execute("CREATE INDEX idx_invoices_phone ON invoices (customer_phone)")


def _upgrade_v5(conn: sqlite3.Connection) -> None:
    """Password recovery keys: one per user, stored hashed, with their own wrong-attempt counter."""
    conn.execute("ALTER TABLE users ADD COLUMN recovery_hash TEXT")
    conn.execute("ALTER TABLE users ADD COLUMN recovery_attempts INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE users ADD COLUMN recovery_locked_until TEXT")


def _upgrade_v6(conn: sqlite3.Connection) -> None:
    """Verified account email (unique) and one-time email codes (verification / password reset), stored hashed."""
    conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.execute("CREATE UNIQUE INDEX idx_users_email ON users (email COLLATE NOCASE) WHERE email IS NOT NULL")
    conn.execute("""CREATE TABLE email_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users (id),
        purpose TEXT NOT NULL CHECK (purpose IN ('verify', 'reset')),
        email TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0, 1))
    )""")
    conn.execute("CREATE INDEX idx_email_codes_user ON email_codes (user_id, purpose, id)")


def _upgrade_v7(conn: sqlite3.Connection) -> None:
    """Customer credit (baki): a per-customer credit limit and a ledger of charges, payments and credit notes."""
    conn.execute("ALTER TABLE customers ADD COLUMN credit_limit_cents INTEGER NOT NULL DEFAULT 0 "
                 "CHECK (credit_limit_cents >= 0)")
    conn.execute("""CREATE TABLE customer_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL REFERENCES customers (id),
        entry_type TEXT NOT NULL CHECK (entry_type IN ('CHARGE', 'PAYMENT', 'CREDIT_NOTE', 'ADJUSTMENT')),
        amount_cents INTEGER NOT NULL CHECK (amount_cents <> 0),
        invoice_id INTEGER REFERENCES invoices (id),
        method TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        username TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX idx_ledger_customer ON customer_ledger (customer_id, id)")
    conn.execute("CREATE INDEX idx_ledger_created ON customer_ledger (created_at)")


def _upgrade_v8(conn: sqlite3.Connection) -> None:
    """Daily expenses and register shifts (opening float, counted drawer, stored summary)."""
    conn.execute("""CREATE TABLE expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_date TEXT NOT NULL,
        category TEXT NOT NULL CHECK (length(trim(category)) > 0),
        amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
        note TEXT NOT NULL DEFAULT '',
        paid_from TEXT NOT NULL DEFAULT 'Cash' CHECK (paid_from IN ('Cash', 'Other')),
        username TEXT NOT NULL,
        created_at TEXT NOT NULL,
        is_void INTEGER NOT NULL DEFAULT 0 CHECK (is_void IN (0, 1)),
        void_reason TEXT NOT NULL DEFAULT '',
        voided_by TEXT
    )""")
    conn.execute("CREATE INDEX idx_expenses_date ON expenses (expense_date)")
    conn.execute("""CREATE TABLE shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open', 'Closed')),
        opened_by TEXT NOT NULL,
        opened_at TEXT NOT NULL,
        opening_cash_cents INTEGER NOT NULL CHECK (opening_cash_cents >= 0),
        closed_by TEXT,
        closed_at TEXT,
        counted_cash_cents INTEGER,
        expected_cash_cents INTEGER,
        variance_cents INTEGER,
        note TEXT NOT NULL DEFAULT '',
        summary_json TEXT
    )""")
    conn.execute("CREATE UNIQUE INDEX idx_shifts_single_open ON shifts (status) WHERE status = 'Open'")


def _upgrade_v9(conn: sqlite3.Connection) -> None:
    """Manager role. SQLite cannot change a CHECK constraint in place, so the users table is rebuilt (every column,
    row and id is copied). The only table that points at users (email_codes) is emptied while the old table is
    dropped and refilled afterwards, because SQLite counts the dropped parent rows as foreign-key violations."""
    code_columns = "id, user_id, purpose, email, code_hash, created_at, expires_at, attempts, used"
    conn.execute(f"CREATE TEMP TABLE _saved_email_codes AS SELECT {code_columns} FROM email_codes")
    conn.execute("DELETE FROM email_codes")
    conn.execute("""CREATE TABLE users_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('Admin', 'Manager', 'Cashier')),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT,
        recovery_hash TEXT,
        recovery_attempts INTEGER NOT NULL DEFAULT 0,
        recovery_locked_until TEXT,
        email TEXT
    )""")
    columns = ("id, username, password_hash, role, is_active, must_change_password, failed_attempts, locked_until, "
               "recovery_hash, recovery_attempts, recovery_locked_until, email")
    conn.execute(f"INSERT INTO users_new ({columns}) SELECT {columns} FROM users")
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")
    conn.execute("CREATE UNIQUE INDEX idx_users_email ON users (email COLLATE NOCASE) WHERE email IS NOT NULL")
    conn.execute(f"INSERT INTO email_codes ({code_columns}) SELECT {code_columns} FROM _saved_email_codes")
    conn.execute("DROP TABLE _saved_email_codes")


def _upgrade_v10(conn: sqlite3.Connection) -> None:
    """Payment reference (bKash / Nagad / card transaction ID) on invoices."""
    conn.execute("ALTER TABLE invoices ADD COLUMN payment_ref TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX idx_invoices_payment_ref ON invoices (payment_ref)")


UPGRADES: dict[int, Callable[[sqlite3.Connection], None]] = {
    2: _upgrade_v2, 3: _upgrade_v3, 4: _upgrade_v4, 5: _upgrade_v5, 6: _upgrade_v6, 7: _upgrade_v7,
    8: _upgrade_v8, 9: _upgrade_v9, 10: _upgrade_v10}
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

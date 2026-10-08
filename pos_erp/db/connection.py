"""SQLite connection helpers: safe pragmas and a nestable transaction context."""
from __future__ import annotations

import itertools
import sqlite3
from contextlib import contextmanager

from .. import config

_savepoint_ids = itertools.count(1)


def connect(path=None) -> sqlite3.Connection:
    """Open the database. Transactions are managed explicitly via ``transaction()``."""
    target = str(path if path is not None else config.DB_PATH)
    conn = sqlite3.connect(target, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """All-or-nothing block. Nested use becomes a SAVEPOINT, so services can call each other."""
    if conn.in_transaction:
        name = f"sp_{next(_savepoint_ids)}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

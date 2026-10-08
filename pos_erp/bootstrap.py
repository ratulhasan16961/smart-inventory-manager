"""Open the database and make sure it is ready to use."""
from __future__ import annotations

import sqlite3

from .db import connect, ensure_schema
from .db.upgrades import apply_upgrades
from .services import lists_service, settings_service


def open_database(path=None) -> sqlite3.Connection:
    """Connect, bring the schema up to date and publish DB-backed settings (currency, lists).

    No accounts are created here: on a brand-new database the login screen runs the first-run setup."""
    conn = connect(path)
    ensure_schema(conn)
    apply_upgrades(conn)
    settings_service.apply(conn)
    lists_service.apply(conn)
    return conn
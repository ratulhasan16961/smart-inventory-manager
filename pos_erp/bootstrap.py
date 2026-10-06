"""Open the database and make sure it is ready to use."""
from __future__ import annotations

import sqlite3

from .db import connect, ensure_schema
from .services.auth_service import ensure_default_users


def open_database(path=None) -> sqlite3.Connection:
    conn = connect(path)
    ensure_schema(conn)
    ensure_default_users(conn)
    return conn
"""Append-only audit trail."""
from __future__ import annotations

import sqlite3

from .. import timeutil
from ..permissions import Session


def log(conn: sqlite3.Connection, username: str, action: str) -> None:
    conn.execute("INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
                 (username, action, timeutil.stamp()))


def recent(conn: sqlite3.Connection, session: Session, limit: int = 1000) -> list[sqlite3.Row]:
    session.require("audit.view")
    return conn.execute("SELECT id, username, action, timestamp FROM audit_logs ORDER BY id DESC LIMIT ?",
                        (limit,)).fetchall()
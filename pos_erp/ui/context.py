"""State shared by every window: database connection, logged-in session, refresh callback."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from ..permissions import Session


@dataclass
class AppContext:
    root: Any
    conn: sqlite3.Connection
    session: Session
    refresh: Callable[[], None] = field(default=lambda: None)

    def can(self, permission: str) -> bool:
        return self.session.can(permission)

    def set_session(self, session: Session) -> None:
        self.session = session
        self.root.title(f"Enterprise POS & ERP System - [{session.username} ({session.role})]")
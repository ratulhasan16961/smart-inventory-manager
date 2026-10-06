"""Consistent SQLite backups (uses SQLite's online backup API, not a raw file copy)."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .. import timeutil
from ..permissions import Session
from . import audit

log = logging.getLogger(__name__)


def _write_backup(conn: sqlite3.Connection, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(dest))
    try:
        conn.backup(target)
    finally:
        target.close()
    return dest


def create_backup(conn: sqlite3.Connection, session: Session, dest: str | Path) -> Path:
    session.require("system.backup")
    path = _write_backup(conn, Path(dest))
    audit.log(conn, session.username, f"Database backup created: {path.name}")
    return path


def auto_backup(conn: sqlite3.Connection, backup_dir: str | Path, keep: int = 20) -> Path:
    """Timestamped backup (used on shutdown). Keeps only the newest ``keep`` files."""
    folder = Path(backup_dir)
    dest = _write_backup(conn, folder / f"backup_{timeutil.now():%Y%m%d_%H%M%S}.db")
    prune(folder, keep)
    return dest


def prune(folder: Path, keep: int) -> None:
    backups = sorted(folder.glob("backup_*.db"), key=lambda p: p.name, reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            log.warning("Could not delete old backup %s", old)
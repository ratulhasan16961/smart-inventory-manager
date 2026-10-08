"""Consistent SQLite backups and restores (SQLite's online backup API, not a raw file copy)."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import timeutil
from ..db.migrations import ensure_schema
from ..db.schema import SCHEMA_VERSION
from ..db.upgrades import apply_upgrades
from ..errors import ValidationError
from ..permissions import Session
from . import audit, stock_service

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


def list_backups(folder: str | Path) -> list[Path]:
    """Database files in a folder, newest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix in {".db", ".bak"}]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


# --------------------------------------------------------------------------- health
@dataclass(frozen=True)
class HealthReport:
    integrity: str
    foreign_key_problems: int
    ledger_problems: int

    @property
    def ok(self) -> bool:
        return self.integrity == "ok" and self.foreign_key_problems == 0 and self.ledger_problems == 0


def health_check(conn: sqlite3.Connection, session: Session) -> HealthReport:
    """File integrity, broken foreign keys, and stock vs ledger."""
    session.require("system.backup")
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    integrity = "ok" if len(rows) == 1 and rows[0][0] == "ok" else "; ".join(str(r[0]) for r in rows[:5])
    foreign = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    return HealthReport(integrity, foreign, len(stock_service.reconcile(conn, session)))


# --------------------------------------------------------------------------- restore
def inspect_backup(path: str | Path) -> None:
    """Raise ValidationError unless the file is a healthy POS database this version can read."""
    path = Path(path)
    if not path.is_file():
        raise ValidationError("Backup file not found!")
    try:
        source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        raise ValidationError("That file is not a valid database backup.") from None
    try:
        verdict = source.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        version = source.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError:
        raise ValidationError("That file is not a valid database backup.") from None
    finally:
        source.close()
    if verdict != "ok":
        raise ValidationError("The backup failed its integrity check, so it was not restored.")
    if "users" not in tables or not ({"products", "inventory"} & tables):
        raise ValidationError("That file does not look like a POS database backup.")
    if version > SCHEMA_VERSION:
        raise ValidationError("This backup was made by a newer version of the app.")


def restore_backup(conn: sqlite3.Connection, session: Session, path: str | Path, safety_dir: str | Path) -> Path:
    """Replace the live database with a backup. Returns the safety copy of what was there before.

    Older formats (including pre-migration copies) are upgraded right after the restore."""
    session.require("system.restore")
    source_path = Path(path)
    inspect_backup(source_path)
    if conn.in_transaction:
        raise RuntimeError("Cannot restore while a transaction is open")
    safety = _write_backup(conn, Path(safety_dir) / f"pre_restore_{timeutil.now():%Y%m%d_%H%M%S}.db")
    source = sqlite3.connect(str(source_path))
    try:
        source.backup(conn)
    finally:
        source.close()
    ensure_schema(conn)
    apply_upgrades(conn)
    audit.log(conn, session.username, f"Database restored from {source_path.name} (safety copy: {safety.name})")
    return safety

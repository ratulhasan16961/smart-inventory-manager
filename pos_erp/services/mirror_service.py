"""A second copy of every automatic backup in a folder you choose (a Google Drive / OneDrive / Dropbox sync
folder, a USB drive or a network share), so a dead computer does not mean lost data."""
from __future__ import annotations

import logging
import shutil
import sqlite3
import uuid
from pathlib import Path

from .. import timeutil
from ..errors import ValidationError
from ..permissions import Session
from . import audit

log = logging.getLogger(__name__)


def _setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _remember(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def configured_folder(conn: sqlite3.Connection) -> Path | None:
    text = _setting(conn, "backup_mirror_dir").strip()
    return Path(text).expanduser() if text else None


def _keep(conn: sqlite3.Connection) -> int:
    try:
        return max(1, int(_setting(conn, "backup_mirror_keep", "30") or 30))
    except ValueError:
        return 30


def check_folder(path) -> None:
    """Raise ValidationError unless files can be created in the folder."""
    folder = Path(path).expanduser()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".pos_write_test_{uuid.uuid4().hex}"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        raise ValidationError(f"Cannot write to that folder: {exc}") from None


def prune(folder: Path, keep: int) -> None:
    backups = sorted(folder.glob("backup_*.db"), key=lambda p: p.name, reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            log.warning("Could not delete old backup copy %s", old)


def status(conn: sqlite3.Connection) -> str:
    return _setting(conn, "backup_mirror_status") or "No copy has been made yet."


def copy_to_mirror(conn: sqlite3.Connection, backup_file: Path) -> Path | None:
    """Called after every automatic backup. Never raises: a failed copy must not stop the app from closing."""
    folder = configured_folder(conn)
    if folder is None:
        return None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / Path(backup_file).name
        shutil.copy2(backup_file, target)
        prune(folder, _keep(conn))
        _remember(conn, "backup_mirror_status", f"OK {timeutil.stamp()} - {target.name}")
        return target
    except Exception as exc:
        log.exception("Backup copy to %s failed", folder)
        try:
            _remember(conn, "backup_mirror_status", f"FAILED {timeutil.stamp()} - {exc}")
        except Exception:
            pass
        return None


def backup_now(conn: sqlite3.Connection, session: Session) -> Path:
    """A fresh backup written straight into the copy folder."""
    session.require("system.backup")
    folder = configured_folder(conn)
    if folder is None:
        raise ValidationError("Choose the backup copy folder first.")
    check_folder(folder)
    dest = folder / f"backup_{timeutil.now():%Y%m%d_%H%M%S}.db"
    target = sqlite3.connect(str(dest))
    try:
        conn.backup(target)
    finally:
        target.close()
    prune(folder, _keep(conn))
    _remember(conn, "backup_mirror_status", f"OK {timeutil.stamp()} - {dest.name}")
    audit.log(conn, session.username, f"Backup copy written to {folder}")
    return dest

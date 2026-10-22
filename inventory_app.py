"""Launcher: python3 inventory_app.py. It is also the entry point of the packaged .exe / .app."""
import os
import runpy
import sys
from pathlib import Path


def _prepare_frozen_environment() -> None:
    """A packaged app must not write next to itself (the bundle is read-only or temporary): use a per-user folder.

    The folders are passed to the app through the same environment variables a developer can set by hand, so the
    database is created automatically on first run in a place that survives updates.
    """
    if not getattr(sys, "frozen", False):
        return
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "EnterprisePOS"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "EnterprisePOS"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "EnterprisePOS"
    base = Path(os.environ.get("POS_DATA_DIR", str(base)))
    for folder in (base, base / "backups", base / "invoices", base / "logs"):
        folder.mkdir(parents=True, exist_ok=True)
    defaults = {"POS_DB_PATH": base / "general_inventory.db", "POS_BACKUP_DIR": base / "backups",
                "POS_INVOICE_DIR": base / "invoices", "POS_LOG_DIR": base / "logs", "POS_MAIL_ENV": base / "mail.env"}
    for key, path in defaults.items():
        os.environ.setdefault(key, str(path))


if __name__ == "__main__":
    _prepare_frozen_environment()
    runpy.run_module("pos_erp", run_name="__main__", alter_sys=True)

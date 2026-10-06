"""Application entry point: logging, database, ONE Tk root (login view, then main window)."""
from __future__ import annotations

import logging
import tkinter as tk
from logging.handlers import RotatingFileHandler
from tkinter import messagebox

from .. import config
from ..bootstrap import open_database
from ..services import backup_service
from .login import LoginView
from .main_window import MainWindow
from .widgets import BG, apply_theme

log = logging.getLogger("pos_erp")


def _configure_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(config.LOG_DIR / "pos_erp.log", maxBytes=500_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def run(db_path=None) -> None:
    _configure_logging()
    try:
        conn = open_database(db_path)
    except Exception as exc:
        log.exception("Could not open the database")
        splash = tk.Tk()
        splash.withdraw()
        messagebox.showerror(
            "Database error",
            f"Could not open the database:\n{exc}\n\nDetails are in logs/pos_erp.log. If a migration was "
            "attempted, a *.pre-migration-*.bak copy of your data sits next to the database file.")
        splash.destroy()
        return

    root = tk.Tk()
    root.title("POS System Login")
    root.geometry("380x400")
    root.config(bg=BG)
    root.eval("tk::PlaceWindow . center")
    apply_theme(root)

    def report_error(exc_type, exc, tb) -> None:
        log.error("Unhandled error", exc_info=(exc_type, exc, tb))
        messagebox.showerror("Unexpected error",
                             f"Something went wrong:\n{exc}\n\nDetails were written to logs/pos_erp.log")

    root.report_callback_exception = report_error

    def start_main(session) -> None:
        root.geometry("1380x900")
        root.minsize(1100, 700)
        MainWindow(root, conn, session)

    def on_close() -> None:
        try:
            backup_service.auto_backup(conn, config.BACKUP_DIR, config.BACKUPS_TO_KEEP)
        except Exception:
            log.exception("Automatic backup failed")
        conn.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    LoginView(root, conn, start_main)
    root.mainloop()
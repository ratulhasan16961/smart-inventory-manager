"""Shared Tk widgets, theme constants and error handling for the UI."""
from __future__ import annotations

import functools
import logging
import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from ..errors import PermissionDenied, POSError

log = logging.getLogger(__name__)

FONT = "Helvetica"
BG = "#f4f6f9"
WHITE = "#ffffff"
DARK = "#212529"
DISABLED_BG = "#ced4da"
DISABLED_FG = "#6c757d"
LABEL_STYLE = {"bg": WHITE, "fg": DARK, "font": (FONT, 9, "bold")}


def apply_theme(root) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Treeview.Heading", font=(FONT, 10, "bold"), background=DARK, foreground="white")
    style.configure("Treeview", font=(FONT, 10), rowheight=26, background=WHITE, fieldbackground=WHITE,
                    foreground="#000000")
    style.map("Treeview", background=[("selected", "#0d6efd")], foreground=[("selected", "white")])
    style.configure("TCombobox", fieldbackground=WHITE, background=WHITE, foreground="#000000")
    style.configure("TNotebook.Tab", font=(FONT, 10, "bold"), padding=[16, 6])


class CanvasButton(tk.Canvas):
    """Flat coloured button. ``set_enabled(False)`` greys it out (used for role-based permissions)."""

    def __init__(self, parent, text, command=None, bg_color="#0d6efd", fg_color="#ffffff",
                 width=110, height=30, **kwargs):
        super().__init__(parent, width=width, height=height, bg=parent["bg"], highlightthickness=0, **kwargs)
        self.command = command
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.enabled = True
        self.rect = self.create_rectangle(2, 2, width - 2, height - 2, fill=bg_color, outline=bg_color)
        self.text_id = self.create_text(width // 2, height // 2, text=text, fill=fg_color,
                                        font=(FONT, 9, "bold"))
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda _e: self.config(cursor="hand2" if self.enabled else ""))
        self.bind("<Leave>", lambda _e: self.config(cursor=""))

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        fill = self.bg_color if enabled else DISABLED_BG
        self.itemconfig(self.rect, fill=fill, outline=fill)
        self.itemconfig(self.text_id, fill=self.fg_color if enabled else DISABLED_FG)

    def _on_click(self, _event) -> None:
        if self.enabled and self.command:
            self.command()


def create_styled_entry(parent, width=15, show=None):
    """Entry with a thin 1px grey border. Returns (container, entry); grid/pack the container."""
    container = tk.Frame(parent, bg="#adb5bd", bd=0, highlightthickness=0)
    entry = tk.Entry(container, width=width, bg=WHITE, fg="#000000", insertbackground="#000000",
                     relief="flat", bd=0, highlightthickness=0, font=(FONT, 10), show=show)
    entry.pack(padx=1, pady=1, ipady=3, fill="both", expand=True)
    return container, entry


def make_tree(parent, columns, widths=None, height=None):
    """Treeview with a scrollbar. Returns (container, tree); pack the container."""
    container = tk.Frame(parent, bg=WHITE)
    options = {"height": height} if height else {}
    tree = ttk.Treeview(container, columns=columns, show="headings", selectmode="browse", **options)
    for index, column in enumerate(columns):
        tree.heading(column, text=column)
        tree.column(column, width=widths[index] if widths else 120, anchor="center")
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    tree.pack(fill="both", expand=True)
    return container, tree


def fill_tree(tree, rows) -> None:
    for item in tree.get_children():
        tree.delete(item)
    for row in rows:
        tree.insert("", "end", values=row)


def handle_errors(func):
    """Turn expected errors into friendly dialogs; anything unexpected goes to the global handler."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except PermissionDenied as exc:
            messagebox.showerror("Access Denied", str(exc))
        except POSError as exc:
            messagebox.showerror("Error", str(exc))
        except sqlite3.Error as exc:
            log.exception("Database error in %s", func.__name__)
            messagebox.showerror("Database Error", f"The database reported a problem:\n{exc}")
        except OSError as exc:
            log.exception("File error in %s", func.__name__)
            messagebox.showerror("File Error", f"Could not read or write the file:\n{exc}")
        return None
    return wrapper
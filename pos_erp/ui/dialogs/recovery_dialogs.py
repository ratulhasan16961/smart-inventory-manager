"""Settings > Recovery Key: see whether a key is set and generate a new one."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ...services import recovery_service
from ..context import AppContext
from ..widgets import DARK, FONT, WHITE, CanvasButton, handle_errors


class RecoveryKeyDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Recovery Key")
        self.win.geometry("480x370")
        self.win.config(bg=WHITE)
        tk.Label(self.win, text="Recovery Key", bg=WHITE, fg=DARK, font=(FONT, 14, "bold")).pack(pady=(16, 4))
        self.status = tk.Label(self.win, text="", bg=WHITE, fg=DARK, font=(FONT, 10, "bold"))
        self.status.pack()
        tk.Label(self.win, text=("If you forget your password, the recovery key lets you set a new one from the login "
                                 "screen (Forgot password?). It is shown only once and works only once. Generating "
                                 "a new key makes the old one stop working."),
                 bg=WHITE, fg=DARK, font=(FONT, 9), wraplength=430, justify="left").pack(padx=20, pady=10)
        self.key_entry = tk.Entry(self.win, font=("Courier", 15, "bold"), justify="center", width=21,
                                  relief="solid", state="readonly", readonlybackground=WHITE)
        self.key_entry.pack(pady=10, ipady=6)
        buttons = tk.Frame(self.win, bg=WHITE)
        buttons.pack(pady=8)
        CanvasButton(buttons, "Generate new key", self.generate, "#d63384", width=160, height=32).pack(
            side="left", padx=5)
        CanvasButton(buttons, "Copy", self.copy, "#6c757d", width=90, height=32).pack(side="left", padx=5)
        self._refresh_status()

    @handle_errors
    def _refresh_status(self) -> None:
        has = recovery_service.has_key(self.ctx.conn, self.ctx.session)
        self.status.config(text="A recovery key is set." if has else "No recovery key is set yet.",
                           fg="#198754" if has else "#dc3545")

    @handle_errors
    def generate(self) -> None:
        question = ("This replaces your current recovery key. Continue?"
                    if recovery_service.has_key(self.ctx.conn, self.ctx.session)
                    else "Generate a recovery key now?")
        if not messagebox.askyesno("Recovery key", question):
            return
        key = recovery_service.issue_key_for_session(self.ctx.conn, self.ctx.session)
        self.key_entry.config(state="normal")
        self.key_entry.delete(0, tk.END)
        self.key_entry.insert(0, key)
        self.key_entry.config(state="readonly")
        self._refresh_status()

    def copy(self) -> None:
        key = self.key_entry.get()
        if not key:
            messagebox.showwarning("Nothing to copy", "Generate a key first.")
            return
        self.ctx.root.clipboard_clear()
        self.ctx.root.clipboard_append(key)
        messagebox.showinfo("Copied", "The recovery key was copied to the clipboard.")


def open_recovery_key(ctx: AppContext) -> RecoveryKeyDialog:
    return RecoveryKeyDialog(ctx)

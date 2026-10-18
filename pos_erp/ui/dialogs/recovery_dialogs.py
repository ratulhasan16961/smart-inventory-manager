"""Settings > Recovery Key: see whether a key is set and generate a new one."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ...services import recovery_service
from ..context import AppContext
from ..widgets import DARK, FONT, WHITE, CanvasButton, handle_errors

CONFIRM_SECONDS = 6
GENERATE_TEXT = "Generate new key"
CONFIRM_TEXT = "Click again to replace"


class RecoveryKeyDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._armed = False
        self._timer = None
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Recovery Key")
        self.win.geometry("480x400")
        self.win.config(bg=WHITE)
        tk.Label(self.win, text="Recovery Key", bg=WHITE, fg=DARK, font=(FONT, 14, "bold")).pack(pady=(16, 4))
        self.status = tk.Label(self.win, text="", bg=WHITE, fg=DARK, font=(FONT, 10, "bold"), wraplength=430)
        self.status.pack()
        tk.Label(self.win, text=("If you forget your password, the recovery key lets you set a new one from the login "
                                 "screen (Forgot password?). It is shown only once and works only once. Generating "
                                 "a new key makes the old one stop working."),
                 bg=WHITE, fg=DARK, font=(FONT, 9), wraplength=430, justify="left").pack(padx=20, pady=10)
        self.key_entry = tk.Entry(self.win, font=("Courier", 15, "bold"), justify="center", width=21, relief="flat",
                                  bd=0, highlightthickness=1, highlightbackground="#adb5bd", state="readonly",
                                  readonlybackground=WHITE)
        self.key_entry.pack(pady=10, ipady=6)
        self.hint = tk.Label(self.win, text="", bg=WHITE, fg="#198754", font=(FONT, 9, "bold"), wraplength=430)
        self.hint.pack()
        buttons = tk.Frame(self.win, bg=WHITE)
        buttons.pack(pady=10)
        self.generate_button = CanvasButton(buttons, GENERATE_TEXT, self.request_generate, "#d63384",
                                            width=170, height=32)
        self.generate_button.pack(side="left", padx=5)
        CanvasButton(buttons, "Copy", self.copy, "#6c757d", width=90, height=32).pack(side="left", padx=5)
        self._refresh_status()

    @handle_errors
    def _refresh_status(self) -> None:
        has = recovery_service.has_key(self.ctx.conn, self.ctx.session)
        self.status.config(text="A recovery key is set." if has else "No recovery key is set yet.",
                           fg="#198754" if has else "#dc3545")

    def request_generate(self) -> None:
        """Button handler. A first key is created at once; replacing an existing key needs a second click."""
        if self._armed or not self._has_key():
            self._disarm()
            self.generate()
            return
        self._armed = True
        self.generate_button.itemconfig(self.generate_button.text_id, text=CONFIRM_TEXT)
        self.status.config(text=f"This replaces your current key. Click the button again within {CONFIRM_SECONDS} "
                                "seconds to confirm.", fg="#dc3545")
        self._timer = self.win.after(CONFIRM_SECONDS * 1000, self._disarm)

    @handle_errors
    def _has_key(self) -> bool:
        return recovery_service.has_key(self.ctx.conn, self.ctx.session)

    def _disarm(self) -> None:
        self._armed = False
        if self._timer is not None:
            try:
                self.win.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None
        try:
            self.generate_button.itemconfig(self.generate_button.text_id, text=GENERATE_TEXT)
        except tk.TclError:
            return
        self._refresh_status()

    @handle_errors
    def generate(self) -> None:
        key = recovery_service.issue_key_for_session(self.ctx.conn, self.ctx.session)
        self.key_entry.config(state="normal")
        self.key_entry.delete(0, tk.END)
        self.key_entry.insert(0, key)
        self.key_entry.config(state="readonly")
        self.hint.config(text="Write this key down now. It will not be shown again.")
        self._refresh_status()

    def copy(self) -> None:
        key = self.key_entry.get()
        if not key:
            messagebox.showwarning("Nothing to copy", "Generate a key first.", parent=self.win)
            return
        self.ctx.root.clipboard_clear()
        self.ctx.root.clipboard_append(key)
        messagebox.showinfo("Copied", "The recovery key was copied to the clipboard.", parent=self.win)


def open_recovery_key(ctx: AppContext) -> RecoveryKeyDialog:
    return RecoveryKeyDialog(ctx)

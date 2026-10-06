"""Login screen, including the forced password change for temporary passwords."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ..errors import AuthenticationError, POSError
from ..services import auth_service
from .widgets import BG, DARK, FONT, CanvasButton, create_styled_entry


class LoginView:
    def __init__(self, root, conn, on_success) -> None:
        self.root = root
        self.conn = conn
        self.on_success = on_success
        self.frame = tk.Frame(root, bg=BG)
        self.frame.pack(fill="both", expand=True)
        self._show_login()

    def _reset(self, title: str) -> None:
        for child in self.frame.winfo_children():
            child.destroy()
        tk.Label(self.frame, text=title, bg=BG, fg=DARK, font=(FONT, 14, "bold")).pack(pady=20)

    def _entry(self, caption: str, show=None):
        tk.Label(self.frame, text=caption, bg=BG, fg=DARK, font=(FONT, 9, "bold")).pack()
        container, entry = create_styled_entry(self.frame, width=20, show=show)
        container.pack(pady=5)
        return entry

    def _show_login(self) -> None:
        self._reset("Enterprise POS System")
        self.user_entry = self._entry("Username:")
        self.pass_entry = self._entry("Password:", show="*")
        self.pass_entry.bind("<Return>", self.authenticate)
        self.user_entry.bind("<Return>", lambda _e: self.pass_entry.focus_set())
        CanvasButton(self.frame, "Login", self.authenticate, width=120, height=32).pack(pady=20)
        self.user_entry.focus_set()

    def authenticate(self, _event=None) -> None:
        try:
            session = auth_service.authenticate(self.conn, self.user_entry.get(), self.pass_entry.get())
        except AuthenticationError as exc:
            messagebox.showerror("Login Failed", str(exc))
            return
        if session.must_change_password:
            self._show_change_password(session)
        else:
            self._finish(session)

    def _show_change_password(self, session) -> None:
        self._reset("Choose a new password")
        tk.Label(self.frame, text="You are using a temporary password.\nPlease set your own to continue.",
                 bg=BG, fg=DARK, font=(FONT, 9)).pack()
        new_entry = self._entry("New password:", show="*")
        confirm_entry = self._entry("Confirm password:", show="*")

        def save(_event=None) -> None:
            if new_entry.get() != confirm_entry.get():
                messagebox.showerror("Error", "The two passwords do not match!")
                return
            try:
                updated = auth_service.change_password(self.conn, session, new_entry.get())
            except POSError as exc:
                messagebox.showerror("Error", str(exc))
                return
            self._finish(updated)

        confirm_entry.bind("<Return>", save)
        CanvasButton(self.frame, "Save password", save, width=140, height=32).pack(pady=20)
        new_entry.focus_set()

    def _finish(self, session) -> None:
        self.frame.destroy()
        self.on_success(session)
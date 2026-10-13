"""Login screen: first-run setup, normal login, forgot password (recovery key) and the forced password change."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from .. import config
from ..errors import AuthenticationError, POSError
from ..services import auth_service, recovery_service, user_service
from .widgets import BG, DARK, FONT, CanvasButton, create_styled_entry

LINK_COLOR = "#0d6efd"
SIZES = {"login": "380x470", "setup": "380x520", "change": "380x520", "forgot": "380x760", "key": "380x600"}
FORGOT_HELP = ("No recovery key? A cashier can ask an administrator (Settings > Manage Users > Reset Password). "
               "An administrator can run this on the computer that holds the database:\n"
               "python3 -m scripts.reset_password --user NAME")


class LoginView:
    def __init__(self, root, conn, on_success) -> None:
        self.root = root
        self.conn = conn
        self.on_success = on_success
        self._after_key = None
        self.frame = tk.Frame(root, bg=BG)
        self.frame.pack(fill="both", expand=True)
        if user_service.needs_setup(conn):
            self._show_setup()
        else:
            self._show_login()

    # ------------------------------------------------------------------ helpers
    def _reset(self, title: str, size: str) -> None:
        self.root.geometry(SIZES[size])
        for child in self.frame.winfo_children():
            child.destroy()
        tk.Label(self.frame, text=title, bg=BG, fg=DARK, font=(FONT, 14, "bold")).pack(pady=20)

    def _entry(self, caption: str, show=None):
        tk.Label(self.frame, text=caption, bg=BG, fg=DARK, font=(FONT, 9, "bold")).pack()
        container, entry = create_styled_entry(self.frame, width=20, show=show)
        container.pack(pady=5)
        return entry

    def _note(self, text: str, size: int = 9) -> None:
        tk.Label(self.frame, text=text, bg=BG, fg=DARK, font=(FONT, size), wraplength=340,
                 justify="center").pack(padx=10, pady=2)

    def _link(self, text: str, command) -> None:
        label = tk.Label(self.frame, text=text, bg=BG, fg=LINK_COLOR, font=(FONT, 9, "underline"), cursor="hand2")
        label.pack(pady=4)
        label.bind("<Button-1>", lambda _e: command())

    def _typed_username(self) -> str:
        entry = getattr(self, "user_entry", None)
        try:
            return entry.get().strip() if entry is not None else ""
        except tk.TclError:           # the login screen was already replaced
            return ""

    # ------------------------------------------------------------------ first run
    def _show_setup(self) -> None:
        self._reset("Welcome! Create the admin account", "setup")
        self._note("This is the first run.\nChoose the administrator username and password.")
        self.user_entry = self._entry("Username:")
        self.user_entry.insert(0, "admin")
        self.pass_entry = self._entry(f"Password (min {config.MIN_PASSWORD_LENGTH} characters):", show="*")
        self.confirm_entry = self._entry("Confirm password:", show="*")
        self.confirm_entry.bind("<Return>", self.create_admin)
        CanvasButton(self.frame, "Create account", self.create_admin, width=150, height=32).pack(pady=20)
        self.pass_entry.focus_set()

    def create_admin(self, _event=None) -> None:
        if self.pass_entry.get() != self.confirm_entry.get():
            messagebox.showerror("Error", "The two passwords do not match!")
            return
        try:
            session = user_service.create_first_admin(self.conn, self.user_entry.get(), self.pass_entry.get())
            key = recovery_service.issue_key_for_session(self.conn, session)
        except POSError as exc:
            messagebox.showerror("Error", str(exc))
            return
        self._show_recovery_key(key, "Your account is ready", lambda: self._finish(session))

    # ------------------------------------------------------------------ normal login
    def _show_login(self) -> None:
        self._reset("Enterprise POS System", "login")
        self.user_entry = self._entry("Username:")
        self.pass_entry = self._entry("Password:", show="*")
        self.pass_entry.bind("<Return>", self.authenticate)
        self.user_entry.bind("<Return>", lambda _e: self.pass_entry.focus_set())
        CanvasButton(self.frame, "Login", self.authenticate, width=120, height=32).pack(pady=(20, 8))
        self._link("Forgot password?", self.show_forgot)
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
        self._reset("Choose a new password", "change")
        self._note("You are using a temporary password.\nPlease set your own to continue.")
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

    # ------------------------------------------------------------------ forgot password
    def show_forgot(self) -> None:
        prefill = self._typed_username()               # read it BEFORE the login widgets are destroyed
        self._reset("Reset your password", "forgot")
        self._note("Enter your username, the recovery key you saved, and choose a new password.")
        self.forgot_user_e = self._entry("Username:")
        self.forgot_user_e.insert(0, prefill)
        self.forgot_key_e = self._entry("Recovery key (like ABCD-EFGH-JKLM-NPQR):")
        self.forgot_new_e = self._entry(f"New password (min {config.MIN_PASSWORD_LENGTH} characters):", show="*")
        self.forgot_confirm_e = self._entry("Confirm new password:", show="*")
        self.forgot_confirm_e.bind("<Return>", self.reset_with_key)
        CanvasButton(self.frame, "Reset password", self.reset_with_key, width=150, height=32).pack(pady=(16, 6))
        self._link("Back to login", self._show_login)
        self._note(FORGOT_HELP, size=8)
        (self.forgot_key_e if prefill else self.forgot_user_e).focus_set()

    def reset_with_key(self, _event=None) -> None:
        if self.forgot_new_e.get() != self.forgot_confirm_e.get():
            messagebox.showerror("Error", "The two passwords do not match!")
            return
        try:
            new_key = recovery_service.reset_password_with_key(
                self.conn, self.forgot_user_e.get(), self.forgot_key_e.get(), self.forgot_new_e.get())
        except POSError as exc:
            messagebox.showerror("Could not reset the password", str(exc))
            return
        messagebox.showinfo("Password changed", "Your password was changed. Please log in with the new password.")
        self._show_recovery_key(new_key, "Save your NEW recovery key", self._show_login,
                                note="The key you just used no longer works.")

    # ------------------------------------------------------------------ recovery key screen
    def _show_recovery_key(self, key: str, title: str, then, note: str = "") -> None:
        self._after_key = then
        self._reset(title, "key")
        self._note((note + "\n\n" if note else "") +
                   "Write this recovery key down and keep it somewhere safe. If you ever forget your password, it is "
                   "how you get back in from the login screen. It is shown only once and works only once.")
        self.key_entry = tk.Entry(self.frame, font=("Courier", 15, "bold"), justify="center", width=21,
                                  relief="solid", readonlybackground="#ffffff")
        self.key_entry.insert(0, key)
        self.key_entry.config(state="readonly")
        self.key_entry.pack(pady=16, ipady=6)
        CanvasButton(self.frame, "Copy to clipboard", lambda: self._copy_key(key), "#6c757d",
                     width=170, height=30).pack(pady=4)
        CanvasButton(self.frame, "I have saved it", self.continue_after_key, "#198754",
                     width=170, height=34).pack(pady=14)

    def _copy_key(self, key: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(key)
        messagebox.showinfo("Copied", "The recovery key was copied to the clipboard.")

    def continue_after_key(self) -> None:
        callback, self._after_key = self._after_key, None
        if callback is not None:
            callback()

    def _finish(self, session) -> None:
        self.frame.destroy()
        self.on_success(session)

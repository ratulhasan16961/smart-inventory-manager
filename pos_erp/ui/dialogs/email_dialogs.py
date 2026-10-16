"""Settings > Account Email: register an email address and verify it with a code."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ... import mailer
from ...services import email_service
from ..context import AppContext
from ..widgets import DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, handle_errors

MUTED = "#6c757d"


class AccountEmailDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Account Email")
        self.win.geometry("500x540")
        self.win.config(bg=WHITE)
        tk.Label(self.win, text="Account Email", bg=WHITE, fg=DARK, font=(FONT, 14, "bold")).pack(pady=(16, 4))
        self.status = tk.Label(self.win, text="", bg=WHITE, fg=DARK, font=(FONT, 10, "bold"))
        self.status.pack()
        tk.Label(self.win, text=("A verified email lets you reset a forgotten password from the login screen "
                                 "(Forgot password? > Email me a code). We send a 6-digit code to the new address "
                                 "first, so a mistyped address can never receive reset codes."),
                 bg=WHITE, fg=DARK, font=(FONT, 9), wraplength=450, justify="left").pack(padx=20, pady=8)
        if not mailer.is_configured():
            tk.Label(self.win, text=("Email sending is not set up on this computer, so codes cannot be sent yet. "
                                     "See the README (Email setup)."),
                     bg=WHITE, fg="#dc3545", font=(FONT, 9, "bold"), wraplength=450, justify="left").pack(padx=20)
        tk.Label(self.win, text="New email address:", **LABEL_STYLE).pack(pady=(12, 0))
        container, self.email_e = create_styled_entry(self.win, width=34)
        container.pack(pady=4)
        CanvasButton(self.win, "Send verification code", self.send_code, "#0d6efd", width=200, height=32).pack(pady=6)
        tk.Label(self.win, text="6-digit code from the email:", **LABEL_STYLE).pack(pady=(10, 0))
        container, self.code_e = create_styled_entry(self.win, width=14)
        container.pack(pady=4)
        CanvasButton(self.win, "Verify", self.confirm, "#198754", width=120, height=32).pack(pady=6)
        CanvasButton(self.win, "Remove my email", self.remove, "#dc3545", width=160, height=30).pack(pady=(14, 6))
        self._refresh()

    @handle_errors
    def _refresh(self) -> None:
        address = email_service.get_email(self.ctx.conn, self.ctx.session)
        self.status.config(text=f"Verified email: {address}" if address else "No verified email yet.",
                           fg="#198754" if address else MUTED)

    @handle_errors
    def send_code(self) -> None:
        address = self.email_e.get()
        self.status.config(text="Sending...", fg=MUTED)
        self.win.update_idletasks()
        try:
            email_service.request_verification(self.ctx.conn, self.ctx.session, address)
        finally:
            self._refresh()
        messagebox.showinfo("Code sent", f"A 6-digit code was sent to {email_service.validate_email(address)}.\n"
                                         f"Enter it below within {email_service.CODE_TTL_MINUTES} minutes "
                                         "(check the spam folder too).")
        self.code_e.focus_set()

    @handle_errors
    def confirm(self) -> None:
        address = email_service.confirm_verification(self.ctx.conn, self.ctx.session, self.code_e.get())
        self.code_e.delete(0, tk.END)
        self.email_e.delete(0, tk.END)
        self._refresh()
        messagebox.showinfo("Email verified", f"{address} is now your account email.")

    @handle_errors
    def remove(self) -> None:
        if messagebox.askyesno("Remove email", "Remove your email address? You will no longer be able to reset "
                                               "your password by email."):
            email_service.remove_email(self.ctx.conn, self.ctx.session)
            self._refresh()


def open_account_email(ctx: AppContext) -> AccountEmailDialog:
    return AccountEmailDialog(ctx)

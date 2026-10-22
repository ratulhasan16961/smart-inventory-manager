"""Customer credit (due): one customer's ledger, and the list of everyone who owes money."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ... import config
from ...errors import POSError
from ...money import fmt
from ...services import credit_service
from ..context import AppContext
from ..widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree,
                       handle_errors, make_tree)

RED, GREEN, MUTED = "#dc3545", "#198754", "#6c757d"


def ask_values(parent, title, fields, submit_text, on_submit, choices=None):
    """A small form. fields = [(key, label)], choices = {key: [options]}; on_submit(values) may raise POSError."""
    choices = choices or {}
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry(f"380x{150 + 62 * len(fields)}")
    win.config(bg=WHITE)
    widgets = {}
    for key, label in fields:
        tk.Label(win, text=label, **LABEL_STYLE).pack(pady=(10, 0))
        if key in choices:
            widget = ttk.Combobox(win, values=list(choices[key]), state="readonly", width=24)
            widget.set(choices[key][0])
            widget.pack(pady=3)
        else:
            container, widget = create_styled_entry(win, width=26)
            container.pack(pady=3)
        widgets[key] = widget

    def submit(_event=None):
        try:
            on_submit({key: widget.get() for key, widget in widgets.items()})
        except POSError as exc:
            messagebox.showerror("Error", str(exc), parent=win)
            return
        win.destroy()

    CanvasButton(win, submit_text, submit, GREEN, width=150, height=32).pack(pady=16)
    next(iter(widgets.values())).focus_set()
    win.submit = submit
    return win


class CustomerLedgerDialog:
    def __init__(self, ctx: AppContext, phone: str) -> None:
        self.ctx = ctx
        self.phone = phone.strip()
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Customer Ledger")
        self.win.geometry("1000x620")
        self.win.config(bg=WHITE)
        self.title = tk.Label(self.win, text="", bg=WHITE, fg=DARK, font=(FONT, 14, "bold"))
        self.title.pack(pady=(12, 2))
        self.balance = tk.Label(self.win, text="", bg=WHITE, font=(FONT, 13, "bold"))
        self.balance.pack()
        self.limit = tk.Label(self.win, text="", bg=WHITE, fg=MUTED, font=(FONT, 9))
        self.limit.pack()
        bar = tk.Frame(self.win, bg=WHITE)
        bar.pack(pady=8)
        for text, command, color, permission in (
                ("💵 Receive payment", self.receive, GREEN, credit_service.PERM_COLLECT),
                ("✏ Adjust / write off", self.adjust, "#fd7e14", credit_service.PERM_MANAGE),
                ("🔒 Credit limit", self.set_limit, "#6f42c1", credit_service.PERM_MANAGE),
                ("⬇ Export CSV", self.export, "#0d6efd", credit_service.PERM_VIEW)):
            button = CanvasButton(bar, text, command, color, width=170, height=30)
            button.pack(side="left", padx=4)
            button.set_enabled(ctx.can(permission))
        box, self.tree = make_tree(self.win, ("When", "Type", "Amount", "Balance", "Invoice", "Method", "Note", "User"),
                                   (140, 100, 100, 100, 150, 90, 230, 80))
        box.pack(fill="both", expand=True, padx=12, pady=8)
        self.load()

    @handle_errors
    def load(self) -> None:
        account = credit_service.get_account(self.ctx.conn, self.ctx.session, self.phone)
        lines = credit_service.ledger(self.ctx.conn, self.ctx.session, self.phone)
        self.title.config(text=f"{account.name}  ({account.phone})")
        if account.balance_cents > 0:
            self.balance.config(text=f"Owes {fmt(account.balance_cents)}", fg=RED)
        elif account.balance_cents < 0:
            self.balance.config(text=f"Credit {fmt(-account.balance_cents)}", fg=GREEN)
        else:
            self.balance.config(text="Nothing owed", fg=GREEN)
        self.limit.config(text=(f"Credit limit: {fmt(account.credit_limit_cents)}" if account.credit_limit_cents
                                else "No credit limit"))
        fill_tree(self.tree, [(l.when, l.entry_type, fmt(l.amount_cents), fmt(l.balance_cents), l.invoice_no,
                               l.method, l.note, l.username) for l in reversed(lines)])

    def receive(self) -> None:
        def submit(values):
            balance = credit_service.receive_payment(self.ctx.conn, self.ctx.session, self.phone, values["amount"],
                                                     values["method"], values["note"])
            messagebox.showinfo("Payment recorded", f"New balance: {fmt(balance)}", parent=self.win)
            self.load()
        ask_values(self.win, "Receive payment", [("amount", "Amount received"), ("method", "Paid by"),
                                                 ("note", "Note (optional)")], "Record payment", submit,
                   {"method": list(config.PAYMENT_METHODS)})

    def adjust(self) -> None:
        def submit(values):
            credit_service.adjust(self.ctx.conn, self.ctx.session, self.phone, values["amount"], values["reason"])
            self.load()
        ask_values(self.win, "Adjust / write off", [("amount", "Amount (negative reduces the debt)"),
                                                    ("reason", "Reason")], "Save", submit)

    def set_limit(self) -> None:
        def submit(values):
            credit_service.set_credit_limit(self.ctx.conn, self.ctx.session, self.phone, values["limit"])
            self.load()
        ask_values(self.win, "Credit limit", [("limit", "Maximum the customer may owe (0 = no limit)")],
                   "Save", submit)

    @handle_errors
    def export(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv",
                                            initialfile=f"ledger_{self.phone}.csv", filetypes=[("CSV", "*.csv")])
        if path:
            credit_service.export_ledger_csv(self.ctx.conn, self.ctx.session, self.phone, path)
            messagebox.showinfo("Exported", f"Ledger saved to {path}", parent=self.win)


class ReceivablesDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Customer Dues")
        self.win.geometry("780x520")
        self.win.config(bg=WHITE)
        tk.Label(self.win, text="Customers who owe money", bg=WHITE, fg=DARK, font=(FONT, 14, "bold")).pack(pady=(12, 2))
        self.total = tk.Label(self.win, text="", bg=WHITE, fg=RED, font=(FONT, 12, "bold"))
        self.total.pack()
        tk.Label(self.win, text="Double-click a customer to open the ledger.", bg=WHITE, fg=MUTED,
                 font=(FONT, 9)).pack()
        box, self.tree = make_tree(self.win, ("Customer", "Phone", "Owes", "Limit", "Last activity"),
                                   (200, 140, 110, 110, 160))
        box.pack(fill="both", expand=True, padx=12, pady=10)
        self.tree.bind("<Double-1>", self.open_selected)
        self.load()

    @handle_errors
    def load(self) -> None:
        rows = credit_service.list_debtors(self.ctx.conn, self.ctx.session)
        fill_tree(self.tree, [(r["name"], r["phone"], fmt(r["balance_cents"]),
                               fmt(r["credit_limit_cents"]) if r["credit_limit_cents"] else "-", r["last_entry"])
                              for r in rows])
        self.total.config(text=f"Total owed to you: {fmt(sum(r['balance_cents'] for r in rows))}")

    def open_selected(self, _event=None) -> None:
        selected = self.tree.selection()
        if selected:
            open_customer_ledger(self.ctx, str(self.tree.item(selected[0])["values"][1]))


def open_customer_ledger(ctx: AppContext, phone: str):
    phone = (phone or "").strip()
    if not phone:
        messagebox.showwarning("Customer ledger", "Enter the customer's phone number first.", parent=ctx.root)
        return None
    try:
        credit_service.get_account(ctx.conn, ctx.session, phone)
    except POSError as exc:
        messagebox.showerror("Customer ledger", str(exc), parent=ctx.root)
        return None
    return CustomerLedgerDialog(ctx, phone)


def open_receivables(ctx: AppContext) -> ReceivablesDialog:
    return ReceivablesDialog(ctx)

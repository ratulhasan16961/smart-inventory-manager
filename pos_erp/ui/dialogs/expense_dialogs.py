"""Expenses report (administrators): any date range, totals by category, cancel an expense, CSV export."""
from __future__ import annotations

import tkinter as tk
from datetime import timedelta
from tkinter import filedialog, messagebox

from ... import timeutil
from ...money import fmt
from ...services import expense_service
from ..context import AppContext
from ..widgets import DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree, handle_errors, make_tree
from .ledger_dialogs import ask_values


class ExpensesDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Expenses")
        self.win.geometry("940x600")
        self.win.config(bg=WHITE)
        today = timeutil.now().date()
        bar = tk.Frame(self.win, bg=WHITE)
        bar.pack(pady=10)
        tk.Label(bar, text="From:", **LABEL_STYLE).pack(side="left", padx=4)
        container, self.from_e = create_styled_entry(bar, width=12)
        container.pack(side="left")
        self.from_e.insert(0, (today - timedelta(days=6)).isoformat())
        tk.Label(bar, text="To:", **LABEL_STYLE).pack(side="left", padx=4)
        container, self.to_e = create_styled_entry(bar, width=12)
        container.pack(side="left")
        self.to_e.insert(0, today.isoformat())
        for text, command, color in (("Show", self.load, "#0d6efd"), ("Cancel selected", self.cancel, "#dc3545"),
                                     ("Export CSV", self.export, "#198754")):
            CanvasButton(bar, text, command, color, width=120, height=28).pack(side="left", padx=4)
        self.totals = tk.Label(self.win, text="", bg=WHITE, fg=DARK, font=(FONT, 10, "bold"), justify="left")
        self.totals.pack(anchor="w", padx=20)
        box, self.tree = make_tree(self.win, ("ID", "Date", "Category", "Amount", "Paid from", "Note", "By", "Status"),
                                   (50, 90, 120, 90, 80, 250, 80, 90))
        box.pack(fill="both", expand=True, padx=12, pady=10)
        self.load()

    @handle_errors
    def load(self) -> None:
        start, end = self.from_e.get(), self.to_e.get()
        rows = expense_service.list_expenses(self.ctx.conn, self.ctx.session, start, end, include_void=True)
        fill_tree(self.tree, [(r["id"], r["expense_date"], r["category"], fmt(r["amount_cents"]), r["paid_from"],
                               r["note"], r["username"], "CANCELLED" if r["is_void"] else "") for r in rows])
        totals = expense_service.totals_by_category(self.ctx.conn, self.ctx.session, start, end)
        grand = sum(cents for _name, cents in totals)
        self.totals.config(text=f"Total {fmt(grand)}   " + "  |  ".join(f"{name} {fmt(cents)}"
                                                                         for name, cents in totals))

    def cancel(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Cancel expense", "Select an expense first.", parent=self.win)
            return
        expense_id = int(self.tree.item(selected[0])["values"][0])

        def submit(values):
            expense_service.void_expense(self.ctx.conn, self.ctx.session, expense_id, values["reason"])
            self.load()
        ask_values(self.win, "Cancel expense", [("reason", "Reason")], "Cancel expense", submit)

    @handle_errors
    def export(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv", initialfile="expenses.csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            expense_service.export_csv(self.ctx.conn, self.ctx.session, self.from_e.get(), self.to_e.get(), path)
            messagebox.showinfo("Exported", f"Expenses saved to {path}", parent=self.win)


def open_expenses(ctx: AppContext) -> ExpensesDialog:
    return ExpensesDialog(ctx)

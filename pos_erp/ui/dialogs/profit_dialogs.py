"""Net profit window (administrators): any date range, expenses by category, CSV export."""
from __future__ import annotations

import tkinter as tk
from datetime import timedelta
from tkinter import filedialog, messagebox

from ... import timeutil
from ...money import fmt
from ...services import profit_service
from ..context import AppContext
from ..widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree,
                       handle_errors, make_tree)


class ProfitDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.report = None
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Net Profit")
        self.win.geometry("720x560")
        self.win.config(bg=WHITE)
        today = timeutil.now().date()
        bar = tk.Frame(self.win, bg=WHITE)
        bar.pack(pady=12)
        tk.Label(bar, text="From:", **LABEL_STYLE).pack(side="left", padx=4)
        container, self.from_e = create_styled_entry(bar, width=12)
        container.pack(side="left")
        self.from_e.insert(0, (today - timedelta(days=29)).isoformat())
        tk.Label(bar, text="To:", **LABEL_STYLE).pack(side="left", padx=4)
        container, self.to_e = create_styled_entry(bar, width=12)
        container.pack(side="left")
        self.to_e.insert(0, today.isoformat())
        CanvasButton(bar, "Show", self.load, "#0d6efd", width=90, height=28).pack(side="left", padx=6)
        CanvasButton(bar, "Export CSV", self.export, "#198754", width=110, height=28).pack(side="left", padx=4)
        self.summary = tk.Label(self.win, text="", bg=WHITE, fg=DARK, font=("Courier", 12, "bold"), justify="left")
        self.summary.pack(pady=8)
        tk.Label(self.win, text="Expenses by category", bg=WHITE, fg=DARK, font=(FONT, 11, "bold")).pack()
        box, self.tree = make_tree(self.win, ("Category", "Amount"), (300, 150))
        box.pack(fill="both", expand=True, padx=20, pady=10)
        tk.Label(self.win, text="Credit sales count as revenue when sold, not when paid. Gross profit is after "
                                "discounts, returns and the cost of goods.", bg=WHITE, fg="#6c757d",
                 font=(FONT, 8), wraplength=660).pack(pady=(0, 8))
        self.load()

    @handle_errors
    def load(self) -> None:
        report = profit_service.profit_report(self.ctx.conn, self.ctx.session, self.from_e.get(), self.to_e.get())
        self.report = report
        net = report.net_profit_cents
        self.summary.config(
            text=(f"Orders            {report.orders}\nNet revenue       {fmt(report.net_revenue_cents)}\n"
                  f"Gross profit      {fmt(report.gross_profit_cents)}\nExpenses        - {fmt(report.expenses_cents)}\n"
                  f"NET PROFIT        {fmt(net)}"), fg="#198754" if net >= 0 else "#dc3545")
        fill_tree(self.tree, [(name, fmt(cents)) for name, cents in report.expenses_by_category])

    @handle_errors
    def export(self) -> None:
        if self.report is None:
            return
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv", initialfile="net_profit.csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            profit_service.export_csv(self.ctx.session, self.report, path)
            messagebox.showinfo("Exported", f"Saved to {path}", parent=self.win)


def open_profit(ctx: AppContext) -> ProfitDialog:
    return ProfitDialog(ctx)

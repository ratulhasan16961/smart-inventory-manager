"""Register & Shift: open/close a shift, live cash summary, today's expenses, shift history.

RegisterDialog(ctx) opens its own window; RegisterDialog(ctx, parent_frame) fills a tab of the main window."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ...money import fmt
from ...services import (business_service, credit_service, expense_service, print_output, shift_report,
                         shift_service, thermal)
from ..context import AppContext
from ..widgets import DARK, FONT, WHITE, CanvasButton, fill_tree, handle_errors, make_tree
from .expense_dialogs import open_expenses
from .ledger_dialogs import ask_values, open_receivables

GREEN, RED, MUTED = "#198754", "#dc3545", "#6c757d"


class RegisterDialog:
    def __init__(self, ctx: AppContext, parent=None) -> None:
        self.ctx = ctx
        if parent is None:
            self.win = tk.Toplevel(ctx.root)
            self.win.title("Register & Shift")
            self.win.geometry("820x760")
            host = self.win
        else:
            self.win = parent.winfo_toplevel()
            host = parent
        host.config(bg=WHITE)
        self.status = tk.Label(host, text="", bg=WHITE, font=(FONT, 13, "bold"))
        self.status.pack(pady=(12, 4))
        self.report = tk.Label(host, text="", bg=WHITE, fg=DARK, font=("Courier", 11), justify="left", anchor="nw")
        self.report.pack(padx=20, anchor="w")
        bar = tk.Frame(host, bg=WHITE)
        bar.pack(pady=10)
        self.buttons = {}
        for key, text, command, color in (
                ("open", "▶ Open shift", self.open_shift, GREEN), ("close", "■ Close shift", self.close_shift, RED),
                ("expense", "💸 Add expense", self.add_expense, "#fd7e14"),
                ("print", "🖨 Print summary", self.print_summary, "#0d6efd")):
            self.buttons[key] = CanvasButton(bar, text, command, color, width=150, height=30)
            self.buttons[key].pack(side="left", padx=4)
        bar2 = tk.Frame(host, bg=WHITE)
        bar2.pack()
        for key, text, command, color in (
                ("dues", "📒 Customer dues", lambda: open_receivables(ctx), "#b02a37"),
                ("expenses", "📊 Expenses report", lambda: open_expenses(ctx), "#6f42c1"),
                ("history", "🕘 Shift history", lambda: open_shift_history(ctx), MUTED)):
            self.buttons[key] = CanvasButton(bar2, text, command, color, width=170, height=28)
            self.buttons[key].pack(side="left", padx=4, pady=4)
        tk.Label(host, text="Today's expenses", bg=WHITE, fg=DARK, font=(FONT, 11, "bold")).pack(pady=(10, 0))
        box, self.tree = make_tree(host, ("Time", "Category", "Amount", "Paid from", "Note", "By"),
                                   (70, 130, 90, 90, 280, 90))
        box.pack(fill="both", expand=True, padx=15, pady=8)
        self.refresh()

    def _shop_name(self) -> str:
        return business_service.shop_basics(self.ctx.conn)["shop_name"]

    @handle_errors
    def refresh(self) -> None:
        conn, session = self.ctx.conn, self.ctx.session
        shift = shift_service.current_shift(conn)
        if shift is None:
            self.status.config(text="No shift is open", fg=MUTED)
            self.report.config(text="Open a shift with the cash that is in the drawer.\n"
                                    "Sales, payments and expenses are summarised from that moment.")
        else:
            row, summary = shift_service.live_summary(conn, session)
            self.status.config(text=f"Shift #{row['id']} open since {row['opened_at']} ({row['opened_by']})", fg=GREEN)
            self.report.config(text="\n".join(shift_report.render(self._shop_name(), row, summary, 46)))
        use, manage = self.ctx.can(shift_service.PERM_USE), self.ctx.can(shift_service.PERM_MANAGE)
        self.buttons["open"].set_enabled(use and shift is None)
        self.buttons["close"].set_enabled(use and shift is not None)
        self.buttons["print"].set_enabled(shift is not None)
        self.buttons["expense"].set_enabled(self.ctx.can(expense_service.PERM_ADD))
        self.buttons["dues"].set_enabled(self.ctx.can(credit_service.PERM_VIEW))
        self.buttons["expenses"].set_enabled(manage)
        self.buttons["history"].set_enabled(manage)
        rows = expense_service.list_expenses(conn, session)
        fill_tree(self.tree, [(r["created_at"][11:16], r["category"], fmt(r["amount_cents"]), r["paid_from"],
                               r["note"], r["username"]) for r in rows])

    def open_shift(self) -> None:
        def submit(values):
            shift_service.open_shift(self.ctx.conn, self.ctx.session, values["cash"])
            self.refresh()
        ask_values(self.win, "Open shift", [("cash", "Cash in the drawer now")], "Open shift", submit)

    def close_shift(self) -> None:
        def submit(values):
            row = shift_service.close_shift(self.ctx.conn, self.ctx.session, values["counted"], values["note"])
            self.refresh()
            diff = row["variance_cents"]
            verdict = "balanced" if diff == 0 else (f"{fmt(diff)} over" if diff > 0 else f"{fmt(-diff)} short")
            messagebox.showinfo("Shift closed", f"Expected {fmt(row['expected_cash_cents'])}, counted "
                                                f"{fmt(row['counted_cash_cents'])}: {verdict}.", parent=self.win)
            if messagebox.askyesno("Print summary", "Print the shift summary?", parent=self.win):
                self._print(row, shift_service.summary_of(self.ctx.conn, row))
        ask_values(self.win, "Close shift", [("counted", "Cash counted in the drawer"), ("note", "Note (optional)")],
                   "Close shift", submit)

    def add_expense(self) -> None:
        def submit(values):
            expense_service.add_expense(self.ctx.conn, self.ctx.session, values["category"], values["amount"],
                                        values["note"], values["paid_from"])
            self.refresh()
        ask_values(self.win, "Add expense", [("category", "Category"), ("amount", "Amount"),
                                             ("paid_from", "Paid from"), ("note", "Note (optional)")],
                   "Save expense", submit, {"category": list(expense_service.CATEGORIES),
                                            "paid_from": list(expense_service.PAID_FROM)})

    @handle_errors
    def print_summary(self) -> None:
        row, summary = shift_service.live_summary(self.ctx.conn, self.ctx.session)
        self._print(row, summary)

    def _print(self, row, summary) -> None:
        profile = business_service.get_profile(self.ctx.conn)
        paper = thermal.paper_for(profile["receipt_paper"])
        text = "\n".join(shift_report.render(self._shop_name(), row, summary, paper.chars)) + "\n"
        print_output.print_document(text, paper.name, profile["printer_target"])
        messagebox.showinfo("Printed", "The shift summary was sent to the printer.", parent=self.win)


class ShiftHistoryDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Shift History")
        self.win.geometry("940x460")
        self.win.config(bg=WHITE)
        tk.Label(self.win, text="Double-click a shift to see its summary.", bg=WHITE, fg=MUTED,
                 font=(FONT, 9)).pack(pady=(10, 0))
        box, self.tree = make_tree(self.win, ("#", "Opened by", "Opened", "Closed", "Opening", "Expected", "Counted",
                                              "Difference"), (50, 100, 150, 150, 90, 90, 90, 90))
        box.pack(fill="both", expand=True, padx=12, pady=10)
        self.tree.bind("<Double-1>", self.show_selected)
        self.load()

    @handle_errors
    def load(self) -> None:
        self.rows = {str(r["id"]): r for r in shift_service.list_shifts(self.ctx.conn, self.ctx.session)}
        fill_tree(self.tree, [(r["id"], r["opened_by"], r["opened_at"], r["closed_at"] or "(open)",
                               fmt(r["opening_cash_cents"]),
                               fmt(r["expected_cash_cents"]) if r["closed_at"] else "-",
                               fmt(r["counted_cash_cents"]) if r["closed_at"] else "-",
                               fmt(r["variance_cents"]) if r["closed_at"] else "-") for r in self.rows.values()])

    @handle_errors
    def show_selected(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        shift = self.rows[str(self.tree.item(selected[0])["values"][0])]
        summary = shift_service.summary_of(self.ctx.conn, shift)
        name = business_service.shop_basics(self.ctx.conn)["shop_name"]
        window = tk.Toplevel(self.win)
        window.title(f"Shift #{shift['id']}")
        window.config(bg=WHITE)
        tk.Label(window, text="\n".join(shift_report.render(name, shift, summary, 46)), bg=WHITE, fg=DARK,
                 font=("Courier", 11), justify="left").pack(padx=20, pady=15)


def open_register(ctx: AppContext) -> RegisterDialog:
    return RegisterDialog(ctx)


def open_shift_history(ctx: AppContext) -> ShiftHistoryDialog:
    return ShiftHistoryDialog(ctx)

"""Customer purchase history (opened from the POS tab)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ...money import fmt
from ...services import customer_service, till_service
from ..context import AppContext
from ..widgets import DARK, FONT, WHITE, CanvasButton, fill_tree, handle_errors, make_tree
from .sales_dialogs import show_receipt


@handle_errors
def open_customer_history(ctx: AppContext, phone: str) -> None:
    info = customer_service.find_by_phone(ctx.conn, ctx.session, phone)
    if info is None:
        messagebox.showinfo("No history", "No customer with that phone number has bought anything yet.")
        return
    rows = customer_service.history(ctx.conn, ctx.session, phone)
    win = tk.Toplevel(ctx.root)
    win.title(f"Customer: {info.name}")
    win.geometry("760x520")
    win.config(bg=WHITE)
    tk.Label(win, text=info.name, bg=WHITE, fg=DARK, font=(FONT, 14, "bold")).pack(pady=(12, 0))
    tk.Label(win, text=(f"{info.phone}   |   {info.points} loyalty points "
                        f"(worth {fmt(customer_service.points_value(info.points))})   |   "
                        f"spent {fmt(info.spent_cents)}   |   {info.visits} visit(s)"),
             bg=WHITE, fg=DARK, font=(FONT, 10)).pack(pady=6)
    box, tree = make_tree(win, ("Invoice", "Date", "Total", "Payment", "Status"), (170, 150, 100, 120, 90))
    box.pack(fill="both", expand=True, padx=15, pady=10)
    fill_tree(tree, [(r["invoice_no"], r["created_at"], fmt(r["total_cents"]), r["payment_method"],
                      "VOIDED" if r["status"] == "Voided" else "Completed") for r in rows])

    @handle_errors
    def view(_event=None) -> None:
        picked = tree.selection()
        if not picked:
            messagebox.showwarning("Warning", "Select an invoice first!")
            return
        invoice, meta = till_service.get_invoice_with_payment(ctx.conn, ctx.session,
                                                              str(tree.item(picked[0], "values")[0]))
        show_receipt(ctx, invoice, meta)

    tree.bind("<Double-1>", view)
    CanvasButton(win, "👁 View Receipt", view, "#198754", width=150, height=32).pack(pady=(0, 12))

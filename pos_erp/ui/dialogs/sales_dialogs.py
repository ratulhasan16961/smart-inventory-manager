"""Receipt window and the return / refund terminal."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ... import config
from ...money import fmt
from ...services import returns_service, settings_service
from ...services.receipt import render_receipt
from ...services.sales_service import Invoice
from ..context import AppContext
from ..widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree,
                       handle_errors, make_tree)


def show_receipt(ctx: AppContext, invoice: Invoice) -> None:
    shop = settings_service.get_all(ctx.conn)
    text = render_receipt(invoice, shop["shop_name"], shop["receipt_footer"])
    win = tk.Toplevel(ctx.root)
    win.title("Sales Invoice & Receipt")
    win.geometry("420x600")
    win.config(bg=WHITE)
    tk.Label(win, text=text, font=("Courier", 10), bg=WHITE, fg="#000000", justify="left").pack(padx=10, pady=10)

    @handle_errors
    def export_txt() -> None:
        config.INVOICE_DIR.mkdir(parents=True, exist_ok=True)
        path = config.INVOICE_DIR / f"Invoice_{invoice.invoice_no}.txt"
        path.write_text(text, encoding="utf-8")
        messagebox.showinfo("Export Successful", f"Invoice saved as {path}")

    CanvasButton(win, "📄 Export Invoice", export_txt, "#198754", width=140, height=32).pack(pady=5)


def open_returns(ctx: AppContext) -> None:
    win = tk.Toplevel(ctx.root)
    win.title("Sales Return & Refund Terminal")
    win.geometry("600x600")
    win.config(bg=WHITE)
    tk.Label(win, text="Process Return / Refund", bg=WHITE, fg=DARK, font=(FONT, 12, "bold")).pack(pady=10)

    top = tk.Frame(win, bg=WHITE)
    top.pack()
    tk.Label(top, text="Invoice No (INV-...):", **LABEL_STYLE).grid(row=0, column=0, padx=5)
    container, invoice_entry = create_styled_entry(top, width=24)
    container.grid(row=0, column=1, padx=5)

    box, tree = make_tree(win, ("Product ID", "Product", "Sold", "Returned", "Left"), (80, 230, 60, 80, 60), height=6)
    box.pack(fill="x", padx=15, pady=10)

    @handle_errors
    def lookup() -> None:
        rows = returns_service.invoice_lines(ctx.conn, ctx.session, invoice_entry.get())
        fill_tree(tree, [(r["product_id"], r["product_name"], r["sold"], r["returned"], r["sold"] - r["returned"])
                         for r in rows])

    CanvasButton(top, "Look up", lookup, "#0d6efd", width=80, height=28).grid(row=0, column=2, padx=5)

    form = tk.Frame(win, bg=WHITE)
    form.pack(pady=5)
    entries = {}
    for row, label in enumerate(("Product ID", "Return Qty", "Reason")):
        tk.Label(form, text=f"{label}:", **LABEL_STYLE).grid(row=row, column=0, padx=5, pady=4, sticky="e")
        container, entry = create_styled_entry(form, width=24)
        container.grid(row=row, column=1, padx=5, pady=4)
        entries[label] = entry
    restock = tk.BooleanVar(value=True)
    tk.Checkbutton(win, text="Return items to stock (untick if damaged)", variable=restock, bg=WHITE).pack(pady=4)

    def pick(_event=None) -> None:
        selected = tree.selection()
        if selected:
            entries["Product ID"].delete(0, tk.END)
            entries["Product ID"].insert(0, str(tree.item(selected[0], "values")[0]))

    tree.bind("<<TreeviewSelect>>", pick)

    @handle_errors
    def confirm() -> None:
        result = returns_service.process_return(
            ctx.conn, ctx.session, invoice_entry.get(), entries["Product ID"].get(), entries["Return Qty"].get(),
            entries["Reason"].get(), restock.get())
        tail = " and stock updated successfully!" if result.restocked else ". Stock was not changed."
        messagebox.showinfo("Success", f"Item refunded ({fmt(result.refund_cents)}){tail}")
        ctx.refresh()
        win.destroy()

    CanvasButton(win, "Confirm Refund", confirm, "#dc3545", width=140, height=32).pack(pady=12)
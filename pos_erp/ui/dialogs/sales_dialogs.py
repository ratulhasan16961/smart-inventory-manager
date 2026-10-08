"""Receipt window (with print), the return / refund terminal, and the invoices window (reprint + void)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ... import config
from ...money import fmt
from ...services import print_service, returns_service, settings_service, till_service
from ...services.receipt import render_receipt
from ...services.sales_service import Invoice
from ..context import AppContext
from ..widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree,
                       handle_errors, make_tree)


def show_receipt(ctx: AppContext, invoice: Invoice, meta=None) -> None:
    meta = meta if meta is not None else till_service.invoice_meta(ctx.conn, invoice.invoice_no)
    shop = settings_service.get_all(ctx.conn)
    text = render_receipt(invoice, shop["shop_name"], shop["receipt_footer"],
                          tendered_cents=meta["tendered_cents"], change_cents=meta["change_cents"],
                          voided=meta["status"] == "Voided")
    win = tk.Toplevel(ctx.root)
    win.title("Sales Invoice & Receipt")
    win.geometry("420x680")
    win.config(bg=WHITE)
    tk.Label(win, text=text, font=("Courier", 10), bg=WHITE, fg="#000000", justify="left").pack(padx=10, pady=10)

    @handle_errors
    def export_txt() -> None:
        config.INVOICE_DIR.mkdir(parents=True, exist_ok=True)
        path = config.INVOICE_DIR / f"Invoice_{invoice.invoice_no}.txt"
        path.write_text(text, encoding="utf-8")
        messagebox.showinfo("Export Successful", f"Invoice saved as {path}")

    @handle_errors
    def print_receipt() -> None:
        print_service.print_text(text)
        messagebox.showinfo("Printing", "The receipt was sent to the printer.")

    buttons = tk.Frame(win, bg=WHITE)
    buttons.pack(pady=5)
    CanvasButton(buttons, "📄 Export Invoice", export_txt, "#198754", width=140, height=32).pack(side="left", padx=5)
    CanvasButton(buttons, "🖨 Print", print_receipt, "#0d6efd", width=100, height=32).pack(side="left", padx=5)


def _ask_reason(parent, title: str, warning: str, on_submit) -> None:
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry("480x240")
    win.config(bg=WHITE)
    tk.Label(win, text=warning, bg=WHITE, fg="#842029", font=(FONT, 10), wraplength=440, justify="left").pack(
        padx=15, pady=12)
    tk.Label(win, text="Reason:", **LABEL_STYLE).pack()
    container, entry = create_styled_entry(win, width=46)
    container.pack(pady=6)

    @handle_errors
    def submit() -> None:
        on_submit(entry.get())
        win.destroy()

    CanvasButton(win, "Confirm void", submit, "#dc3545", width=150, height=32).pack(pady=12)
    entry.focus_set()


@handle_errors
def open_invoices(ctx: AppContext) -> None:
    ctx.session.require("invoices.view")
    win = tk.Toplevel(ctx.root)
    win.title("Recent Invoices")
    win.geometry("1040x600")
    win.config(bg=WHITE)

    bar = tk.Frame(win, bg=WHITE)
    bar.pack(fill="x", padx=15, pady=10)
    entries = {}
    for label, width in (("Search (invoice / name / phone)", 22), ("From (YYYY-MM-DD)", 12), ("To", 12)):
        tk.Label(bar, text=f"{label}:", **LABEL_STYLE).pack(side="left", padx=4)
        container, entry = create_styled_entry(bar, width=width)
        container.pack(side="left", padx=4)
        entries[label] = entry
    search_e, from_e, to_e = entries.values()

    box, tree = make_tree(win, ("Invoice", "Date", "Customer", "Total", "Payment", "Cashier", "Status"),
                          (170, 150, 170, 100, 120, 100, 90))
    box.pack(fill="both", expand=True, padx=15, pady=(0, 10))

    @handle_errors
    def load(_event=None) -> None:
        rows = till_service.list_invoices(ctx.conn, ctx.session, search_e.get(), from_e.get(), to_e.get())
        fill_tree(tree, [(r["invoice_no"], r["created_at"], r["customer_name"], fmt(r["total_cents"]),
                          r["payment_method"], r["cashier"], "VOIDED" if r["status"] == "Voided" else "Completed")
                         for r in rows])

    def selected():
        picked = tree.selection()
        if not picked:
            messagebox.showwarning("Warning", "Select an invoice first!")
            return None
        return str(tree.item(picked[0], "values")[0])

    @handle_errors
    def view(_event=None) -> None:
        invoice_no = selected()
        if invoice_no is not None:
            invoice, meta = till_service.get_invoice_with_payment(ctx.conn, ctx.session, invoice_no)
            show_receipt(ctx, invoice, meta)

    def void() -> None:
        invoice_no = selected()
        if invoice_no is None:
            return

        def apply_void(reason: str) -> None:
            till_service.void_invoice(ctx.conn, ctx.session, invoice_no, reason)
            messagebox.showinfo("Invoice voided", f"{invoice_no} was voided and its items returned to stock.")
            load()
            ctx.refresh()

        _ask_reason(win, f"Void {invoice_no}",
                    "Voiding returns the items to stock, reverses the customer's totals and removes the sale "
                    "from reports. It cannot be undone.", apply_void)

    search_e.bind("<Return>", load)
    tree.bind("<Double-1>", view)
    CanvasButton(bar, "🔍 Search", load, "#0d6efd", width=90, height=28).pack(side="left", padx=6)
    actions = tk.Frame(win, bg=WHITE)
    actions.pack(pady=(0, 12))
    CanvasButton(actions, "👁 View / Print Receipt", view, "#198754", width=190, height=32).pack(side="left", padx=5)
    void_button = CanvasButton(actions, "⛔ Void Invoice", void, "#dc3545", width=150, height=32)
    void_button.pack(side="left", padx=5)
    void_button.set_enabled(ctx.can("sales.void"))
    load()


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

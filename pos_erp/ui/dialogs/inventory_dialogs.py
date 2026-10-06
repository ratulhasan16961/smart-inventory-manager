"""Product edit dialog and the barcode label preview."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ...money import plain
from ...services import inventory_service
from ...services.inventory_service import ProductForm
from ..context import AppContext
from ..widgets import FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, handle_errors

EDIT_FIELDS = ("Name", "Category", "Buying Price", "Selling Price", "Stock", "Min Alert", "Batch",
               "Barcode", "Warehouse", "Expiry (YYYY-MM)")
REASON_LABEL = "Reason if stock changed"


@handle_errors
def open_edit_product(ctx: AppContext, product_id: int) -> None:
    product = inventory_service.get_product(ctx.conn, ctx.session, product_id)
    win = tk.Toplevel(ctx.root)
    win.title(f"Edit Product ID: {product_id}")
    win.geometry("430x560")
    win.config(bg=WHITE)

    current = [product["name"], product["category"], plain(product["cost_cents"]), plain(product["price_cents"]),
               product["stock"], product["min_alert"], product["batch"], product["barcode"] or "",
               product["warehouse"], product["expiry"], ""]
    entries = {}
    for row, (label, value) in enumerate(zip(EDIT_FIELDS + (REASON_LABEL,), current)):
        tk.Label(win, text=f"{label}:", **LABEL_STYLE).grid(row=row, column=0, padx=10, pady=5, sticky="e")
        container, entry = create_styled_entry(win, width=24)
        container.grid(row=row, column=1, padx=10, pady=5)
        entry.insert(0, str(value))
        entries[label] = entry

    @handle_errors
    def save() -> None:
        form = ProductForm(
            name=entries["Name"].get(), category=entries["Category"].get(),
            buying_price=entries["Buying Price"].get(), price=entries["Selling Price"].get(),
            stock=entries["Stock"].get(), min_alert=entries["Min Alert"].get(), batch=entries["Batch"].get(),
            barcode=entries["Barcode"].get(), warehouse=entries["Warehouse"].get(),
            expiry=entries["Expiry (YYYY-MM)"].get())
        inventory_service.update_product(ctx.conn, ctx.session, product_id, form,
                                         stock_note=entries[REASON_LABEL].get())
        messagebox.showinfo("Updated", "Product successfully updated!")
        win.destroy()
        ctx.refresh()

    CanvasButton(win, "Save Changes", save, "#198754", width=130, height=32).grid(
        row=len(EDIT_FIELDS) + 1, column=0, columnspan=2, pady=15)


def open_barcode_generator(ctx: AppContext) -> None:
    """Text-only label preview (placeholder, as in the original app)."""
    win = tk.Toplevel(ctx.root)
    win.title("Barcode & Label Generator")
    win.geometry("380x300")
    win.config(bg=WHITE)
    tk.Label(win, text="Generate Item Barcode Label", bg=WHITE, fg="#212529", font=(FONT, 12, "bold")).pack(pady=10)
    tk.Label(win, text="Product Code / Barcode:", **LABEL_STYLE).pack()
    container, code_entry = create_styled_entry(win, width=22)
    container.pack(pady=5)
    preview = tk.Label(win, text="Barcode Visual Canvas", bg=WHITE, bd=1, relief="solid", width=34, height=4)
    preview.pack(pady=10)

    def draw() -> None:
        value = code_entry.get().strip()
        if not value:
            messagebox.showwarning("Warning", "Enter barcode value!")
            return
        preview.config(text=f"||||||||||||||||||||||||||||\n{value}\n[ENTERPRISE POS LABEL]",
                       font=("Courier", 11, "bold"))

    CanvasButton(win, "Generate Label", draw, "#0d6efd", width=130, height=30).pack(pady=5)
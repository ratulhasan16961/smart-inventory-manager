"""Create a purchase order from the products that are running low."""
from __future__ import annotations

import tkinter as tk
from collections import Counter
from tkinter import messagebox, ttk

from ...money import fmt, to_cents
from ...services import purchase_service
from ...services.purchase_service import POLine
from ..context import AppContext
from ..widgets import DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree, handle_errors, make_tree


@handle_errors
def open_reorder(ctx: AppContext, on_created) -> None:
    suggestions = purchase_service.reorder_suggestions(ctx.conn, ctx.session)
    if not suggestions:
        messagebox.showinfo("Nothing to reorder", "No product is below its minimum alert "
                                                  "(after counting what is already on order).")
        return
    suppliers = purchase_service.list_suppliers(ctx.conn, ctx.session)
    if not suppliers:
        messagebox.showwarning("No suppliers", "Add a supplier first (Suppliers button).")
        return
    by_name = {f"{s['name']} (#{s['id']})": s["id"] for s in suppliers}
    name_by_id = {supplier_id: name for name, supplier_id in by_name.items()}
    usual = Counter(s.supplier_id for s in suggestions if s.supplier_id in name_by_id)
    default_id = usual.most_common(1)[0][0] if usual else suppliers[0]["id"]
    lines = [{"product_id": s.product_id, "name": s.name, "stock": s.stock, "min": s.min_alert,
              "on_order": s.on_order, "qty": s.quantity, "cost": s.unit_cost, "last": s.supplier_name or "-"}
             for s in suggestions]

    win = tk.Toplevel(ctx.root)
    win.title("Reorder Low Stock")
    win.geometry("900x620")
    win.config(bg=WHITE)
    tk.Label(win, text="Suggested order quantity = 2 x minimum alert - stock - already on order.",
             bg=WHITE, fg=DARK, font=(FONT, 10)).pack(pady=(12, 4))
    top = tk.Frame(win, bg=WHITE)
    top.pack(pady=6)
    tk.Label(top, text="Supplier:", **LABEL_STYLE).pack(side="left", padx=6)
    supplier_combo = ttk.Combobox(top, values=list(by_name), state="readonly", width=34)
    supplier_combo.pack(side="left", padx=6)
    supplier_combo.set(name_by_id[default_id])

    box, tree = make_tree(win, ("Product", "Stock", "Min", "On order", "Order qty", "Unit cost", "Last supplier"),
                          (230, 70, 60, 80, 90, 90, 160), height=12)
    box.pack(fill="both", expand=True, padx=15, pady=8)
    total_label = tk.Label(win, text="", bg=WHITE, fg=DARK, font=(FONT, 11, "bold"))
    total_label.pack()

    def render() -> None:
        fill_tree(tree, [(l["name"], l["stock"], l["min"], l["on_order"], l["qty"], l["cost"], l["last"])
                         for l in lines])
        total = sum(l["qty"] * to_cents(l["cost"]) for l in lines)
        total_label.config(text=f"{len(lines)} product(s), order total {fmt(total)}")

    def selected_line():
        picked = tree.selection()
        if not picked:
            messagebox.showwarning("Warning", "Select a row first!")
            return None
        return tree.index(picked[0])

    controls = tk.Frame(win, bg=WHITE)
    controls.pack(pady=8)
    tk.Label(controls, text="Qty for selected row:", **LABEL_STYLE).pack(side="left", padx=6)
    container, qty_entry = create_styled_entry(controls, width=8)
    container.pack(side="left", padx=6)

    def set_qty() -> None:
        index = selected_line()
        if index is None:
            return
        try:
            qty = int(qty_entry.get().strip())
        except ValueError:
            qty = 0
        if qty <= 0:
            messagebox.showerror("Error", "Quantity must be a positive whole number!")
            return
        lines[index]["qty"] = qty
        qty_entry.delete(0, tk.END)
        render()

    def remove() -> None:
        index = selected_line()
        if index is not None:
            del lines[index]
            render()

    @handle_errors
    def create() -> None:
        if not lines:
            messagebox.showwarning("Warning", "There is nothing left to order!")
            return
        purchase_service.create_purchase_order(
            ctx.conn, ctx.session, by_name[supplier_combo.get()],
            [POLine(l["product_id"], l["qty"], l["cost"]) for l in lines], "Suggested from low stock")
        messagebox.showinfo("Saved", "Purchase order created.")
        win.destroy()
        on_created()

    CanvasButton(controls, "Set qty", set_qty, "#0d6efd", width=90, height=28).pack(side="left", padx=4)
    CanvasButton(controls, "Remove row", remove, "#dc3545", width=100, height=28).pack(side="left", padx=4)
    CanvasButton(win, "Create purchase order", create, "#198754", width=190, height=34).pack(pady=12)
    render()

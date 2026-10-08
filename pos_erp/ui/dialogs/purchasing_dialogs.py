"""Suppliers and purchase orders (create -> receive stock -> cancel)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...money import fmt, plain, to_cents
from ...services import inventory_service, purchase_service
from ...services.purchase_service import POLine
from ..context import AppContext
from ..widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree,
                       handle_errors, make_tree)


@handle_errors
def open_suppliers(ctx: AppContext) -> None:
    win = tk.Toplevel(ctx.root)
    win.title("Supplier Management")
    win.geometry("820x480")
    win.config(bg=WHITE)
    tk.Label(win, text="Supplier Management", bg=WHITE, fg=DARK, font=(FONT, 12, "bold")).pack(pady=8)

    form = tk.Frame(win, bg=WHITE)
    form.pack(pady=5)
    entries = {}
    for column, label in enumerate(("Name", "Phone", "Email", "Address")):
        tk.Label(form, text=f"{label}:", **LABEL_STYLE).grid(row=0, column=column * 2, padx=4)
        container, entry = create_styled_entry(form, width=13)
        container.grid(row=0, column=column * 2 + 1, padx=4)
        entries[label] = entry

    box, tree = make_tree(win, ("ID", "Supplier Name", "Phone", "Email", "Address"), (50, 190, 120, 190, 200))

    def load() -> None:
        rows = purchase_service.list_suppliers(ctx.conn, ctx.session)
        fill_tree(tree, [(s["id"], s["name"], s["phone"], s["email"], s["address"]) for s in rows])

    @handle_errors
    def add() -> None:
        purchase_service.add_supplier(ctx.conn, ctx.session, entries["Name"].get(), entries["Phone"].get(),
                                      entries["Email"].get(), entries["Address"].get())
        for entry in entries.values():
            entry.delete(0, tk.END)
        messagebox.showinfo("Saved", "Supplier added successfully!")
        load()

    @handle_errors
    def deactivate() -> None:
        selected = tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select a supplier first!")
            return
        if messagebox.askyesno("Confirm", "Deactivate this supplier? Existing orders are kept."):
            purchase_service.deactivate_supplier(ctx.conn, ctx.session, int(tree.item(selected[0], "values")[0]))
            load()

    buttons = tk.Frame(win, bg=WHITE)
    buttons.pack(pady=5)
    CanvasButton(buttons, "Add Supplier", add, "#198754", width=120, height=30).pack(side="left", padx=5)
    CanvasButton(buttons, "Deactivate Selected", deactivate, "#dc3545", width=150, height=30).pack(side="left", padx=5)
    box.pack(fill="both", expand=True, padx=15, pady=10)
    load()


@handle_errors
def open_purchase_orders(ctx: AppContext) -> None:
    win = tk.Toplevel(ctx.root)
    win.title("Purchase Orders")
    win.geometry("980x660")
    win.config(bg=WHITE)

    bar = tk.Frame(win, bg=WHITE)
    bar.pack(fill="x", padx=15, pady=10)
    orders_box, orders = make_tree(
        win, ("ID", "PO Number", "Supplier", "Status", "Created", "Total", "Received"),
        (50, 160, 200, 140, 160, 100, 100), height=9)
    orders_box.pack(fill="both", expand=True, padx=15)
    tk.Label(win, text="Order lines", **LABEL_STYLE).pack(anchor="w", padx=15, pady=(10, 0))
    lines_box, lines = make_tree(win, ("Product", "Ordered", "Received", "Unit Cost", "Line Total"),
                                 (320, 100, 100, 120, 120), height=6)
    lines_box.pack(fill="both", expand=True, padx=15, pady=(0, 10))

    def selected_po() -> int | None:
        selected = orders.selection()
        return int(orders.item(selected[0], "values")[0]) if selected else None

    def load_orders() -> None:
        rows = purchase_service.list_purchase_orders(ctx.conn, ctx.session)
        fill_tree(orders, [(r["id"], r["po_number"], r["supplier"], r["status"], r["created_at"],
                            fmt(r["total_cents"]), f"{r['received']}/{r['ordered']}") for r in rows])
        fill_tree(lines, [])

    @handle_errors
    def show_lines(_event=None) -> None:
        po_id = selected_po()
        if po_id is None:
            return
        items = purchase_service.get_po_items(ctx.conn, ctx.session, po_id)
        fill_tree(lines, [(i["product_name"], i["quantity_ordered"], i["quantity_received"],
                           fmt(i["unit_cost_cents"]), fmt(i["quantity_ordered"] * i["unit_cost_cents"]))
                          for i in items])

    orders.bind("<<TreeviewSelect>>", show_lines)


    @handle_errors
    def new_order() -> None:
        suppliers = purchase_service.list_suppliers(ctx.conn, ctx.session)
        if not suppliers:
            messagebox.showwarning("No suppliers", "Add a supplier first (Suppliers button).")
            return
        by_name = {f"{s['name']} (#{s['id']})": s["id"] for s in suppliers}
        draft: list[dict] = []

        dlg = tk.Toplevel(win)
        dlg.title("New Purchase Order")
        dlg.geometry("720x600")
        dlg.config(bg=WHITE)
        tk.Label(dlg, text="Supplier:", **LABEL_STYLE).grid(row=0, column=0, padx=8, pady=8, sticky="e")
        supplier_combo = ttk.Combobox(dlg, values=list(by_name), state="readonly", width=36)
        supplier_combo.grid(row=0, column=1, columnspan=5, padx=8, sticky="w")
        supplier_combo.current(0)

        def field(caption: str, column: int, width: int):
            tk.Label(dlg, text=caption, **LABEL_STYLE).grid(row=1, column=column, padx=6, pady=6, sticky="e")
            container, entry = create_styled_entry(dlg, width=width)
            container.grid(row=1, column=column + 1, padx=4, pady=6)
            return entry

        product_entry = field("Product (barcode/ID):", 0, 12)
        qty_entry = field("Qty:", 2, 6)
        cost_entry = field("Unit cost:", 4, 8)

        draft_box, draft_tree = make_tree(dlg, ("Product", "Qty", "Unit Cost", "Line Total"), (330, 80, 120, 130),
                                          height=10)
        draft_box.grid(row=3, column=0, columnspan=6, padx=10, pady=8, sticky="nsew")
        total_label = tk.Label(dlg, text="", bg=WHITE, fg=DARK, font=(FONT, 11, "bold"))
        total_label.grid(row=4, column=0, columnspan=6, pady=4)

        def render() -> None:
            fill_tree(draft_tree, [(l["name"], l["qty"], fmt(to_cents(l["cost"])), fmt(l["qty"] * to_cents(l["cost"])))
                                   for l in draft])
            total_label.config(text=f"Order total: {fmt(sum(l['qty'] * to_cents(l['cost']) for l in draft))}")

        @handle_errors
        def add_line() -> None:
            key = product_entry.get().strip()
            if not key:
                messagebox.showwarning("Warning", "Enter a product barcode or ID!")
                return
            found = inventory_service.find_for_sale(ctx.conn, ctx.session, key)
            product = inventory_service.get_product(ctx.conn, ctx.session, found["id"])
            try:
                qty = int(qty_entry.get().strip() or "1")
            except ValueError:
                qty = 0
            if qty <= 0:
                messagebox.showerror("Error", "Quantity must be a positive whole number!")
                return
            cost = cost_entry.get().strip() or plain(product["cost_cents"])
            try:
                if to_cents(cost) < 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Invalid unit cost!")
                return
            if any(l["product_id"] == product["id"] for l in draft):
                messagebox.showwarning("Warning", "This product is already on the order. Remove it first to change it.")
                return
            draft.append({"product_id": product["id"], "name": product["name"], "qty": qty, "cost": cost})
            for entry in (product_entry, qty_entry, cost_entry):
                entry.delete(0, tk.END)
            render()

        def remove_line() -> None:
            selected = draft_tree.selection()
            if selected:
                del draft[draft_tree.index(selected[0])]
                render()

        CanvasButton(dlg, "Add line", add_line, "#6f42c1", width=90, height=28).grid(row=1, column=6, padx=6)
        tk.Label(dlg, text="Notes:", **LABEL_STYLE).grid(row=5, column=0, padx=8, pady=6, sticky="e")
        container, notes_entry = create_styled_entry(dlg, width=50)
        container.grid(row=5, column=1, columnspan=5, padx=8, sticky="w")

        @handle_errors
        def create() -> None:
            purchase_service.create_purchase_order(
                ctx.conn, ctx.session, by_name[supplier_combo.get()],
                [POLine(l["product_id"], l["qty"], l["cost"]) for l in draft], notes_entry.get())
            messagebox.showinfo("Saved", "Purchase order created.")
            dlg.destroy()
            load_orders()

        actions = tk.Frame(dlg, bg=WHITE)
        actions.grid(row=6, column=0, columnspan=6, pady=10)
        CanvasButton(actions, "Remove Selected Line", remove_line, "#dc3545", width=160, height=30).pack(side="left", padx=5)
        CanvasButton(actions, "Create Order", create, "#198754", width=130, height=30).pack(side="left", padx=5)
        render()


    @handle_errors
    def receive() -> None:
        po_id = selected_po()
        if po_id is None:
            messagebox.showwarning("Warning", "Select a purchase order first!")
            return
        items = [i for i in purchase_service.get_po_items(ctx.conn, ctx.session, po_id)
                 if i["quantity_ordered"] > i["quantity_received"]]
        if not items:
            messagebox.showinfo("Nothing to receive", "Nothing is outstanding on this order.")
            return

        dlg = tk.Toplevel(win)
        dlg.title("Receive Stock")
        dlg.geometry(f"560x{140 + 42 * len(items)}")
        dlg.config(bg=WHITE)
        for column, caption in enumerate(("Product", "Outstanding", "Receive now")):
            tk.Label(dlg, text=caption, **LABEL_STYLE).grid(row=0, column=column, padx=10, pady=8)
        entries: dict[int, tk.Entry] = {}
        for row, item in enumerate(items, start=1):
            outstanding = item["quantity_ordered"] - item["quantity_received"]
            tk.Label(dlg, text=item["product_name"], bg=WHITE, fg=DARK).grid(row=row, column=0, padx=10, pady=4, sticky="w")
            tk.Label(dlg, text=str(outstanding), bg=WHITE, fg=DARK).grid(row=row, column=1)
            container, entry = create_styled_entry(dlg, width=8)
            container.grid(row=row, column=2, padx=10)
            entry.insert(0, str(outstanding))
            entries[item["id"]] = entry

        @handle_errors
        def confirm() -> None:
            receipts = {}
            for item_id, entry in entries.items():
                try:
                    receipts[item_id] = int(entry.get().strip() or "0")
                except ValueError:
                    messagebox.showerror("Error", "Quantities must be whole numbers!")
                    return
            status = purchase_service.receive_purchase_order(ctx.conn, ctx.session, po_id, receipts)
            messagebox.showinfo("Stock received", f"Order status: {status}")
            dlg.destroy()
            load_orders()
            ctx.refresh()

        CanvasButton(dlg, "Receive Stock", confirm, "#198754", width=140, height=32).grid(
            row=len(items) + 1, column=0, columnspan=3, pady=14)

    @handle_errors
    def cancel() -> None:
        po_id = selected_po()
        if po_id is None:
            messagebox.showwarning("Warning", "Select a purchase order first!")
            return
        if messagebox.askyesno("Confirm", "Cancel this purchase order?"):
            purchase_service.cancel_purchase_order(ctx.conn, ctx.session, po_id)
            load_orders()

    CanvasButton(bar, "➕ New Order", new_order, "#198754", width=120, height=30).pack(side="left", padx=4)
    CanvasButton(bar, "📥 Receive Stock", receive, "#0d6efd", width=130, height=30).pack(side="left", padx=4)
    CanvasButton(bar, "✖ Cancel Order", cancel, "#dc3545", width=120, height=30).pack(side="left", padx=4)
    CanvasButton(bar, "↻ Refresh", load_orders, "#6c757d", width=90, height=30).pack(side="left", padx=4)
    load_orders()

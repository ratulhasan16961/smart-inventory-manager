"""Inventory tab: product form, role-aware action buttons, search and the highlighted product table."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import config
from ..money import plain
from ..services import inventory_service
from ..services.inventory_service import ProductForm
from .context import AppContext
from .dialogs.admin_dialogs import open_analytics, open_audit_logs, open_settings, open_stock_ledger
from .dialogs.inventory_dialogs import open_barcode_generator, open_edit_product
from .dialogs.purchasing_dialogs import open_purchase_orders, open_suppliers
from .dialogs.sales_dialogs import open_returns
from .widgets import FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, handle_errors, make_tree

# (label, widget kind, row, column, width)
FIELDS = (
    ("Name", "entry", 0, 0, 15), ("Category", "combo", 0, 2, 13), ("Buying Price", "entry", 0, 4, 12),
    ("Selling Price", "entry", 1, 0, 15), ("Stock", "entry", 1, 2, 15), ("Min Alert", "entry", 1, 4, 12),
    ("Batch No", "entry", 2, 0, 15), ("Barcode", "entry", 2, 2, 15), ("Warehouse", "combo", 2, 4, 11),
    ("Expiry (YYYY-MM)", "entry", 3, 0, 15),
)
COLUMNS = ("ID", "Name", "Category", "Buying Price", "Price", "Stock", "Min Alert", "Batch",
           "Barcode", "Warehouse", "Expiry")
WIDTHS = (45, 190, 100, 95, 80, 70, 80, 90, 140, 140, 90)


class InventoryTab:
    def __init__(self, parent: tk.Frame, ctx: AppContext) -> None:
        self.ctx = ctx
        self.fields: dict[str, tk.Widget] = {}
        self._build_form(parent)
        self._build_search(parent)
        self._build_table(parent)

    # ------------------------------------------------------------------ layout
    def _build_form(self, parent) -> None:
        frame = tk.LabelFrame(parent, text=" Product Entry Form ", bg=WHITE, fg="#212529",
                              font=(FONT, 10, "bold"), bd=1, relief="solid")
        frame.pack(fill="x", padx=5, pady=5)
        form = tk.Frame(frame, bg=WHITE)
        form.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        buttons = tk.Frame(frame, bg=WHITE)
        buttons.pack(side="right", fill="y", padx=15, pady=10)

        for label, kind, row, col, width in FIELDS:
            tk.Label(form, text=f"{label}:", **LABEL_STYLE).grid(row=row, column=col, sticky="e", padx=5, pady=5)
            if kind == "entry":
                container, widget = create_styled_entry(form, width=width)
                container.grid(row=row, column=col + 1, padx=5, pady=5)
            else:
                values = config.CATEGORIES if label == "Category" else config.WAREHOUSES
                widget = ttk.Combobox(form, values=list(values), width=width, font=(FONT, 10))
                widget.grid(row=row, column=col + 1, padx=5, pady=5)
            self.fields[label] = widget
        self.clear_form()

        ctx = self.ctx
        specs = (  # (text, handler, colour, permission, text colour)
            ("➕ Add Product", self.add_product, "#198754", "inventory.manage", "#ffffff"),
            ("📊 Analytics", lambda: open_analytics(ctx), "#fd7e14", "analytics.view", "#ffffff"),
            ("🗑 Delete Product", self.delete_selected, "#dc3545", "inventory.manage", "#ffffff"),
            ("📤 Export CSV", self.export_csv, "#0d6efd", "inventory.export", "#ffffff"),
            ("📥 Import CSV", self.import_csv, "#6c757d", "inventory.import", "#ffffff"),
            ("📜 Audit Logs", lambda: open_audit_logs(ctx), "#212529", "audit.view", "#ffffff"),
            ("🏷 Barcode Gen", lambda: open_barcode_generator(ctx), "#0dcaf0", "barcode.generate", "#000000"),
            ("🏭 Suppliers", lambda: open_suppliers(ctx), "#20c997", "purchasing.manage", "#ffffff"),
            ("📦 Purchase Orders", lambda: open_purchase_orders(ctx), "#6610f2", "purchasing.manage", "#ffffff"),
            ("🔄 Return / Refund", lambda: open_returns(ctx), "#d63384", "returns.process", "#ffffff"),
            ("📒 Stock Ledger", lambda: open_stock_ledger(ctx), "#0b5ed7", "stock.view", "#ffffff"),
            ("⚙ Settings", lambda: open_settings(ctx), "#495057", "account.update", "#ffffff"),
        )
        for index, (text, handler, color, permission, text_color) in enumerate(specs):
            button = CanvasButton(buttons, text, handler, color, text_color, width=135, height=30)
            button.grid(row=index // 3, column=index % 3, padx=4, pady=3)
            button.set_enabled(ctx.can(permission))  # role-based: greyed out instead of error popups

    def _build_search(self, parent) -> None:
        bar = tk.Frame(parent, bg="#f4f6f9")
        bar.pack(fill="x", padx=5, pady=5)
        tk.Label(bar, text="Search Inventory:", bg="#f4f6f9", fg="#212529", font=(FONT, 10, "bold")).pack(
            side="left", padx=5)
        container, self.search_entry = create_styled_entry(bar, width=25)
        container.pack(side="left", padx=5)
        self.search_entry.bind("<Return>", lambda _e: self.refresh_table())
        CanvasButton(bar, "🔍 Search", self.refresh_table, "#0d6efd", width=90, height=28).pack(side="left", padx=5)
        CanvasButton(bar, "🔄 Reset Table", self.reset_search, "#6c757d", width=100, height=28).pack(
            side="left", padx=5)
        tk.Label(bar, text="🔴 Low stock    🟠 Expiring ≤ 30 days    ⚫ Expired", bg="#f4f6f9", fg="#495057",
                 font=(FONT, 9)).pack(side="right", padx=10)

    def _build_table(self, parent) -> None:
        box, self.tree = make_tree(parent, COLUMNS, WIDTHS)
        box.pack(fill="both", expand=True, padx=5, pady=5)
        self.tree.tag_configure("low_stock", background="#ffd2d2", foreground="#842029")
        self.tree.tag_configure("expiring", background="#ffe8cc", foreground="#8a4b08")
        self.tree.tag_configure("expired", background="#842029", foreground="#ffffff")
        self.tree.bind("<Double-1>", self.open_edit)

    # ------------------------------------------------------------------ table
    @handle_errors
    def refresh_table(self) -> None:
        rows = inventory_service.list_products(self.ctx.conn, self.ctx.session, self.search_entry.get())
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in rows:
            flag = inventory_service.product_flag(row)
            values = (row["id"], row["name"], row["category"], plain(row["cost_cents"]), plain(row["price_cents"]),
                      row["stock"], row["min_alert"], row["batch"], row["barcode"] or "", row["warehouse"],
                      row["expiry"])
            self.tree.insert("", "end", values=values, tags=(flag,) if flag else ())

    def reset_search(self) -> None:
        self.search_entry.delete(0, tk.END)
        self.refresh_table()

    def _selected_id(self) -> int | None:
        selected = self.tree.selection()
        return int(self.tree.item(selected[0], "values")[0]) if selected else None

    def check_low_stock_alerts(self) -> None:
        alerts = inventory_service.low_stock(self.ctx.conn)
        if alerts:
            lines = "".join(f"• {a['name']} - Current Stock: {a['stock']} (Min Limit: {a['min_alert']})\n"
                            for a in alerts)
            messagebox.showwarning("Low Stock Notice", "Warning! Below items are running low on stock:\n\n" + lines)

    # ------------------------------------------------------------------ form
    def clear_form(self) -> None:
        for label, widget in self.fields.items():
            if isinstance(widget, ttk.Combobox):
                widget.set("General" if label == "Category" else config.WAREHOUSES[0])
            else:
                widget.delete(0, tk.END)

    def _read_form(self) -> ProductForm:
        def get(name: str) -> str:
            return self.fields[name].get()

        return ProductForm(name=get("Name"), category=get("Category"), buying_price=get("Buying Price"),
                           price=get("Selling Price"), stock=get("Stock"), min_alert=get("Min Alert"),
                           batch=get("Batch No"), barcode=get("Barcode"), warehouse=get("Warehouse"),
                           expiry=get("Expiry (YYYY-MM)"))

    # ------------------------------------------------------------------ actions
    @handle_errors
    def add_product(self) -> None:
        inventory_service.add_product(self.ctx.conn, self.ctx.session, self._read_form())
        messagebox.showinfo("Success", "Product added successfully!")
        self.clear_form()
        self.ctx.refresh()

    def open_edit(self, _event=None) -> None:
        product_id = self._selected_id()
        if product_id is not None and self.ctx.can("inventory.manage"):
            open_edit_product(self.ctx, product_id)

    @handle_errors
    def delete_selected(self) -> None:
        product_id = self._selected_id()
        if product_id is None:
            messagebox.showwarning("Warning", "Select a product to delete!")
            return
        if messagebox.askyesno("Confirm", f"Delete product ID {product_id}?\nIts sales history is kept."):
            inventory_service.deactivate_product(self.ctx.conn, self.ctx.session, product_id)
            self.ctx.refresh()

    @handle_errors
    def export_csv(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
        if path:
            count = inventory_service.export_csv(self.ctx.conn, self.ctx.session, path)
            messagebox.showinfo("Export Complete", f"{count} products exported to CSV successfully!")

    @handle_errors
    def import_csv(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if not path:
            return
        preview = inventory_service.import_csv(self.ctx.conn, self.ctx.session, path, dry_run=True)
        if not preview.ok:
            shown = "\n".join(preview.errors[:10])
            extra = f"\n... and {len(preview.errors) - 10} more" if len(preview.errors) > 10 else ""
            messagebox.showerror("Import blocked", f"Nothing was imported. Please fix these problems:\n\n{shown}{extra}")
            return
        if messagebox.askyesno("Confirm import",
                               f"{preview.created} product(s) will be created and {preview.updated} updated.\n"
                               "Stock levels in the file replace the current stock. Continue?"):
            inventory_service.import_csv(self.ctx.conn, self.ctx.session, path)
            messagebox.showinfo("Import Complete", "Products imported successfully!")
            self.ctx.refresh()
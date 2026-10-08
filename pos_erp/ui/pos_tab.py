"""POS Billing tab: customer fields, live cart table and checkout."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import config
from ..errors import POSError
from ..money import fmt
from ..services import inventory_service, sales_service, settings_service
from .context import AppContext
from .dialogs.sales_dialogs import show_receipt
from .widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, handle_errors,
                      make_tree)


class PosTab:
    def __init__(self, parent: tk.Frame, ctx: AppContext) -> None:
        self.ctx = ctx
        self.cart: list[dict] = []
        self._build_billing(parent)
        self._build_cart(parent)
        self.render_cart()

    def _build_billing(self, parent) -> None:
        frame = tk.LabelFrame(parent, text=" Customer Billing & POS Multi-Item Cart ", bg=WHITE, fg="#0d6efd",
                              font=(FONT, 10, "bold"), bd=1, relief="solid")
        frame.pack(fill="x", padx=5, pady=5)

        def field(caption: str, row: int, column: int, width: int, **grid):
            tk.Label(frame, text=caption, **LABEL_STYLE).grid(row=row, column=column, padx=4, pady=6, sticky="e")
            container, entry = create_styled_entry(frame, width=width)
            container.grid(row=row, column=column + 1, padx=4, pady=6, **grid)
            return entry

        self.prod_id_e = field("Barcode Scan / ID:", 0, 0, 14)
        self.prod_qty_e = field("Qty:", 0, 2, 5)
        CanvasButton(frame, "🛒 Add Cart", self.add_to_cart, "#6f42c1", width=90, height=28).grid(
            row=0, column=4, padx=6, pady=6)
        self.disc_e = field(f"Discount ({config.CURRENCY_SYMBOL}):", 0, 5, 7)
        self.tax_e = field("Tax (%):", 0, 7, 6)
        self.disc_e.insert(0, "0")
        self.tax_e.insert(0, settings_service.get(self.ctx.conn, "default_tax_rate"))
        tk.Label(frame, text="Payment:", **LABEL_STYLE).grid(row=0, column=9, padx=4, pady=6, sticky="e")
        self.pay_combo = ttk.Combobox(frame, values=list(config.PAYMENT_METHODS), width=14, font=(FONT, 9),
                                      state="readonly")
        self.pay_combo.grid(row=0, column=10, padx=4, pady=6)
        self.pay_combo.set(config.PAYMENT_METHODS[0])
        self.cust_name_e = field("Customer Name:", 1, 0, 14)
        self.cust_phone_e = field("Phone:", 1, 2, 14, columnspan=2, sticky="w")

        self.prod_id_e.bind("<Return>", self.add_to_cart)
        self.prod_qty_e.bind("<Return>", self.add_to_cart)
        self.disc_e.bind("<KeyRelease>", self.update_summary)
        self.tax_e.bind("<KeyRelease>", self.update_summary)

    def _build_cart(self, parent) -> None:
        self.cart_frame = tk.LabelFrame(parent, text=" Current Cart (0 items) ", bg=WHITE, fg=DARK,
                                        font=(FONT, 10, "bold"), bd=1, relief="solid")
        self.cart_frame.pack(fill="both", expand=True, padx=5, pady=5)
        box, self.tree_cart = make_tree(self.cart_frame, ("Product", "Unit Price", "Qty", "Line Total"),
                                        (420, 140, 100, 160), height=10)
        box.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree_cart.bind("<Delete>", lambda _e: self.remove_selected())

        bottom = tk.Frame(self.cart_frame, bg=WHITE)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self.lbl_summary = tk.Label(bottom, text="", bg=WHITE, fg=DARK, font=(FONT, 11, "bold"))
        self.lbl_summary.pack(side="left", padx=5)
        CanvasButton(bottom, "Process Bill", self.process_sale, "#198754", width=120, height=34).pack(
            side="right", padx=5)
        CanvasButton(bottom, "Clear Cart", self.clear_cart, "#6c757d", width=100, height=34).pack(
            side="right", padx=5)
        CanvasButton(bottom, "Remove Selected", self.remove_selected, "#dc3545", width=130, height=34).pack(
            side="right", padx=5)

    # ------------------------------------------------------------------ cart
    def focus_barcode(self) -> None:
        self.prod_id_e.focus_set()

    def _items(self) -> list[tuple[int, int]]:
        return [(line["product_id"], line["qty"]) for line in self.cart]

    def render_cart(self) -> None:
        for row in self.tree_cart.get_children():
            self.tree_cart.delete(row)
        for index, line in enumerate(self.cart):
            total = line["price_cents"] * line["qty"]
            self.tree_cart.insert("", "end", iid=str(index),
                                  values=(line["name"], fmt(line["price_cents"]), line["qty"], fmt(total)))
        self.cart_frame.config(text=f" Current Cart ({len(self.cart)} items) ")
        self.update_summary()

    def update_summary(self, _event=None) -> None:
        if not self.cart:
            zero = fmt(0)
            self.lbl_summary.config(
                text=f"Subtotal: {zero}    Discount: -{zero}    Tax: +{zero}    TOTAL: {zero}", fg=DARK)
            return
        try:
            quote = sales_service.quote(self.ctx.conn, self._items(), self.disc_e.get(), self.tax_e.get())
        except POSError:
            self.lbl_summary.config(text="Check the discount / tax values", fg="#dc3545")
            return
        self.lbl_summary.config(
            text=(f"Subtotal: {fmt(quote.subtotal_cents)}    Discount: -{fmt(quote.discount_cents)}    "
                  f"Tax ({quote.tax_rate:f}%): +{fmt(quote.tax_cents)}    TOTAL: {fmt(quote.total_cents)}"),
            fg=DARK)

    @handle_errors
    def add_to_cart(self, _event=None) -> None:
        key = self.prod_id_e.get().strip()
        if not key:
            messagebox.showwarning("Warning", "Product Barcode or ID required!")
            return
        try:
            qty = int(self.prod_qty_e.get().strip() or "1")
            if qty <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Quantity must be a positive whole number!")
            return

        product = inventory_service.find_for_sale(self.ctx.conn, self.ctx.session, key)
        line = next((l for l in self.cart if l["product_id"] == product["id"]), None)
        already = line["qty"] if line else 0
        if product["stock"] < already + qty:
            messagebox.showerror("Stock Error", f"Insufficient stock for {product['name']}!\n"
                                                f"Available: {product['stock']}  |  Already in cart: {already}")
            return
        if line:
            line["qty"] += qty
        else:
            self.cart.append({"product_id": product["id"], "name": product["name"],
                              "price_cents": product["price_cents"], "qty": qty})
        self.render_cart()
        self.prod_id_e.delete(0, tk.END)
        self.prod_qty_e.delete(0, tk.END)
        self.prod_id_e.focus_set()

    def remove_selected(self) -> None:
        selected = self.tree_cart.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select a cart row to remove!")
            return
        del self.cart[int(selected[0])]
        self.render_cart()

    def clear_cart(self) -> None:
        if self.cart and messagebox.askyesno("Confirm", "Remove all items from the cart?"):
            self.cart.clear()
            self.render_cart()

    # ------------------------------------------------------------------ checkout
    @handle_errors
    def process_sale(self) -> None:
        if not self.cart:
            messagebox.showwarning("Warning", "Cart is empty! Add products to cart first.")
            return
        invoice = sales_service.checkout(
            self.ctx.conn, self.ctx.session, self._items(), self.disc_e.get(), self.tax_e.get(),
            self.pay_combo.get(), self.cust_name_e.get(), self.cust_phone_e.get())
        self.cart.clear()
        for entry in (self.cust_name_e, self.cust_phone_e):
            entry.delete(0, tk.END)
        self.disc_e.delete(0, tk.END)
        self.disc_e.insert(0, "0")
        self.render_cart()
        self.ctx.refresh()
        show_receipt(self.ctx, invoice)

"""POS Billing tab: customer lookup + loyalty points, credit (baki), live cart, cash received / change due, checkout."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import config
from ..errors import POSError, ValidationError
from ..money import fmt, plain, to_cents
from ..services import (credit_service, customer_service, inventory_service, sales_service, settings_service,
                        till_service)
from .context import AppContext
from .dialogs.customer_dialogs import open_customer_history
from .dialogs.ledger_dialogs import open_customer_ledger
from .dialogs.sales_dialogs import open_invoices, show_receipt
from .widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, handle_errors,
                      make_tree)

MUTED = "#6c757d"
LOOKUP_HINT = "Enter a phone number and press Lookup to see loyalty points."


class PosTab:
    def __init__(self, parent: tk.Frame, ctx: AppContext) -> None:
        self.ctx = ctx
        self.cart: list[dict] = []
        self._total_cents: int | None = None
        self._mode = "cash"
        self._build_billing(parent)
        self._build_cart(parent)
        self.render_cart()

    def _build_billing(self, parent) -> None:
        frame = tk.LabelFrame(parent, text=" Customer Billing & POS Multi-Item Cart ", bg=WHITE, fg="#0d6efd",
                              font=(FONT, 10, "bold"), bd=1, relief="solid")
        frame.pack(fill="x", padx=5, pady=5)
        labels: dict[tuple[int, int], tk.Label] = {}

        def field(caption: str, row: int, column: int, width: int, **grid):
            label = tk.Label(frame, text=caption, **LABEL_STYLE)
            label.grid(row=row, column=column, padx=4, pady=6, sticky="e")
            labels[(row, column)] = label
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
        self.pay_combo = ttk.Combobox(frame, values=list(credit_service.sale_methods(self.ctx.session)), width=14,
                                      font=(FONT, 9), state="readonly")
        self.pay_combo.grid(row=0, column=10, padx=4, pady=6)
        self.pay_combo.set(config.PAYMENT_METHODS[0])

        self.cust_name_e = field("Customer Name:", 1, 0, 14)
        self.cust_phone_e = field("Phone:", 1, 2, 14)
        lookup = CanvasButton(frame, "🔎 Lookup", self.lookup_customer, "#0d6efd", width=90, height=28)
        lookup.grid(row=1, column=4, padx=6, pady=6)
        lookup.set_enabled(self.ctx.can("customers.lookup"))
        self.received_e = field(f"Received ({config.CURRENCY_SYMBOL}):", 1, 5, 9)
        self.lbl_received = labels[(1, 5)]
        self.lbl_change = tk.Label(frame, text="", bg=WHITE, fg=MUTED, font=(FONT, 10, "bold"))
        self.lbl_change.grid(row=1, column=7, columnspan=4, padx=6, sticky="w")

        self.points_e = field("Use points:", 2, 0, 8)
        self.lbl_loyalty = tk.Label(frame, text=LOOKUP_HINT, bg=WHITE, fg=MUTED, font=(FONT, 10))
        self.lbl_loyalty.grid(row=2, column=2, columnspan=7, padx=6, sticky="w")
        history = CanvasButton(frame, "📜 History", self.show_history, "#6c757d", width=90, height=28)
        history.grid(row=2, column=9, padx=4, pady=6)
        history.set_enabled(self.ctx.can("customers.lookup"))
        ledger = CanvasButton(frame, "📒 Ledger", self.show_ledger, "#b02a37", width=90, height=28)
        ledger.grid(row=2, column=10, padx=4, pady=6)
        ledger.set_enabled(self.ctx.can(credit_service.PERM_VIEW))

        self.prod_id_e.bind("<Return>", self.add_to_cart)
        self.prod_qty_e.bind("<Return>", self.add_to_cart)
        self.disc_e.bind("<KeyRelease>", self.update_summary)
        self.tax_e.bind("<KeyRelease>", self.update_summary)
        self.points_e.bind("<KeyRelease>", self.update_summary)
        self.received_e.bind("<KeyRelease>", lambda _e: self._update_change())
        self.cust_phone_e.bind("<Return>", self.lookup_customer)
        self.cust_phone_e.bind("<KeyRelease>", self._phone_changed)
        self.pay_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_payment_change())

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
        invoices_button = CanvasButton(bottom, "🧾 Recent Invoices", lambda: open_invoices(self.ctx), "#0d6efd",
                                       width=150, height=34)
        invoices_button.pack(side="right", padx=5)
        invoices_button.set_enabled(self.ctx.can("invoices.view"))

    def _phone_changed(self, event=None) -> None:
        if getattr(event, "keysym", "") in ("Return", "KP_Enter"):
            return
        self.lbl_loyalty.config(text=LOOKUP_HINT, fg=MUTED)

    def _points(self) -> int:
        text = self.points_e.get().strip()
        if not text:
            return 0
        try:
            points = int(text)
        except ValueError:
            raise ValidationError("Points must be a whole number (0 or more)!") from None
        if points < 0:
            raise ValidationError("Points must be a whole number (0 or more)!")
        return points

    def _effective_discount(self) -> str:
        """Typed discount plus the value of any loyalty points (used for the live preview)."""
        points = self._points()
        text = self.disc_e.get()
        if not points:
            return text
        return plain(to_cents(text or 0) + customer_service.points_value(points))

    @handle_errors
    def lookup_customer(self, _event=None) -> None:
        info = customer_service.find_by_phone(self.ctx.conn, self.ctx.session, self.cust_phone_e.get())
        if info is None:
            self.lbl_loyalty.config(text="New customer: no purchase history yet.", fg=MUTED)
            return
        if not self.cust_name_e.get().strip():
            self.cust_name_e.insert(0, info.name)
        value = fmt(customer_service.points_value(info.points))
        balance = credit_service.balance_of_phone(self.ctx.conn, info.phone) if self.ctx.can(
            credit_service.PERM_VIEW) else 0
        account = ""
        if balance > 0:
            account = f"  |  OWES {fmt(balance)}"
        elif balance < 0:
            account = f"  |  credit {fmt(-balance)}"
        self.lbl_loyalty.config(
            text=f"{info.name}: {info.points} points (worth {value})  |  spent {fmt(info.spent_cents)}  |  "
                 f"{info.visits} visit(s){account}", fg="#dc3545" if balance > 0 else "#198754")

    def show_history(self) -> None:
        open_customer_history(self.ctx, self.cust_phone_e.get())

    def show_ledger(self) -> None:
        open_customer_ledger(self.ctx, self.cust_phone_e.get())

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
        self._total_cents = None
        if not self.cart:
            zero = fmt(0)
            self.lbl_summary.config(
                text=f"Subtotal: {zero}    Discount: -{zero}    Tax: +{zero}    TOTAL: {zero}", fg=DARK)
            self._update_change()
            return
        try:
            points = self._points()
            quote = sales_service.quote(self.ctx.conn, self._items(), self._effective_discount(), self.tax_e.get())
        except (POSError, ValueError):
            self.lbl_summary.config(text="Check the discount / tax / points values", fg="#dc3545")
            self._update_change()
            return
        self._total_cents = quote.total_cents
        used = f" (incl. {points} pts)" if points else ""
        self.lbl_summary.config(
            text=(f"Subtotal: {fmt(quote.subtotal_cents)}    Discount: -{fmt(quote.discount_cents)}{used}    "
                  f"Tax ({quote.tax_rate:f}%): +{fmt(quote.tax_cents)}    TOTAL: {fmt(quote.total_cents)}"),
            fg=DARK)
        self._update_change()

    def _cash(self) -> bool:
        return self.pay_combo.get() == till_service.cash_method()

    def _credit(self) -> bool:
        return self.pay_combo.get() == credit_service.CREDIT_METHOD

    def _on_payment_change(self) -> None:
        cash, credit = self._cash(), self._credit()
        mode = "credit" if credit else ("cash" if cash else "other")
        self.received_e.config(state="normal")
        if mode != self._mode or mode == "other":
            self.received_e.delete(0, tk.END)
        self._mode = mode
        self.received_e.config(state="normal" if (cash or credit) else "disabled")
        self.lbl_received.config(text=f"{'Paid now' if credit else 'Received'} ({config.CURRENCY_SYMBOL}):")
        self._update_change()

    def _update_credit_hint(self) -> None:
        if self._total_cents is None:
            self.lbl_change.config(text="On credit: add items first", fg=MUTED)
            return
        text = self.received_e.get().strip()
        try:
            paid = to_cents(text) if text else 0
        except ValueError:
            self.lbl_change.config(text="Paid-now amount is not a number", fg="#dc3545")
            return
        if paid < 0 or paid > self._total_cents:
            self.lbl_change.config(text=f"Paid now must be between {fmt(0)} and {fmt(self._total_cents)}", fg="#dc3545")
            return
        due = self._total_cents - paid
        self.lbl_change.config(text=f"On credit: {fmt(due)}", fg="#dc3545" if due else "#198754")

    def _update_change(self) -> None:
        if self._credit():
            self._update_credit_hint()
            return
        if not self._cash():
            self.lbl_change.config(text="Change: not needed (not cash)", fg=MUTED)
            return
        text = self.received_e.get().strip()
        if not text or self._total_cents is None:
            self.lbl_change.config(text="Change: enter the amount received", fg=MUTED)
            return
        try:
            received = to_cents(text)
        except ValueError:
            self.lbl_change.config(text="Amount received is not a number", fg="#dc3545")
            return
        difference = received - self._total_cents
        if difference >= 0:
            self.lbl_change.config(text=f"Change due: {fmt(difference)}", fg="#198754")
        else:
            self.lbl_change.config(text=f"Short by {fmt(-difference)}", fg="#dc3545")

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

    @handle_errors
    def process_sale(self) -> None:
        if not self.cart:
            messagebox.showwarning("Warning", "Cart is empty! Add products to cart first.")
            return
        uses_received = self._cash() or self._credit()
        invoice = till_service.checkout_with_payment(
            self.ctx.conn, self.ctx.session, self._items(), self.disc_e.get(), self.tax_e.get(),
            self.pay_combo.get(), self.cust_name_e.get(), self.cust_phone_e.get(),
            tendered=self.received_e.get() if uses_received else None, redeem_points=self._points())
        self.cart.clear()
        for entry in (self.cust_name_e, self.cust_phone_e, self.points_e):
            entry.delete(0, tk.END)
        self.lbl_loyalty.config(text=LOOKUP_HINT, fg=MUTED)
        self.disc_e.delete(0, tk.END)
        self.disc_e.insert(0, "0")
        if uses_received:
            self.received_e.delete(0, tk.END)
        self.render_cart()
        self.ctx.refresh()
        show_receipt(self.ctx, invoice)

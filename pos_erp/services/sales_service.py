"""Pricing and checkout. The UI sends product IDs + quantities only; prices always come from the DB."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .. import config, timeutil
from ..db.connection import transaction
from ..errors import NotFoundError, ValidationError
from ..money import allocate, fmt, parse_rate, percent_of, to_cents
from ..permissions import Session
from . import audit, stock_service


@dataclass(frozen=True)
class QuoteLine:
    product_id: int
    name: str
    quantity: int
    unit_price_cents: int
    unit_cost_cents: int
    subtotal_cents: int
    discount_cents: int
    tax_cents: int

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents - self.discount_cents + self.tax_cents


@dataclass(frozen=True)
class Quote:
    lines: tuple[QuoteLine, ...]
    subtotal_cents: int
    discount_cents: int
    tax_rate: Decimal
    tax_cents: int

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents - self.discount_cents + self.tax_cents


@dataclass(frozen=True)
class InvoiceItem:
    product_id: int
    product_name: str
    quantity: int
    unit_price_cents: int
    unit_cost_cents: int
    line_subtotal_cents: int
    line_discount_cents: int
    line_tax_cents: int
    line_total_cents: int


@dataclass(frozen=True)
class Invoice:
    id: int
    invoice_no: str
    customer_name: str
    customer_phone: str
    subtotal_cents: int
    discount_cents: int
    tax_rate: Decimal
    tax_cents: int
    total_cents: int
    payment_method: str
    cashier: str
    created_at: str
    items: tuple[InvoiceItem, ...]


def _positive_int(value, label: str = "Quantity") -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a positive whole number!") from None
    if number <= 0:
        raise ValidationError(f"{label} must be a positive whole number!")
    return number


def quote(conn: sqlite3.Connection, items: Iterable[tuple[int, int]], discount="0", tax_rate="0") -> Quote:
    """Price a cart. Discount is a money amount for the whole bill, tax_rate a percentage."""
    merged: dict[int, int] = {}
    for product_id, qty in items:
        merged[product_id] = merged.get(product_id, 0) + _positive_int(qty)
    if not merged:
        raise ValidationError("Cart is empty! Add products to cart first.")

    products = []
    for product_id, qty in merged.items():
        row = conn.execute("SELECT id, name, price_cents, cost_cents FROM products WHERE id = ? AND is_active = 1",
                           (product_id,)).fetchone()
        if row is None:
            raise NotFoundError("Product not found!")
        products.append((row, qty))
    subtotals = [row["price_cents"] * qty for row, qty in products]
    subtotal = sum(subtotals)

    try:
        discount_cents = to_cents(discount or 0)
        rate = parse_rate(tax_rate or 0)
    except ValueError:
        raise ValidationError("Invalid Discount or Tax format!") from None
    if discount_cents < 0 or discount_cents > subtotal:
        raise ValidationError("Discount cannot be negative or exceed the subtotal!")

    line_discounts = allocate(discount_cents, subtotals)
    lines = []
    for (row, qty), sub, disc in zip(products, subtotals, line_discounts):
        lines.append(QuoteLine(row["id"], row["name"], qty, row["price_cents"], row["cost_cents"],
                               sub, disc, percent_of(sub - disc, rate)))
    return Quote(tuple(lines), subtotal, discount_cents, rate, sum(l.tax_cents for l in lines))


def _next_invoice_no(conn: sqlite3.Connection, moment) -> str:
    prefix = f"INV-{moment:%Y%m%d}-"
    last = conn.execute("SELECT invoice_no FROM invoices WHERE invoice_no LIKE ? ORDER BY invoice_no DESC LIMIT 1",
                        (prefix + "%",)).fetchone()
    sequence = int(last["invoice_no"][len(prefix):]) + 1 if last else 1
    return f"{prefix}{sequence:04d}"


def _touch_customer(conn: sqlite3.Connection, name: str, phone: str, total_cents: int) -> int:
    points = total_cents // config.LOYALTY_CENTS_PER_POINT
    row = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
    if row is None:
        return conn.execute("INSERT INTO customers (name, phone, loyalty_points, total_spent_cents) VALUES (?,?,?,?)",
                            (name, phone, points, total_cents)).lastrowid
    # An existing customer keeps their stored name (a different cashier typing another name can't overwrite it).
    conn.execute("UPDATE customers SET loyalty_points = loyalty_points + ?, total_spent_cents = total_spent_cents + ? "
                 "WHERE id = ?", (points, total_cents, row["id"]))
    return row["id"]


def checkout(conn: sqlite3.Connection, session: Session, items: Iterable[tuple[int, int]], discount="0",
             tax_rate="0", payment_method: str = "Cash", customer_name: str = "",
             customer_phone: str = "") -> Invoice:
    """Create an invoice atomically: stock check, ledger, invoice, customer and audit succeed or fail together."""
    session.require("sales.create")
    if payment_method not in config.PAYMENT_METHODS:
        raise ValidationError("Invalid payment method!")
    name = customer_name.strip() or "Walk-in Customer"
    phone = customer_phone.strip() or "N/A"

    with transaction(conn):
        priced = quote(conn, list(items), discount, tax_rate)
        created = timeutil.now()
        invoice_no = _next_invoice_no(conn, created)
        customer_id = _touch_customer(conn, name, phone, priced.total_cents) if phone != "N/A" else None
        invoice_id = conn.execute(
            """INSERT INTO invoices (invoice_no, customer_id, customer_name, customer_phone, subtotal_cents,
                                     discount_cents, tax_rate, tax_cents, total_cents, payment_method, cashier,
                                     created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (invoice_no, customer_id, name, phone, priced.subtotal_cents, priced.discount_cents,
             format(priced.tax_rate.normalize(), "f"), priced.tax_cents, priced.total_cents, payment_method,
             session.username, timeutil.stamp(created))).lastrowid
        for line in priced.lines:
            conn.execute(
                """INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price_cents,
                                              unit_cost_cents, line_subtotal_cents, line_discount_cents,
                                              line_tax_cents, line_total_cents) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (invoice_id, line.product_id, line.name, line.quantity, line.unit_price_cents, line.unit_cost_cents,
                 line.subtotal_cents, line.discount_cents, line.tax_cents, line.total_cents))
            stock_service.apply_stock_change(conn, line.product_id, -line.quantity, "SALE", session.username,
                                             ref_type="invoice", ref_id=invoice_id, note=invoice_no)
        audit.log(conn, session.username, f"Processed Sale Invoice: {invoice_no} ({fmt(priced.total_cents)})")
    return get_invoice(conn, invoice_no)


def get_invoice(conn: sqlite3.Connection, invoice_no: str) -> Invoice:
    row = conn.execute("SELECT * FROM invoices WHERE invoice_no = ?", (invoice_no.strip(),)).fetchone()
    if row is None:
        raise NotFoundError("Invoice not found!")
    items = conn.execute("SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY id", (row["id"],)).fetchall()
    return Invoice(
        row["id"], row["invoice_no"], row["customer_name"], row["customer_phone"], row["subtotal_cents"],
        row["discount_cents"], Decimal(row["tax_rate"]), row["tax_cents"], row["total_cents"],
        row["payment_method"], row["cashier"], row["created_at"],
        tuple(InvoiceItem(i["product_id"], i["product_name"], i["quantity"], i["unit_price_cents"],
                          i["unit_cost_cents"], i["line_subtotal_cents"], i["line_discount_cents"],
                          i["line_tax_cents"], i["line_total_cents"]) for i in items))
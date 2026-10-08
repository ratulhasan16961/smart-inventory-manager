"""Till operations beyond a plain checkout: cash tendering, voiding, invoice lookup."""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Iterable

from .. import config, dayrange, timeutil
from ..db.connection import transaction
from ..errors import NotFoundError, ValidationError
from ..money import fmt, to_cents
from ..permissions import Session
from . import audit, sales_service, stock_service


def cash_method() -> str:
    return config.PAYMENT_METHODS[0]


def payment_amounts(total_cents: int, payment_method: str, tendered) -> tuple[int, int]:
    """Returns (amount received, change). A blank amount, or any non-cash method, means exact payment."""
    if payment_method != cash_method() or tendered is None or not str(tendered).strip():
        return total_cents, 0
    try:
        received = to_cents(tendered)
    except ValueError:
        raise ValidationError("Amount received is not a valid number!") from None
    if received < total_cents:
        raise ValidationError(f"Amount received ({fmt(received)}) is less than the total ({fmt(total_cents)}).")
    return received, received - total_cents


def checkout_with_payment(conn: sqlite3.Connection, session: Session, items: Iterable[tuple[int, int]],
                          discount="0", tax_rate="0", payment_method: str | None = None,
                          customer_name: str = "", customer_phone: str = "", tendered=None):
    """Normal checkout plus the amount received / change, all in ONE transaction:
    if the cash is too little, the sale (stock, invoice, customer) is rolled back as well."""
    payment_method = payment_method or cash_method()
    with transaction(conn):
        invoice = sales_service.checkout(
            conn, session, items, discount=discount, tax_rate=tax_rate, payment_method=payment_method,
            customer_name=customer_name, customer_phone=customer_phone)
        received, change = payment_amounts(invoice.total_cents, payment_method, tendered)
        conn.execute("UPDATE invoices SET tendered_cents = ?, change_cents = ? WHERE id = ?",
                     (received, change, invoice.id))
    return invoice


def invoice_meta(conn: sqlite3.Connection, invoice_no: str) -> sqlite3.Row:
    row = conn.execute("SELECT tendered_cents, change_cents, status, voided_at, voided_by, void_reason "
                       "FROM invoices WHERE invoice_no = ?", (invoice_no.strip(),)).fetchone()
    if row is None:
        raise NotFoundError("Invoice not found!")
    return row


def get_invoice_with_payment(conn: sqlite3.Connection, session: Session, invoice_no: str):
    session.require("invoices.view")
    return sales_service.get_invoice(conn, invoice_no), invoice_meta(conn, invoice_no)


def _like(text: str) -> str:
    return "%" + text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_invoices(conn: sqlite3.Connection, session: Session, query: str = "", date_from: str = "",
                  date_to: str = "", limit: int = 300) -> list[sqlite3.Row]:
    session.require("invoices.view")
    sql = ("SELECT invoice_no, created_at, customer_name, customer_phone, total_cents, payment_method, cashier, "
           "status FROM invoices WHERE 1 = 1")
    params: list = []
    if query.strip():
        sql += (" AND (invoice_no LIKE ? ESCAPE '\\' OR customer_name LIKE ? ESCAPE '\\' "
                "OR customer_phone LIKE ? ESCAPE '\\')")
        params += [_like(query)] * 3
    if date_from.strip():
        sql += " AND created_at >= ?"
        params.append(f"{dayrange.parse_day(date_from):%Y-%m-%d} 00:00:00")
    if date_to.strip():
        sql += " AND created_at < ?"
        params.append(f"{dayrange.parse_day(date_to) + timedelta(days=1):%Y-%m-%d} 00:00:00")
    return conn.execute(sql + " ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()


def void_invoice(conn: sqlite3.Connection, session: Session, invoice_no: str, reason: str) -> None:
    """Cancel a sale: stock goes back, the customer's totals are reversed, reports ignore the invoice.
    Not allowed once any item has been returned (use Return / Refund for the rest)."""
    session.require("sales.void")
    reason = reason.strip()
    if len(reason) < 3:
        raise ValidationError("Please give a reason for voiding (at least 3 characters).")
    with transaction(conn):
        invoice = conn.execute("SELECT * FROM invoices WHERE invoice_no = ?", (invoice_no.strip(),)).fetchone()
        if invoice is None:
            raise NotFoundError("Invoice not found!")
        if invoice["status"] == "Voided":
            raise ValidationError("This invoice is already voided.")
        returned = conn.execute(
            "SELECT COUNT(*) FROM sales_returns r JOIN invoice_items ii ON ii.id = r.invoice_item_id "
            "WHERE ii.invoice_id = ?", (invoice["id"],)).fetchone()[0]
        if returned:
            raise ValidationError("This invoice already has returns, so it cannot be voided. "
                                  "Process the remaining items through Return / Refund.")
        items = conn.execute("SELECT product_id, quantity FROM invoice_items WHERE invoice_id = ?",
                             (invoice["id"],)).fetchall()
        for item in items:
            stock_service.apply_stock_change(
                conn, item["product_id"], item["quantity"], "RETURN", session.username,
                ref_type="void", ref_id=invoice["id"], note=f"VOID {invoice['invoice_no']}: {reason}")
        if invoice["customer_phone"] != "N/A":
            conn.execute(
                "UPDATE customers SET total_spent_cents = MAX(total_spent_cents - ?, 0), "
                "loyalty_points = MAX(loyalty_points - ?, 0) WHERE phone = ?",
                (invoice["total_cents"], invoice["total_cents"] // config.LOYALTY_CENTS_PER_POINT,
                 invoice["customer_phone"]))
        conn.execute("UPDATE invoices SET status = 'Voided', voided_at = ?, voided_by = ?, void_reason = ? "
                     "WHERE id = ?", (timeutil.stamp(), session.username, reason, invoice["id"]))
        audit.log(conn, session.username,
                  f"Voided invoice {invoice['invoice_no']} ({fmt(invoice['total_cents'])}): {reason}")

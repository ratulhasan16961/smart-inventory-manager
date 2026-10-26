"""Till operations beyond a plain checkout: cash tendering, credit (baki) sales, loyalty points, voiding, lookup."""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Iterable

from .. import config, dayrange, timeutil
from ..db.connection import transaction
from ..errors import NotFoundError, ValidationError
from ..money import fmt, plain, to_cents
from ..permissions import Session
from . import audit, credit_service, sales_service, stock_service


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


def _whole_points(value) -> int:
    if value is None or str(value).strip() == "":
        return 0
    try:
        points = int(str(value).strip())
    except ValueError:
        raise ValidationError("Points must be a whole number (0 or more)!") from None
    if points < 0:
        raise ValidationError("Points must be a whole number (0 or more)!")
    return points


def _redeemable_value(conn: sqlite3.Connection, items: list, discount, phone: str, points: int) -> int:
    """Checks the customer really has the points and that points + discount do not exceed the bill.
    Returns the value of the points in cents."""
    if phone in ("", "N/A"):
        raise ValidationError("Loyalty points can only be used when a customer phone number is entered.")
    row = conn.execute("SELECT loyalty_points FROM customers WHERE phone = ?", (phone,)).fetchone()
    if row is None:
        raise ValidationError("This customer has no loyalty points yet.")
    if points > row["loyalty_points"]:
        raise ValidationError(f"The customer only has {row['loyalty_points']} point(s).")
    value = points * config.LOYALTY_POINT_VALUE_CENTS
    subtotal = sales_service.quote(conn, items, "0", "0").subtotal_cents
    try:
        manual = to_cents(discount or 0)
    except ValueError:
        raise ValidationError("Invalid Discount or Tax format!") from None
    if manual + value > subtotal:
        raise ValidationError(f"Points worth {fmt(value)} plus the discount exceed the bill ({fmt(subtotal)}). "
                              "Use fewer points.")
    return value


def checkout_with_payment(conn: sqlite3.Connection, session: Session, items: Iterable[tuple[int, int]],
                          discount="0", tax_rate="0", payment_method: str | None = None,
                          customer_name: str = "", customer_phone: str = "", tendered=None,
                          redeem_points=0, payment_ref: str = ""):
    """Normal checkout plus the amount received / change and optional loyalty redemption, all in ONE
    transaction: if the cash is too little or the points are not valid, the whole sale is rolled back.
    Redeemed points become part of the invoice discount (and are recorded separately).

    Credit (baki): with payment_method == credit_service.CREDIT_METHOD the customer's phone number is required,
    `tendered` is what the customer pays now (blank = nothing), and the rest goes on the customer's account
    (refused when it would pass the credit limit, and the whole sale rolls back).

    payment_ref is the transaction ID of a card / bKash / Nagad payment (not for cash or credit)."""
    payment_method = payment_method or cash_method()
    credit = payment_method == credit_service.CREDIT_METHOD
    ref = (payment_ref or "").strip()
    if len(ref) > 40:
        raise ValidationError("The payment reference is too long (max 40 characters).")
    if ref and (credit or payment_method == cash_method()):
        raise ValidationError("A payment reference (transaction ID) only applies to card and mobile payments.")
    if credit:
        session.require(credit_service.PERM_CREDIT_SALE)
    items = list(items)
    points = _whole_points(redeem_points)
    phone = customer_phone.strip()
    if credit and phone in ("", "N/A"):
        raise ValidationError("Credit sales need the customer's phone number.")
    with transaction(conn):
        value = 0
        if points:
            value = _redeemable_value(conn, items, discount, phone, points)
            discount = plain(to_cents(discount or 0) + value)
        invoice = sales_service.checkout(
            conn, session, items, discount=discount, tax_rate=tax_rate,
            payment_method=cash_method() if credit else payment_method,
            customer_name=customer_name, customer_phone=customer_phone)
        if credit:
            credit_service.convert_to_credit(conn, session, invoice.invoice_no, tendered)
            conn.execute("UPDATE invoices SET points_redeemed = ?, points_value_cents = ? WHERE id = ?",
                         (points, value, invoice.id))
        else:
            received, change = payment_amounts(invoice.total_cents, payment_method, tendered)
            conn.execute("UPDATE invoices SET tendered_cents = ?, change_cents = ?, points_redeemed = ?, "
                         "points_value_cents = ?, payment_ref = ? WHERE id = ?",
                         (received, change, points, value, ref, invoice.id))
        if points:
            conn.execute("UPDATE customers SET loyalty_points = loyalty_points - ? WHERE phone = ?", (points, phone))
            audit.log(conn, session.username,
                      f"Redeemed {points} loyalty point(s) on {invoice.invoice_no} (-{fmt(value)})")
        if credit:
            invoice = sales_service.get_invoice(conn, invoice.invoice_no)
    return invoice


def invoice_meta(conn: sqlite3.Connection, invoice_no: str) -> sqlite3.Row:
    row = conn.execute("SELECT tendered_cents, change_cents, status, voided_at, voided_by, void_reason, "
                       "points_redeemed, points_value_cents, payment_ref FROM invoices WHERE invoice_no = ?",
                       (invoice_no.strip(),)).fetchone()
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
    """`query` matches the invoice number, customer name, phone or the payment reference (transaction ID)."""
    session.require("invoices.view")
    sql = ("SELECT invoice_no, created_at, customer_name, customer_phone, total_cents, payment_method, cashier, "
           "status FROM invoices WHERE 1 = 1")
    params: list = []
    if query.strip():
        sql += (" AND (invoice_no LIKE ? ESCAPE '\\' OR customer_name LIKE ? ESCAPE '\\' "
                "OR customer_phone LIKE ? ESCAPE '\\' OR payment_ref LIKE ? ESCAPE '\\')")
        params += [_like(query)] * 4
    if date_from.strip():
        sql += " AND created_at >= ?"
        params.append(f"{dayrange.parse_day(date_from):%Y-%m-%d} 00:00:00")
    if date_to.strip():
        sql += " AND created_at < ?"
        params.append(f"{dayrange.parse_day(date_to) + timedelta(days=1):%Y-%m-%d} 00:00:00")
    return conn.execute(sql + " ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()


def void_invoice(conn: sqlite3.Connection, session: Session, invoice_no: str, reason: str) -> None:
    """Cancel a sale: stock goes back, the customer's totals are reversed (points earned are removed, points
    spent are given back), reports ignore the invoice. Not allowed once any item has been returned.
    A credit sale's charge is cancelled; money the customer paid at the sale stays on the account as credit."""
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
            if invoice["points_redeemed"]:
                conn.execute("UPDATE customers SET loyalty_points = loyalty_points + ? WHERE phone = ?",
                             (invoice["points_redeemed"], invoice["customer_phone"]))
        credit_service.on_void(conn, session, invoice["invoice_no"], reason)
        conn.execute("UPDATE invoices SET status = 'Voided', voided_at = ?, voided_by = ?, void_reason = ? "
                     "WHERE id = ?", (timeutil.stamp(), session.username, reason, invoice["id"]))
        audit.log(conn, session.username,
                  f"Voided invoice {invoice['invoice_no']} ({fmt(invoice['total_cents'])}): {reason}")

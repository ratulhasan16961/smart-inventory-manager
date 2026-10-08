"""Returns and refunds with hard limits: never refund more than was sold or paid."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import config, timeutil
from ..db.connection import transaction
from ..errors import NotFoundError, ValidationError
from ..money import fmt
from ..permissions import Session
from . import audit, stock_service


@dataclass(frozen=True)
class ReturnResult:
    return_id: int
    refund_cents: int
    restocked: bool


def invoice_lines(conn: sqlite3.Connection, session: Session, invoice_no: str) -> list[sqlite3.Row]:
    """Lines of an invoice with sold / already returned / still returnable quantities."""
    session.require("returns.process")
    rows = conn.execute(
        """SELECT ii.product_id, ii.product_name, ii.quantity AS sold,
                  COALESCE((SELECT SUM(r.quantity) FROM sales_returns r WHERE r.invoice_item_id = ii.id), 0) AS returned
           FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
           WHERE i.invoice_no = ? ORDER BY ii.id""", (invoice_no.strip(),)).fetchall()
    if not rows:
        raise NotFoundError("Invoice not found!")
    return rows


def process_return(conn: sqlite3.Connection, session: Session, invoice_no: str, product_id: int, quantity: int,
                   reason: str = "", restock: bool = True) -> ReturnResult:
    session.require("returns.process")
    try:
        quantity = int(quantity)
        product_id = int(product_id)
    except (TypeError, ValueError):
        raise ValidationError("Product ID and Return Qty must be positive whole numbers!") from None
    if quantity <= 0:
        raise ValidationError("Product ID and Return Qty must be positive whole numbers!")

    with transaction(conn):
        item = conn.execute(
            """SELECT ii.id, ii.quantity, ii.line_total_cents, i.customer_phone
               FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
               WHERE i.invoice_no = ? AND ii.product_id = ?""", (invoice_no.strip(), product_id)).fetchone()
        if item is None:
            raise NotFoundError("Matching sale record not found!")
        done = conn.execute("SELECT COALESCE(SUM(quantity),0) AS qty, COALESCE(SUM(refund_cents),0) AS cents "
                            "FROM sales_returns WHERE invoice_item_id = ?", (item["id"],)).fetchone()
        remaining = item["quantity"] - done["qty"]
        if quantity > remaining:
            raise ValidationError(f"Only {remaining} unit(s) can still be returned for this item "
                                  f"(sold {item['quantity']}, already returned {done['qty']}).")
        # Cumulative proportional refund: returning everything refunds exactly the line total.
        cumulative = (item["line_total_cents"] * (done["qty"] + quantity) * 2 + item["quantity"]) // (2 * item["quantity"])
        refund = cumulative - done["cents"]

        return_id = conn.execute(
            """INSERT INTO sales_returns (invoice_item_id, quantity, refund_cents, reason, restocked, processed_by,
                                          created_at) VALUES (?,?,?,?,?,?,?)""",
            (item["id"], quantity, refund, reason.strip(), 1 if restock else 0, session.username,
             timeutil.stamp())).lastrowid
        if restock:
            stock_service.apply_stock_change(conn, product_id, quantity, "RETURN", session.username,
                                             ref_type="return", ref_id=return_id, note=invoice_no.strip())
        if item["customer_phone"] != "N/A":
            conn.execute(
                """UPDATE customers SET total_spent_cents = MAX(total_spent_cents - ?, 0),
                          loyalty_points = MAX(loyalty_points - ?, 0) WHERE phone = ?""",
                (refund, refund // config.LOYALTY_CENTS_PER_POINT, item["customer_phone"]))
        audit.log(conn, session.username,
                  f"Processed Refund for Invoice: {invoice_no.strip()}, Product ID: {product_id}, "
                  f"Qty: {quantity}, Amount: {fmt(refund)}" + ("" if restock else " (not restocked)"))
    return ReturnResult(return_id, refund, restock)

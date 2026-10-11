"""Customer lookup for the till: loyalty points, spending and purchase history (read-only)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import config
from ..errors import ValidationError
from ..permissions import Session


@dataclass(frozen=True)
class CustomerInfo:
    id: int
    name: str
    phone: str
    points: int
    spent_cents: int
    visits: int
    last_visit: str


def points_value(points: int) -> int:
    """Cash value (in cents) of loyalty points when they are redeemed."""
    return points * config.LOYALTY_POINT_VALUE_CENTS


def _clean_phone(phone: str) -> str:
    phone = (phone or "").strip()
    if not phone or phone == "N/A":
        raise ValidationError("Enter the customer's phone number first!")
    return phone


def find_by_phone(conn: sqlite3.Connection, session: Session, phone: str) -> CustomerInfo | None:
    session.require("customers.lookup")
    phone = _clean_phone(phone)
    row = conn.execute("SELECT id, name, phone, loyalty_points, total_spent_cents FROM customers WHERE phone = ?",
                       (phone,)).fetchone()
    if row is None:
        return None
    stats = conn.execute("SELECT COUNT(*) AS visits, COALESCE(MAX(created_at), '') AS last FROM invoices "
                         "WHERE customer_phone = ? AND status = 'Completed'", (phone,)).fetchone()
    return CustomerInfo(row["id"], row["name"], row["phone"], row["loyalty_points"], row["total_spent_cents"],
                        stats["visits"], stats["last"])


def history(conn: sqlite3.Connection, session: Session, phone: str, limit: int = 50) -> list[sqlite3.Row]:
    """Newest invoices first (voided ones are included and marked)."""
    session.require("customers.lookup")
    phone = _clean_phone(phone)
    return conn.execute("SELECT invoice_no, created_at, total_cents, payment_method, status FROM invoices "
                        "WHERE customer_phone = ? ORDER BY id DESC LIMIT ?", (phone, limit)).fetchall()

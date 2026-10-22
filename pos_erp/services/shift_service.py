"""Register shifts: open with a float, close with a counted drawer, and a summary in between.

Cash expected in the drawer = opening cash + cash sales + cash received on credit accounts
                              - cash refunds (returns, and voids of earlier sales) - cash expenses.
A sale that is voided inside the same shift never counts at all.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .. import config, timeutil
from ..db.connection import transaction
from ..errors import ValidationError
from ..money import fmt, to_cents
from ..permissions import Session
from . import audit, credit_service

PERM_USE = "sales.create"
PERM_MANAGE = "reports.view"


@dataclass(frozen=True)
class ShiftSummary:
    opening_cash_cents: int = 0
    cash_sales_cents: int = 0
    digital_sales: dict = field(default_factory=dict)
    credit_sales_cents: int = 0
    invoices: int = 0
    due_cash_cents: int = 0
    due_digital: dict = field(default_factory=dict)
    cash_refunds_cents: int = 0
    digital_refunds_cents: int = 0
    expenses_cash_cents: int = 0

    @property
    def expected_cash_cents(self) -> int:
        return (self.opening_cash_cents + self.cash_sales_cents + self.due_cash_cents
                - self.cash_refunds_cents - self.expenses_cash_cents)

    @property
    def digital_net_cents(self) -> int:
        return sum(self.digital_sales.values()) + sum(self.due_digital.values()) - self.digital_refunds_cents


def compute_summary(conn: sqlite3.Connection, start: str, end: str, opening_cash_cents: int) -> ShiftSummary:
    """Everything that happened between two timestamps ('YYYY-MM-DD HH:MM:SS')."""
    names = {row["name"] for row in conn.execute("PRAGMA table_info(sales_returns)")}
    if "created_at" not in names:
        raise RuntimeError("The sales_returns table has no created_at column, which the shift summary needs.")
    cash, credit = config.PAYMENT_METHODS[0], credit_service.CREDIT_METHOD

    cash_sales = credit_sales = invoices = 0
    digital_sales: dict[str, int] = {}
    for row in conn.execute("SELECT payment_method, COUNT(*) AS n, COALESCE(SUM(total_cents), 0) AS cents "
                            "FROM invoices WHERE status = 'Completed' AND created_at >= ? AND created_at <= ? "
                            "GROUP BY payment_method", (start, end)):
        invoices += row["n"]
        if row["payment_method"] == cash:
            cash_sales = row["cents"]
        elif row["payment_method"] == credit:
            credit_sales = row["cents"]
        else:
            digital_sales[row["payment_method"]] = row["cents"]

    due_cash = 0
    due_digital: dict[str, int] = {}
    for row in conn.execute("SELECT method, COALESCE(SUM(-amount_cents), 0) AS cents FROM customer_ledger "
                            "WHERE entry_type = 'PAYMENT' AND created_at >= ? AND created_at <= ? GROUP BY method",
                            (start, end)):
        if row["method"] == cash:
            due_cash = row["cents"]
        else:
            due_digital[row["method"]] = row["cents"]

    cash_refunds = digital_refunds = 0
    for row in conn.execute("SELECT i.payment_method, COALESCE(SUM(r.refund_cents), 0) AS cents FROM sales_returns r "
                            "JOIN invoice_items ii ON ii.id = r.invoice_item_id JOIN invoices i ON i.id = ii.invoice_id "
                            "WHERE r.created_at >= ? AND r.created_at <= ? GROUP BY i.payment_method", (start, end)):
        if row["payment_method"] == cash:
            cash_refunds += row["cents"]
        elif row["payment_method"] != credit:
            digital_refunds += row["cents"]
    cash_refunds += conn.execute("SELECT COALESCE(SUM(total_cents), 0) FROM invoices WHERE status = 'Voided' "
                                 "AND payment_method = ? AND voided_at >= ? AND voided_at <= ? AND created_at < ?",
                                 (cash, start, end, start)).fetchone()[0]

    expenses_cash = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM expenses WHERE is_void = 0 "
                                 "AND paid_from = 'Cash' AND created_at >= ? AND created_at <= ?",
                                 (start, end)).fetchone()[0]
    return ShiftSummary(opening_cash_cents, cash_sales, digital_sales, credit_sales, invoices, due_cash, due_digital,
                        cash_refunds, digital_refunds, expenses_cash)


def _cents(text: str, label: str) -> int:
    try:
        cents = to_cents(text) if str(text).strip() else 0
    except ValueError:
        raise ValidationError(f"{label} is not a valid number!") from None
    if cents < 0:
        raise ValidationError(f"{label} cannot be negative.")
    return cents


def current_shift(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM shifts WHERE status = 'Open'").fetchone()


def open_shift(conn: sqlite3.Connection, session: Session, opening_cash: str, now: datetime | None = None) -> int:
    session.require(PERM_USE)
    cents = _cents(opening_cash, "Opening cash")
    stamp = timeutil.stamp(now or timeutil.now())
    with transaction(conn):
        if current_shift(conn) is not None:
            raise ValidationError("A shift is already open. Close it first.")
        try:
            shift_id = conn.execute("INSERT INTO shifts (opened_by, opened_at, opening_cash_cents) VALUES (?, ?, ?)",
                                    (session.username, stamp, cents)).lastrowid
        except sqlite3.IntegrityError:
            raise ValidationError("A shift is already open. Close it first.") from None
        audit.log(conn, session.username, f"Opened shift #{shift_id} with {fmt(cents)} in the drawer")
    return shift_id


def summary_of(conn: sqlite3.Connection, shift, now: datetime | None = None) -> ShiftSummary:
    """The stored summary for a closed shift, a live one for the open shift."""
    if shift["summary_json"]:
        return ShiftSummary(**json.loads(shift["summary_json"]))
    return compute_summary(conn, shift["opened_at"], timeutil.stamp(now or timeutil.now()),
                           shift["opening_cash_cents"])


def live_summary(conn: sqlite3.Connection, session: Session, now: datetime | None = None):
    session.require(PERM_USE)
    shift = current_shift(conn)
    if shift is None:
        raise ValidationError("There is no open shift.")
    return shift, summary_of(conn, shift, now)


def close_shift(conn: sqlite3.Connection, session: Session, counted_cash: str, note: str = "",
                now: datetime | None = None) -> sqlite3.Row:
    """Close the open shift against the cash counted in the drawer. Returns the stored shift row."""
    session.require(PERM_USE)
    counted = _cents(counted_cash, "Counted cash")
    closed_at = timeutil.stamp(now or timeutil.now())
    with transaction(conn):
        shift = current_shift(conn)
        if shift is None:
            raise ValidationError("There is no open shift.")
        if shift["opened_by"] != session.username:
            session.require(PERM_MANAGE)
        summary = compute_summary(conn, shift["opened_at"], closed_at, shift["opening_cash_cents"])
        variance = counted - summary.expected_cash_cents
        conn.execute("UPDATE shifts SET status = 'Closed', closed_by = ?, closed_at = ?, counted_cash_cents = ?, "
                     "expected_cash_cents = ?, variance_cents = ?, note = ?, summary_json = ? WHERE id = ?",
                     (session.username, closed_at, counted, summary.expected_cash_cents, variance,
                      (note or "").strip(), json.dumps(asdict(summary)), shift["id"]))
        audit.log(conn, session.username, f"Closed shift #{shift['id']}: expected {fmt(summary.expected_cash_cents)}, "
                                          f"counted {fmt(counted)}, difference {fmt(variance)}")
    return conn.execute("SELECT * FROM shifts WHERE id = ?", (shift["id"],)).fetchone()


def list_shifts(conn: sqlite3.Connection, session: Session, limit: int = 50) -> list[sqlite3.Row]:
    session.require(PERM_MANAGE)
    return conn.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

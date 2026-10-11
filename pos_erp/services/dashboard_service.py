"""Numbers for the dashboard tab: daily revenue chart and top sellers (Admin)."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from .. import timeutil
from ..errors import ValidationError
from ..permissions import Session


def _window(days: int, today: date | None) -> tuple[date, str, str]:
    if not isinstance(days, int) or not 1 <= days <= 366:
        raise ValidationError("Choose between 1 and 366 days.")
    end = today or timeutil.now().date()
    start = end - timedelta(days=days - 1)
    return start, f"{start:%Y-%m-%d} 00:00:00", f"{end + timedelta(days=1):%Y-%m-%d} 00:00:00"


def daily_revenue(conn: sqlite3.Connection, session: Session, days: int = 30,
                  today: date | None = None) -> list[tuple[str, int]]:
    """Net revenue (tax included, refunds taken off) per day, oldest first, with 0 for quiet days.
    Voided invoices are ignored; refunds count against the day of the original invoice."""
    session.require("reports.view")
    start, lo, hi = _window(days, today)
    gross = {r["day"]: r["cents"] for r in conn.execute(
        "SELECT substr(created_at, 1, 10) AS day, SUM(total_cents) AS cents FROM invoices "
        "WHERE status = 'Completed' AND created_at >= ? AND created_at < ? GROUP BY day", (lo, hi))}
    refunds = {r["day"]: r["cents"] for r in conn.execute(
        "SELECT substr(i.created_at, 1, 10) AS day, SUM(r.refund_cents) AS cents FROM sales_returns r "
        "JOIN invoice_items ii ON ii.id = r.invoice_item_id JOIN invoices i ON i.id = ii.invoice_id "
        "WHERE i.status = 'Completed' AND i.created_at >= ? AND i.created_at < ? GROUP BY day", (lo, hi))}
    series = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        series.append((day, gross.get(day, 0) - refunds.get(day, 0)))
    return series


def top_sellers(conn: sqlite3.Connection, session: Session, days: int = 30, limit: int = 5,
                today: date | None = None) -> list[tuple[str, int, int]]:
    """(product, units, revenue) net of returns, best revenue first."""
    session.require("reports.view")
    _start, lo, hi = _window(days, today)
    rows = conn.execute(
        """SELECT ii.product_name, ii.quantity, ii.line_total_cents,
                  COALESCE((SELECT SUM(r.quantity) FROM sales_returns r WHERE r.invoice_item_id = ii.id), 0) AS returned,
                  COALESCE((SELECT SUM(r.refund_cents) FROM sales_returns r WHERE r.invoice_item_id = ii.id), 0) AS refunded
           FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
           WHERE i.status = 'Completed' AND i.created_at >= ? AND i.created_at < ?""", (lo, hi)).fetchall()
    totals: dict[str, list[int]] = {}
    for r in rows:
        entry = totals.setdefault(r["product_name"], [0, 0])
        entry[0] += r["quantity"] - r["returned"]
        entry[1] += r["line_total_cents"] - r["refunded"]
    ranked = sorted(((name, v[0], v[1]) for name, v in totals.items() if v[0] > 0),
                    key=lambda item: (-item[2], item[0]))
    return ranked[:limit]

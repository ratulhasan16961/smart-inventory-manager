"""Read-only numbers for the dashboard cards and analytics window."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from fractions import Fraction

from ..permissions import Session
from .inventory_service import expiry_status


@dataclass(frozen=True)
class DashboardStats:
    total_items: int
    low_stock: int
    expiry_alerts: int
    cost_value_cents: int
    retail_value_cents: int
    net_revenue_cents: int


@dataclass(frozen=True)
class SalesSummary:
    orders: int
    gross_revenue_cents: int
    refunds_cents: int
    est_gross_profit_cents: int

    @property
    def net_revenue_cents(self) -> int:
        return self.gross_revenue_cents - self.refunds_cents


def _revenue(conn: sqlite3.Connection) -> tuple[int, int]:
    gross = conn.execute("SELECT COALESCE(SUM(total_cents), 0) FROM invoices").fetchone()[0]
    refunds = conn.execute("SELECT COALESCE(SUM(refund_cents), 0) FROM sales_returns").fetchone()[0]
    return gross, refunds


def dashboard_stats(conn: sqlite3.Connection) -> DashboardStats:
    products = conn.execute("SELECT stock, min_alert, cost_cents, price_cents, expiry FROM products "
                            "WHERE is_active = 1").fetchall()
    gross, refunds = _revenue(conn)
    return DashboardStats(
        total_items=len(products),
        low_stock=sum(1 for p in products if p["stock"] <= p["min_alert"]),
        expiry_alerts=sum(1 for p in products if expiry_status(p["expiry"])),
        cost_value_cents=sum(p["stock"] * p["cost_cents"] for p in products),
        retail_value_cents=sum(p["stock"] * p["price_cents"] for p in products),
        net_revenue_cents=gross - refunds,
    )


def _items_with_returns(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT ii.quantity, ii.unit_cost_cents, ii.line_subtotal_cents, ii.line_discount_cents, p.category,
                  COALESCE((SELECT SUM(r.quantity) FROM sales_returns r WHERE r.invoice_item_id = ii.id), 0) AS returned
           FROM invoice_items ii JOIN products p ON p.id = ii.product_id""").fetchall()


def sales_summary(conn: sqlite3.Connection, session: Session) -> SalesSummary:
    """Profit uses the cost snapshot stored on each invoice line (not today's cost) and excludes tax."""
    session.require("analytics.view")
    gross, refunds = _revenue(conn)
    orders = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    profit = 0
    for item in _items_with_returns(conn):
        line_profit = (item["line_subtotal_cents"] - item["line_discount_cents"]) - item["unit_cost_cents"] * item["quantity"]
        kept = item["quantity"] - item["returned"]
        profit += int(round(Fraction(line_profit * kept, item["quantity"])))
    return SalesSummary(orders, gross, refunds, profit)


def sales_by_category(conn: sqlite3.Connection, session: Session) -> list[tuple[str, int]]:
    """Units sold per category (net of returns), largest first."""
    session.require("analytics.view")
    totals: dict[str, int] = {}
    for item in _items_with_returns(conn):
        totals[item["category"] or "General"] = totals.get(item["category"] or "General", 0) + item["quantity"] - item["returned"]
    return sorted(((c, n) for c, n in totals.items() if n > 0), key=lambda pair: -pair[1])


def customers(conn: sqlite3.Connection, session: Session) -> list[sqlite3.Row]:
    session.require("customers.view")
    return conn.execute("SELECT id, name, phone, loyalty_points, total_spent_cents FROM customers "
                        "ORDER BY total_spent_cents DESC").fetchall()

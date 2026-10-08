"""Read-only numbers: dashboard cards, analytics, date-range sales report, low-stock / expiry / valuation."""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .. import dayrange
from ..money import plain
from ..permissions import Session
from .inventory_service import expiry_status


# --------------------------------------------------------------------------- dashboard + analytics
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
    """Voided invoices are excluded (they can have no returns, so refunds need no filter)."""
    gross = conn.execute("SELECT COALESCE(SUM(total_cents), 0) FROM invoices WHERE status = 'Completed'").fetchone()[0]
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
           FROM invoice_items ii
           JOIN invoices i ON i.id = ii.invoice_id
           JOIN products p ON p.id = ii.product_id
           WHERE i.status = 'Completed'""").fetchall()


def _kept_profit(item) -> int:
    """Profit of an item line (tax excluded, cost at time of sale), reduced for returned units."""
    line_profit = (item["line_subtotal_cents"] - item["line_discount_cents"]) - item["unit_cost_cents"] * item["quantity"]
    kept = item["quantity"] - item["returned"]
    return int(round(Fraction(line_profit * kept, item["quantity"])))


def sales_summary(conn: sqlite3.Connection, session: Session) -> SalesSummary:
    session.require("analytics.view")
    gross, refunds = _revenue(conn)
    orders = conn.execute("SELECT COUNT(*) FROM invoices WHERE status = 'Completed'").fetchone()[0]
    profit = sum(_kept_profit(item) for item in _items_with_returns(conn))
    return SalesSummary(orders, gross, refunds, profit)


def sales_by_category(conn: sqlite3.Connection, session: Session) -> list[tuple[str, int]]:
    """Units sold per category (net of returns), largest first."""
    session.require("analytics.view")
    totals: dict[str, int] = {}
    for item in _items_with_returns(conn):
        name = item["category"] or "General"
        totals[name] = totals.get(name, 0) + item["quantity"] - item["returned"]
    return sorted(((c, n) for c, n in totals.items() if n > 0), key=lambda pair: -pair[1])


def customers(conn: sqlite3.Connection, session: Session) -> list[sqlite3.Row]:
    session.require("customers.view")
    return conn.execute("SELECT id, name, phone, loyalty_points, total_spent_cents FROM customers "
                        "ORDER BY total_spent_cents DESC").fetchall()


# --------------------------------------------------------------------------- date-range sales report
@dataclass(frozen=True)
class DailyRow:
    day: str
    orders: int
    units: int
    revenue_cents: int


@dataclass(frozen=True)
class SalesReport:
    start: str
    end: str
    orders: int
    units: int
    gross_cents: int
    discount_cents: int
    tax_cents: int
    refunds_cents: int
    profit_cents: int
    voided_orders: int
    voided_cents: int
    daily: tuple[DailyRow, ...]
    top_products: tuple[tuple[str, int, int], ...]    # (product, units, revenue)
    payments: tuple[tuple[str, int, int], ...]        # (method, orders, revenue)
    categories: tuple[tuple[str, int, int], ...]      # (category, units, revenue)

    @property
    def net_cents(self) -> int:
        return self.gross_cents - self.refunds_cents


_REPORT_ITEMS_SQL = """
    SELECT i.id AS invoice_id, substr(i.created_at, 1, 10) AS day, i.payment_method,
           ii.product_name, p.category, ii.quantity, ii.unit_cost_cents, ii.line_subtotal_cents,
           ii.line_discount_cents, ii.line_tax_cents, ii.line_total_cents,
           COALESCE((SELECT SUM(r.quantity) FROM sales_returns r WHERE r.invoice_item_id = ii.id), 0) AS returned,
           COALESCE((SELECT SUM(r.refund_cents) FROM sales_returns r WHERE r.invoice_item_id = ii.id), 0) AS refunded
    FROM invoice_items ii
    JOIN invoices i ON i.id = ii.invoice_id
    JOIN products p ON p.id = ii.product_id
    WHERE i.status = 'Completed' AND i.created_at >= ? AND i.created_at < ?"""


def sales_report(conn: sqlite3.Connection, session: Session, start: str, end: str) -> SalesReport:
    """Sales made between two dates (inclusive, YYYY-MM-DD). Returns are counted against the invoice date;
    voided invoices are left out and reported separately."""
    session.require("reports.view")
    start_ts, end_ts, start_day, end_day = dayrange.bounds(start, end)
    rows = conn.execute(_REPORT_ITEMS_SQL, (start_ts, end_ts)).fetchall()

    invoices: set[int] = set()
    units = gross = discount = tax = refunds = profit = 0
    daily: dict[str, dict] = {}
    products: dict[str, list[int]] = {}
    payments: dict[str, dict] = {}
    categories: dict[str, list[int]] = {}
    for r in rows:
        kept = r["quantity"] - r["returned"]
        revenue = r["line_total_cents"] - r["refunded"]
        invoices.add(r["invoice_id"])
        units += kept
        gross += r["line_total_cents"]
        discount += r["line_discount_cents"]
        tax += r["line_tax_cents"]
        refunds += r["refunded"]
        profit += _kept_profit(r)
        day = daily.setdefault(r["day"], {"invoices": set(), "units": 0, "revenue": 0})
        day["invoices"].add(r["invoice_id"])
        day["units"] += kept
        day["revenue"] += revenue
        product = products.setdefault(r["product_name"], [0, 0])
        product[0] += kept
        product[1] += revenue
        method = payments.setdefault(r["payment_method"], {"invoices": set(), "revenue": 0})
        method["invoices"].add(r["invoice_id"])
        method["revenue"] += revenue
        category = categories.setdefault(r["category"] or "General", [0, 0])
        category[0] += kept
        category[1] += revenue

    voided = conn.execute("SELECT COUNT(*), COALESCE(SUM(total_cents), 0) FROM invoices "
                          "WHERE status = 'Voided' AND created_at >= ? AND created_at < ?", (start_ts, end_ts)).fetchone()
    return SalesReport(
        start=start_day.isoformat(), end=end_day.isoformat(), orders=len(invoices), units=units,
        gross_cents=gross, discount_cents=discount, tax_cents=tax, refunds_cents=refunds, profit_cents=profit,
        voided_orders=voided[0], voided_cents=voided[1],
        daily=tuple(DailyRow(d, len(v["invoices"]), v["units"], v["revenue"]) for d, v in sorted(daily.items())),
        top_products=tuple((name, v[0], v[1]) for name, v in
                           sorted(products.items(), key=lambda kv: (-kv[1][1], kv[0]))[:10]),
        payments=tuple((name, len(v["invoices"]), v["revenue"]) for name, v in
                       sorted(payments.items(), key=lambda kv: (-kv[1]["revenue"], kv[0]))),
        categories=tuple((name, v[0], v[1]) for name, v in
                         sorted(categories.items(), key=lambda kv: (-kv[1][1], kv[0]))),
    )


def export_sales_report_csv(session: Session, report: SalesReport, path: str | Path) -> None:
    session.require("reports.view")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Sales report", report.start, report.end])
        writer.writerow([])
        for label, value in (("Orders", report.orders), ("Units sold", report.units),
                             ("Gross sales", plain(report.gross_cents)), ("Refunds", plain(report.refunds_cents)),
                             ("Net revenue", plain(report.net_cents)), ("Discounts", plain(report.discount_cents)),
                             ("Tax collected", plain(report.tax_cents)),
                             ("Est. gross profit", plain(report.profit_cents)),
                             ("Voided invoices", report.voided_orders), ("Voided amount", plain(report.voided_cents))):
            writer.writerow([label, value])
        sections = (
            ("Daily", ("Date", "Orders", "Units", "Revenue"),
             [(d.day, d.orders, d.units, plain(d.revenue_cents)) for d in report.daily]),
            ("Top products", ("Product", "Units", "Revenue"),
             [(n, u, plain(c)) for n, u, c in report.top_products]),
            ("Payment methods", ("Method", "Orders", "Revenue"), [(n, u, plain(c)) for n, u, c in report.payments]),
            ("Categories", ("Category", "Units", "Revenue"), [(n, u, plain(c)) for n, u, c in report.categories]),
        )
        for title, header, body in sections:
            writer.writerow([])
            writer.writerow([title])
            writer.writerow(header)
            writer.writerows(body)


# --------------------------------------------------------------------------- snapshot reports
LOW_STOCK_HEADER = ("ID", "Product", "Category", "Warehouse", "Stock", "Min Alert", "Short by")
EXPIRY_HEADER = ("ID", "Product", "Batch", "Expiry", "Status", "Stock", "Stock Value (cost)")
VALUATION_HEADER = ("Category", "Products", "Units", "Cost Value", "Retail Value")


def low_stock_report(conn: sqlite3.Connection, session: Session) -> list[tuple]:
    session.require("reports.view")
    rows = conn.execute(
        "SELECT id, name, category, warehouse, stock, min_alert FROM products "
        "WHERE is_active = 1 AND stock <= min_alert ORDER BY (min_alert - stock) DESC, name").fetchall()
    return [(r["id"], r["name"], r["category"], r["warehouse"], r["stock"], r["min_alert"],
             max(r["min_alert"] - r["stock"], 0)) for r in rows]


def expiry_report(conn: sqlite3.Connection, session: Session) -> list[tuple]:
    """Products that are expired or expire within the warning window, soonest first."""
    session.require("reports.view")
    rows = conn.execute("SELECT id, name, batch, expiry, stock, cost_cents FROM products "
                        "WHERE is_active = 1 AND expiry <> '' ORDER BY expiry, name").fetchall()
    result = []
    for r in rows:
        status = expiry_status(r["expiry"])
        if status:
            result.append((r["id"], r["name"], r["batch"], r["expiry"],
                           "Expired" if status == "expired" else "Expiring soon", r["stock"],
                           plain(r["stock"] * r["cost_cents"])))
    return result


def stock_valuation(conn: sqlite3.Connection, session: Session) -> list[tuple]:
    session.require("reports.view")
    rows = conn.execute(
        """SELECT category, COUNT(*) AS items, COALESCE(SUM(stock), 0) AS units,
                  COALESCE(SUM(stock * cost_cents), 0) AS cost, COALESCE(SUM(stock * price_cents), 0) AS retail
           FROM products WHERE is_active = 1 GROUP BY category ORDER BY cost DESC, category""").fetchall()
    result = [(r["category"], r["items"], r["units"], plain(r["cost"]), plain(r["retail"])) for r in rows]
    if rows:
        result.append(("TOTAL", sum(r["items"] for r in rows), sum(r["units"] for r in rows),
                       plain(sum(r["cost"] for r in rows)), plain(sum(r["retail"] for r in rows))))
    return result


def export_table_csv(session: Session, header, rows, path: str | Path) -> None:
    session.require("reports.view")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)

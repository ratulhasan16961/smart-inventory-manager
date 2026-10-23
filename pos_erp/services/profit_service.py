"""Net profit = estimated gross profit (after discounts, returns and cost of goods) minus expenses."""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import dayrange
from ..money import plain
from ..permissions import Session
from . import expense_service, reporting_service


@dataclass(frozen=True)
class ProfitReport:
    start: str
    end: str
    orders: int
    net_revenue_cents: int
    gross_profit_cents: int
    expenses_cents: int
    expenses_by_category: tuple[tuple[str, int], ...]

    @property
    def net_profit_cents(self) -> int:
        return self.gross_profit_cents - self.expenses_cents


def profit_report(conn: sqlite3.Connection, session: Session, start: str, end: str) -> ProfitReport:
    """Credit sales count as revenue when sold (not when paid); cancelled expenses are left out."""
    session.require("reports.view")
    _start_ts, _end_ts, first, last = dayrange.bounds(start, end)
    sales = reporting_service.sales_report(conn, session, first.isoformat(), last.isoformat())
    expenses = expense_service.totals_by_category(conn, session, first.isoformat(), last.isoformat())
    return ProfitReport(first.isoformat(), last.isoformat(), sales.orders, sales.net_cents, sales.profit_cents,
                        sum(cents for _name, cents in expenses), tuple(expenses))


def export_csv(session: Session, report: ProfitReport, path) -> Path:
    session.require("reports.view")
    target = Path(path)
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Net profit report", report.start, report.end])
        writer.writerow([])
        for label, value in (("Orders", report.orders), ("Net revenue", plain(report.net_revenue_cents)),
                             ("Est. gross profit", plain(report.gross_profit_cents)),
                             ("Expenses", plain(report.expenses_cents)),
                             ("Net profit", plain(report.net_profit_cents))):
            writer.writerow([label, value])
        writer.writerow([])
        writer.writerow(["Expenses by category"])
        writer.writerow(["Category", "Amount"])
        writer.writerows((name, plain(cents)) for name, cents in report.expenses_by_category)
    return target

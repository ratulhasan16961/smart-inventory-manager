"""Daily summary email: sales, profit, expenses, money owed, low stock, expiry. Sent to the owner's address."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from .. import mailer, timeutil
from ..errors import ValidationError
from ..money import fmt
from ..permissions import Session
from . import (audit, business_service, credit_service, expense_service, reporting_service, settings_service)


def system_session(conn: sqlite3.Connection) -> Session:
    """The first active administrator: for the command-line script and the automatic send after closing a shift
    (the content goes only to the owner's own email address)."""
    row = conn.execute("SELECT id, username, role FROM users WHERE role = 'Admin' AND is_active = 1 "
                       "ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise ValidationError("There is no active administrator account.")
    return Session(row["id"], row["username"], row["role"])


def build(conn: sqlite3.Connection, session: Session, day: str | None = None,
          now: datetime | None = None) -> tuple[str, str]:
    """Returns (subject, body) for one day (today by default)."""
    session.require("reports.view")
    try:
        first = date.fromisoformat(day) if day else (now or timeutil.now()).date()
    except ValueError:
        raise ValidationError("Dates must look like 2026-10-22.") from None
    day_text, next_text = first.isoformat(), (first + timedelta(days=1)).isoformat()
    sales = reporting_service.sales_report(conn, session, day_text, day_text)
    expenses = expense_service.totals_by_category(conn, session, day_text, day_text, now=now)
    spent = sum(cents for _name, cents in expenses)
    collected = conn.execute(
        "SELECT method, COALESCE(SUM(-amount_cents), 0) AS cents FROM customer_ledger WHERE entry_type = 'PAYMENT' "
        "AND created_at >= ? AND created_at < ? GROUP BY method ORDER BY cents DESC",
        (f"{day_text} 00:00:00", f"{next_text} 00:00:00")).fetchall()
    debtors = credit_service.list_debtors(conn, session)
    low = reporting_service.low_stock_report(conn, session)
    expiring = reporting_service.expiry_report(conn, session)
    shop = settings_service.get_all(conn)["shop_name"]

    lines = [f"{shop} - daily summary for {day_text}", "", "SALES",
             f"Orders            : {sales.orders}", f"Net revenue       : {fmt(sales.net_cents)}",
             f"Refunds           : {fmt(sales.refunds_cents)}", f"Est. gross profit : {fmt(sales.profit_cents)}",
             f"Expenses          : {fmt(spent)}", f"Est. NET PROFIT   : {fmt(sales.profit_cents - spent)}"]
    for method, orders, revenue in sales.payments:
        lines.append(f"  {method:<18} {orders:>3} order(s)  {fmt(revenue)}")
    lines += ["", "MONEY RECEIVED ON CUSTOMER ACCOUNTS TODAY"]
    lines += [f"  {row['method'] or 'Other':<18} {fmt(row['cents'])}" for row in collected] or ["  none"]
    lines += ["", f"MONEY OWED BY CUSTOMERS: {fmt(sum(d['balance_cents'] for d in debtors))}"]
    lines += [f"  {d['name']} ({d['phone']}): {fmt(d['balance_cents'])}" for d in debtors[:5]]
    lines += ["", f"LOW STOCK: {len(low)} product(s)"]
    lines += [f"  {row[1]}: {row[4]} left (minimum {row[5]})" for row in low[:10]]
    lines += ["", f"EXPIRY ALERTS: {len(expiring)} product(s)"]
    lines += [f"  {row[1]}: {row[4]} ({row[3]})" for row in expiring[:5]]
    return f"{shop}: daily summary for {day_text}", "\n".join(lines) + "\n"


def send(conn: sqlite3.Connection, session: Session, day: str | None = None, sender=None,
         now: datetime | None = None) -> str:
    """Email the summary to the address in Settings > Backup & Reminders. Returns that address."""
    to = business_service.get_profile(conn)["digest_email"]
    if not to:
        raise ValidationError("Set the summary email address first (Settings > Backup & Reminders).")
    subject, body = build(conn, session, day, now)
    (sender or mailer.send_mail)(to, subject, body)
    audit.log(conn, session.username, f"Sent the daily summary to {to}")
    return to


def send_after_close(conn: sqlite3.Connection, sender=None, now: datetime | None = None) -> str | None:
    """Used after a shift is closed: sends only when 'send automatically' is on. Returns the address or None."""
    profile = business_service.get_profile(conn)
    if profile["digest_auto"] != "Yes" or not profile["digest_email"]:
        return None
    return send(conn, system_session(conn), sender=sender, now=now)

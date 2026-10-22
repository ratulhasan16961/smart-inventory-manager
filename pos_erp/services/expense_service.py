"""Daily expenses (rent, power, tea, transport...). Cash expenses reduce the cash expected in the drawer."""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path

from .. import timeutil
from ..db.connection import transaction
from ..errors import ValidationError
from ..money import fmt, plain, to_cents
from ..permissions import Session
from . import audit

CATEGORIES = ("Rent", "Utilities", "Salary", "Transport", "Supplies", "Repairs", "Tea / Snacks", "Other")
PAID_FROM = ("Cash", "Other")
PERM_ADD = "sales.create"
PERM_VIEW = "sales.create"
PERM_MANAGE = "reports.view"


def _today(now: datetime | None = None) -> str:
    return (now or timeutil.now()).date().isoformat()


def _check_day(text: str) -> str:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValidationError("Dates must look like 2026-10-22.") from None


def add_expense(conn: sqlite3.Connection, session: Session, category: str, amount: str, note: str = "",
                paid_from: str = "Cash", day: str | None = None, now: datetime | None = None) -> int:
    session.require(PERM_ADD)
    category = (category or "").strip()
    if not category or len(category) > 40:
        raise ValidationError("Choose or type a category (max 40 characters).")
    note = (note or "").strip()
    if len(note) > 200:
        raise ValidationError("The note is too long (max 200 characters).")
    try:
        cents = to_cents(amount)
    except ValueError:
        raise ValidationError("Amount is not a valid number!") from None
    if cents <= 0:
        raise ValidationError("Amount must be more than zero!")
    if paid_from not in PAID_FROM:
        raise ValidationError("Choose where the money came from.")
    now = now or timeutil.now()
    expense_day = _check_day(day) if day else _today(now)
    if expense_day != _today(now):
        session.require(PERM_MANAGE)
    with transaction(conn):
        expense_id = conn.execute(
            "INSERT INTO expenses (expense_date, category, amount_cents, note, paid_from, username, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (expense_day, category, cents, note, paid_from, session.username, timeutil.stamp(now))).lastrowid
        audit.log(conn, session.username, f"Expense {fmt(cents)} ({category}, paid from {paid_from})")
    return expense_id


def _range(date_from: str | None, date_to: str | None, now: datetime | None) -> tuple[str, str]:
    start = _check_day(date_from) if date_from else _today(now)
    end = _check_day(date_to) if date_to else start
    if start > end:
        raise ValidationError("The first date must not be after the last date.")
    return start, end


def list_expenses(conn: sqlite3.Connection, session: Session, date_from: str | None = None,
                  date_to: str | None = None, include_void: bool = False,
                  now: datetime | None = None) -> list[sqlite3.Row]:
    """Today's expenses by default. Other dates need the manage permission."""
    session.require(PERM_VIEW)
    start, end = _range(date_from, date_to, now)
    today = _today(now)
    if (start, end) != (today, today):
        session.require(PERM_MANAGE)
    return conn.execute("SELECT * FROM expenses WHERE expense_date BETWEEN ? AND ? AND (is_void = 0 OR ?) "
                        "ORDER BY id DESC", (start, end, 1 if include_void else 0)).fetchall()


def void_expense(conn: sqlite3.Connection, session: Session, expense_id: int, reason: str) -> None:
    session.require(PERM_MANAGE)
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationError("Please write a short reason (at least 3 characters).")
    with transaction(conn):
        row = conn.execute("SELECT amount_cents, category, is_void FROM expenses WHERE id = ?",
                           (expense_id,)).fetchone()
        if row is None:
            raise ValidationError("Expense not found.")
        if row["is_void"]:
            raise ValidationError("This expense was already cancelled.")
        conn.execute("UPDATE expenses SET is_void = 1, void_reason = ?, voided_by = ? WHERE id = ?",
                     (reason, session.username, expense_id))
        audit.log(conn, session.username, f"Cancelled expense #{expense_id} ({row['category']}, "
                                          f"{fmt(row['amount_cents'])}): {reason}")


def totals_by_category(conn: sqlite3.Connection, session: Session, date_from: str | None = None,
                       date_to: str | None = None, now: datetime | None = None) -> list[tuple[str, int]]:
    session.require(PERM_MANAGE)
    start, end = _range(date_from, date_to, now)
    rows = conn.execute("SELECT category, SUM(amount_cents) AS cents FROM expenses WHERE is_void = 0 "
                        "AND expense_date BETWEEN ? AND ? GROUP BY category ORDER BY cents DESC, category",
                        (start, end)).fetchall()
    return [(row["category"], row["cents"]) for row in rows]


def export_csv(conn: sqlite3.Connection, session: Session, date_from: str | None, date_to: str | None,
               path, now: datetime | None = None) -> Path:
    rows = list_expenses(conn, session, date_from, date_to, include_void=False, now=now)
    target = Path(path)
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Date", "Category", "Amount", "Paid from", "Note", "By"])
        for row in reversed(rows):
            writer.writerow([row["expense_date"], row["category"], plain(row["amount_cents"]), row["paid_from"],
                             row["note"], row["username"]])
    return target

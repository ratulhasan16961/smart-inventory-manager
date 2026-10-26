"""Customer credit (due): a ledger of what each customer owes the shop.

Balance = sum of the ledger. Positive: the customer owes money. Negative: the customer has credit (an advance).
Entries: CHARGE (sold on credit), PAYMENT (money received), CREDIT_NOTE (returns, voids), ADJUSTMENT (an
administrator's correction or write-off). A payment goes to the account as a whole, not to one invoice.
"""
from __future__ import annotations

import csv
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import config, timeutil
from ..db.connection import transaction
from ..errors import ValidationError
from ..money import fmt, plain, to_cents
from ..permissions import Session
from . import audit

CREDIT_METHOD = "Credit (Due)"
ENTRY_TYPES = ("CHARGE", "PAYMENT", "CREDIT_NOTE", "ADJUSTMENT")
PERM_VIEW = "customers.lookup"
PERM_CREDIT_SALE = "sales.create"
PERM_COLLECT = "sales.create"
PERM_MANAGE = "reports.view"


def sale_methods(session: Session) -> tuple[str, ...]:
    """Payment methods offered at the till (credit only for users who may sell on credit)."""
    methods = tuple(config.PAYMENT_METHODS)
    return methods + (CREDIT_METHOD,) if session.can(PERM_CREDIT_SALE) else methods


def customer_id_for(conn: sqlite3.Connection, phone: str | None) -> int | None:
    row = conn.execute("SELECT id FROM customers WHERE phone = ?", ((phone or "").strip(),)).fetchone()
    return row["id"] if row else None


def balance_of(conn: sqlite3.Connection, customer_id: int) -> int:
    return conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM customer_ledger WHERE customer_id = ?",
                        (customer_id,)).fetchone()[0]


def balance_of_phone(conn: sqlite3.Connection, phone: str | None) -> int:
    customer_id = customer_id_for(conn, phone)
    return balance_of(conn, customer_id) if customer_id else 0


def credit_limit_of(conn: sqlite3.Connection, customer_id: int) -> int:
    return conn.execute("SELECT credit_limit_cents FROM customers WHERE id = ?", (customer_id,)).fetchone()[0]


def post(conn: sqlite3.Connection, customer_id: int, entry_type: str, amount_cents: int, username: str, *,
         invoice_id: int | None = None, method: str = "", note: str = "") -> int:
    if entry_type not in ENTRY_TYPES or amount_cents == 0:
        raise ValueError("Invalid ledger entry")
    return conn.execute(
        "INSERT INTO customer_ledger (customer_id, entry_type, amount_cents, invoice_id, method, note, username, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (customer_id, entry_type, amount_cents, invoice_id, method, note, username, timeutil.stamp())).lastrowid


def _positive_cents(text: str, label: str) -> int:
    try:
        cents = to_cents(text)
    except ValueError:
        raise ValidationError(f"{label} is not a valid number!") from None
    if cents <= 0:
        raise ValidationError(f"{label} must be more than zero!")
    return cents


def _require_customer(conn: sqlite3.Connection, phone: str):
    row = conn.execute("SELECT id, name, phone, credit_limit_cents FROM customers WHERE phone = ?",
                       ((phone or "").strip(),)).fetchone()
    if row is None:
        raise ValidationError("No customer with that phone number.")
    return row

@dataclass(frozen=True)
class CustomerAccount:
    customer_id: int
    name: str
    phone: str
    balance_cents: int
    credit_limit_cents: int


@dataclass(frozen=True)
class LedgerLine:
    when: str
    entry_type: str
    amount_cents: int
    balance_cents: int
    invoice_no: str
    method: str
    note: str
    username: str


def get_account(conn: sqlite3.Connection, session: Session, phone: str) -> CustomerAccount:
    session.require(PERM_VIEW)
    row = _require_customer(conn, phone)
    return CustomerAccount(row["id"], row["name"], row["phone"], balance_of(conn, row["id"]), row["credit_limit_cents"])


def ledger(conn: sqlite3.Connection, session: Session, phone: str, limit: int = 500) -> list[LedgerLine]:
    session.require(PERM_VIEW)
    customer = _require_customer(conn, phone)
    rows = conn.execute(
        "SELECT l.created_at, l.entry_type, l.amount_cents, l.method, l.note, l.username, i.invoice_no "
        "FROM customer_ledger l LEFT JOIN invoices i ON i.id = l.invoice_id WHERE l.customer_id = ? ORDER BY l.id",
        (customer["id"],)).fetchall()
    running, lines = 0, []
    for row in rows:
        running += row["amount_cents"]
        lines.append(LedgerLine(row["created_at"], row["entry_type"], row["amount_cents"], running,
                                row["invoice_no"] or "", row["method"], row["note"], row["username"]))
    return lines[-limit:]


def list_debtors(conn: sqlite3.Connection, session: Session) -> list[sqlite3.Row]:
    session.require(PERM_VIEW)
    return conn.execute(
        "SELECT c.id, c.name, c.phone, c.credit_limit_cents, SUM(l.amount_cents) AS balance_cents, "
        "MAX(l.created_at) AS last_entry FROM customers c JOIN customer_ledger l ON l.customer_id = c.id "
        "GROUP BY c.id HAVING balance_cents > 0 ORDER BY balance_cents DESC").fetchall()


def total_receivable(conn: sqlite3.Connection, session: Session) -> int:
    return sum(row["balance_cents"] for row in list_debtors(conn, session))


def receive_payment(conn: sqlite3.Connection, session: Session, phone: str, amount: str, method: str,
                    note: str = "") -> int:
    """Record money received against the account. Returns the new balance."""
    session.require(PERM_COLLECT)
    cents = _positive_cents(amount, "Amount")
    if method not in config.PAYMENT_METHODS:
        raise ValidationError("Choose how the customer paid.")
    with transaction(conn):
        customer = _require_customer(conn, phone)
        balance = balance_of(conn, customer["id"])
        if balance <= 0:
            raise ValidationError("This customer does not owe anything.")
        if cents > balance:
            raise ValidationError(f"The customer owes only {fmt(balance)}.")
        post(conn, customer["id"], "PAYMENT", -cents, session.username, method=method, note=note.strip())
        audit.log(conn, session.username, f"Received {fmt(cents)} ({method}) on the account of {customer['phone']}")
    return balance - cents


def adjust(conn: sqlite3.Connection, session: Session, phone: str, amount: str, reason: str) -> int:
    """Administrator correction: negative = write-off / reduce the debt, positive = add to it."""
    session.require(PERM_MANAGE)
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationError("Please write a short reason (at least 3 characters).")
    try:
        cents = to_cents(amount)
    except ValueError:
        raise ValidationError("Amount is not a valid number!") from None
    if cents == 0:
        raise ValidationError("The adjustment cannot be zero.")
    with transaction(conn):
        customer = _require_customer(conn, phone)
        post(conn, customer["id"], "ADJUSTMENT", cents, session.username, method="Adjustment", note=reason)
        audit.log(conn, session.username, f"Adjusted the account of {customer['phone']} by {fmt(cents)}: {reason}")
        return balance_of(conn, customer["id"])


def set_credit_limit(conn: sqlite3.Connection, session: Session, phone: str, limit: str) -> None:
    """0 means no limit."""
    session.require(PERM_MANAGE)
    try:
        cents = to_cents(limit) if str(limit).strip() else 0
    except ValueError:
        raise ValidationError("Limit is not a valid number!") from None
    if cents < 0:
        raise ValidationError("The limit cannot be negative.")
    with transaction(conn):
        customer = _require_customer(conn, phone)
        conn.execute("UPDATE customers SET credit_limit_cents = ? WHERE id = ?", (cents, customer["id"]))
        audit.log(conn, session.username, f"Set the credit limit of {customer['phone']} to {fmt(cents)}")


def export_ledger_csv(conn: sqlite3.Connection, session: Session, phone: str, path) -> Path:
    lines = ledger(conn, session, phone, limit=100000)
    target = Path(path)
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["When", "Type", "Amount", "Balance", "Invoice", "Method", "Note", "User"])
        for line in lines:
            writer.writerow([line.when, line.entry_type, plain(line.amount_cents), plain(line.balance_cents),
                             line.invoice_no, line.method, line.note, line.username])
    return target


def _parse_paid_now(text: str, total_cents: int) -> int:
    if text is None or not str(text).strip():
        return 0
    try:
        paid = to_cents(text)
    except ValueError:
        raise ValidationError("The amount paid now is not a valid number!") from None
    if paid < 0 or paid > total_cents:
        raise ValidationError(f"Paid now must be between {fmt(0)} and the bill total ({fmt(total_cents)}).")
    return paid


def convert_to_credit(conn: sqlite3.Connection, session: Session, invoice_no: str, paid_now: str = "") -> int:
    """Turn a just-created invoice into a credit sale: the unpaid part goes on the customer's account.

    Call it inside the checkout transaction, so a refusal (no phone number, over the credit limit) rolls the
    whole sale back. Returns the customer's new balance.
    """
    session.require(PERM_CREDIT_SALE)
    invoice = conn.execute("SELECT id, total_cents, customer_phone FROM invoices WHERE invoice_no = ?",
                           (invoice_no,)).fetchone()
    if invoice is None:
        raise ValidationError("Invoice not found.")
    phone = (invoice["customer_phone"] or "").strip()
    customer_id = customer_id_for(conn, phone) if phone and phone.upper() != "N/A" else None
    if customer_id is None:
        raise ValidationError("Credit sales need the customer's phone number.")
    total = invoice["total_cents"]
    paid = _parse_paid_now(paid_now, total)
    owed_after = balance_of(conn, customer_id) + total - paid
    limit = credit_limit_of(conn, customer_id)
    if limit and owed_after > limit:
        raise ValidationError(f"Credit limit exceeded: the limit is {fmt(limit)} and the customer would owe "
                              f"{fmt(owed_after)}.")
    conn.execute("UPDATE invoices SET payment_method = ?, tendered_cents = ?, change_cents = 0 WHERE id = ?",
                 (CREDIT_METHOD, paid, invoice["id"]))
    if total:
        post(conn, customer_id, "CHARGE", total, session.username, invoice_id=invoice["id"],
             method=CREDIT_METHOD, note=invoice_no)
    if paid:
        post(conn, customer_id, "PAYMENT", -paid, session.username, invoice_id=invoice["id"],
             method=config.PAYMENT_METHODS[0], note="Paid at sale")
    audit.log(conn, session.username, f"{invoice_no} sold on credit: {fmt(total - paid)} added to {phone}")
    return balance_of(conn, customer_id)


def sell_on_credit(conn: sqlite3.Connection, session: Session, run_checkout: Callable[[], object],
                   paid_now: str = "") -> str:
    """Run `run_checkout()` (a normal checkout that returns the Invoice) and make it a credit sale, atomically.

    Returns the invoice number; read the invoice again afterwards, because its payment method has changed.
    """
    session.require(PERM_CREDIT_SALE)
    with transaction(conn):
        invoice = run_checkout()
        convert_to_credit(conn, session, invoice.invoice_no, paid_now)
    return invoice.invoice_no


def on_return(conn: sqlite3.Connection, session: Session, invoice_no: str, refund_cents: int) -> bool:
    """Call inside the return transaction. A returned credit sale reduces the debt instead of paying cash out.

    Returns True when the refund went to the customer's account.
    """
    invoice = conn.execute("SELECT id, payment_method, customer_phone FROM invoices WHERE invoice_no = ?",
                           (invoice_no,)).fetchone()
    if invoice is None or invoice["payment_method"] != CREDIT_METHOD:
        return False
    customer_id = customer_id_for(conn, invoice["customer_phone"])
    if customer_id and refund_cents:
        post(conn, customer_id, "CREDIT_NOTE", -refund_cents, session.username, invoice_id=invoice["id"],
             method="Return", note=f"{invoice_no} return")
    return True


def on_void(conn: sqlite3.Connection, session: Session, invoice_no: str, reason: str = "") -> None:
    """Call inside the void transaction: cancels the charge. Money paid at the sale stays on the account as credit."""
    invoice = conn.execute("SELECT id, payment_method, customer_phone FROM invoices WHERE invoice_no = ?",
                           (invoice_no,)).fetchone()
    if invoice is None or invoice["payment_method"] != CREDIT_METHOD:
        return
    customer_id = customer_id_for(conn, invoice["customer_phone"])
    charged = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM customer_ledger "
                           "WHERE invoice_id = ? AND entry_type = 'CHARGE'", (invoice["id"],)).fetchone()[0]
    if customer_id and charged:
        post(conn, customer_id, "CREDIT_NOTE", -charged, session.username, invoice_id=invoice["id"],
             method="Void", note=f"VOID {invoice_no}: {reason}".strip())

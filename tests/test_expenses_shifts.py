import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from pos_erp import timeutil
from pos_erp.errors import PermissionDenied, ValidationError
from pos_erp.permissions import Session
from pos_erp.services import credit_service, expense_service, shift_report, shift_service, user_service
from tests.helpers import DbTestCase

T0 = datetime(2026, 10, 22, 9, 0, 0)


def at(minutes):
    return timeutil.stamp(T0 + timedelta(minutes=minutes))


def insert_row(conn, table, **values):
    """Insert a row, filling every other required column with a dummy value."""
    row = {}
    for col in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if not col["pk"] and col["notnull"] and col["dflt_value"] is None:
            row[col["name"]] = 0 if "INT" in (col["type"] or "").upper() else "x"
    row.update(values)
    marks = ", ".join("?" for _ in row)
    return conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({marks})", tuple(row.values())).lastrowid


class ExpenseTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.cashier = Session(2, "sara", "Cashier")

    def test_add_and_list_today(self):
        expense_service.add_expense(self.conn, self.cashier, "Tea / Snacks", "12.50", "milk", now=T0)
        expense_service.add_expense(self.conn, self.cashier, "Rent", "300", paid_from="Other", now=T0)
        rows = expense_service.list_expenses(self.conn, self.cashier, now=T0)
        self.assertEqual([(r["category"], r["amount_cents"], r["paid_from"]) for r in rows],
                         [("Rent", 30000, "Other"), ("Tea / Snacks", 1250, "Cash")])

    def test_validation(self):
        for category, amount, paid_from in (("", "5", "Cash"), ("x" * 41, "5", "Cash"), ("Rent", "0", "Cash"),
                                            ("Rent", "-3", "Cash"), ("Rent", "abc", "Cash"), ("Rent", "5", "Bank")):
            with self.assertRaises(ValidationError, msg=f"{category!r} {amount} {paid_from}"):
                expense_service.add_expense(self.conn, self.cashier, category, amount, paid_from=paid_from)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM expenses"), 0)

    def test_other_days_and_reports_are_for_administrators(self):
        with self.assertRaises(PermissionDenied):
            expense_service.add_expense(self.conn, self.cashier, "Rent", "5", day="2026-10-01", now=T0)
        expense_service.add_expense(self.conn, self.admin, "Rent", "5", day="2026-10-01", now=T0)
        with self.assertRaises(PermissionDenied):
            expense_service.list_expenses(self.conn, self.cashier, "2026-10-01", "2026-10-22", now=T0)
        with self.assertRaises(PermissionDenied):
            expense_service.totals_by_category(self.conn, self.cashier, "2026-10-01", "2026-10-22", now=T0)
        with self.assertRaises(ValidationError):
            expense_service.list_expenses(self.conn, self.admin, "2026-10-22", "2026-10-01", now=T0)
        with self.assertRaises(ValidationError):
            expense_service.list_expenses(self.conn, self.admin, "22/10/2026", now=T0)

    def test_cancel_totals_and_csv(self):
        keep = expense_service.add_expense(self.conn, self.admin, "Rent", "100", now=T0)
        drop = expense_service.add_expense(self.conn, self.admin, "Transport", "20", now=T0)
        expense_service.add_expense(self.conn, self.admin, "Rent", "50", now=T0)
        with self.assertRaises(PermissionDenied):
            expense_service.void_expense(self.conn, self.cashier, drop, "mistake")
        with self.assertRaises(ValidationError):
            expense_service.void_expense(self.conn, self.admin, drop, "x")
        expense_service.void_expense(self.conn, self.admin, drop, "entered twice")
        with self.assertRaises(ValidationError):
            expense_service.void_expense(self.conn, self.admin, drop, "again please")
        self.assertEqual(expense_service.totals_by_category(self.conn, self.admin, now=T0), [("Rent", 15000)])
        self.assertEqual(len(expense_service.list_expenses(self.conn, self.admin, now=T0, include_void=True)), 3)
        with tempfile.TemporaryDirectory() as folder:
            path = expense_service.export_csv(self.conn, self.admin, None, None, Path(folder) / "e.csv", now=T0)
            rows = list(csv.reader(open(path, encoding="utf-8")))
        self.assertEqual(rows[0][:3], ["Date", "Category", "Amount"])
        self.assertEqual([r[2] for r in rows[1:]], ["100.00", "50.00"])
        self.assertTrue(keep)


class ShiftTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.cashier = Session(2, "sara", "Cashier")

    def invoice(self, number, total, method, created, status="Completed", voided_at=None):
        values = dict(invoice_no=number, total_cents=total, payment_method=method, created_at=created, status=status)
        if voided_at:
            values["voided_at"] = voided_at
        return insert_row(self.conn, "invoices", **values)

    def test_open_close_rules(self):
        with self.assertRaises(ValidationError):
            shift_service.close_shift(self.conn, self.cashier, "0")
        with self.assertRaises(ValidationError):
            shift_service.open_shift(self.conn, self.cashier, "abc")
        with self.assertRaises(ValidationError):
            shift_service.open_shift(self.conn, self.cashier, "-5")
        shift_service.open_shift(self.conn, self.cashier, "", now=T0)
        with self.assertRaises(ValidationError):
            shift_service.open_shift(self.conn, self.admin, "10", now=T0)
        with self.assertRaises(PermissionDenied):
            shift_service.close_shift(self.conn, Session(3, "bob", "Cashier"), "0")
        with self.assertRaises(PermissionDenied):
            shift_service.list_shifts(self.conn, self.cashier)
        shift_service.close_shift(self.conn, self.admin, "0", now=T0 + timedelta(hours=1))
        shift_service.open_shift(self.conn, self.cashier, "10", now=T0 + timedelta(hours=2))
        self.assertEqual(len(shift_service.list_shifts(self.conn, self.admin)), 2)

    def test_summary_matches_the_drawer(self):
        shift_service.open_shift(self.conn, self.admin, "100", now=T0)
        cash_invoice = self.invoice("A1", 5000, "Cash", at(10))
        card_invoice = self.invoice("A2", 3000, "Card", at(20))
        self.invoice("A3", 2000, credit_service.CREDIT_METHOD, at(30))
        self.invoice("A4", 1000, "Cash", at(40), status="Voided", voided_at=at(50))
        self.invoice("OLD1", 700, "Cash", at(-600), status="Voided", voided_at=at(60))
        self.invoice("OLD2", 900, "Cash", at(-600))
        for invoice_id, refund in ((cash_invoice, 500), (card_invoice, 300)):
            item = insert_row(self.conn, "invoice_items", invoice_id=invoice_id, quantity=1)
            insert_row(self.conn, "sales_returns", invoice_item_id=item, quantity=1, refund_cents=refund,
                       created_at=at(70))
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Sam', '0170')")
        customer = credit_service.customer_id_for(self.conn, "0170")
        for method, cents in (("Cash", 1000), ("Card", 400)):
            insert_row(self.conn, "customer_ledger", customer_id=customer, entry_type="PAYMENT", amount_cents=-cents,
                       method=method, created_at=at(90))
        expense_service.add_expense(self.conn, self.admin, "Rent", "2.50", now=T0 + timedelta(minutes=80))
        expense_service.add_expense(self.conn, self.admin, "Rent", "9.99", paid_from="Other",
                                    now=T0 + timedelta(minutes=81))
        cancelled = expense_service.add_expense(self.conn, self.admin, "Tea / Snacks", "1", now=T0 + timedelta(minutes=82))
        expense_service.void_expense(self.conn, self.admin, cancelled, "entered twice")

        shift, summary = shift_service.live_summary(self.conn, self.admin, now=T0 + timedelta(minutes=120))
        self.assertEqual(summary.opening_cash_cents, 10000)
        self.assertEqual(summary.cash_sales_cents, 5000)
        self.assertEqual(summary.digital_sales, {"Card": 3000})
        self.assertEqual(summary.credit_sales_cents, 2000)
        self.assertEqual(summary.invoices, 3)
        self.assertEqual((summary.due_cash_cents, summary.due_digital), (1000, {"Card": 400}))
        self.assertEqual((summary.cash_refunds_cents, summary.digital_refunds_cents), (700 + 500, 300))
        self.assertEqual(summary.expenses_cash_cents, 250)
        self.assertEqual(summary.expected_cash_cents, 10000 + 5000 + 1000 - 1200 - 250)
        self.assertEqual(summary.digital_net_cents, 3000 + 400 - 300)

        closed = shift_service.close_shift(self.conn, self.admin, "145", "short on change",
                                           now=T0 + timedelta(minutes=120))
        self.assertEqual((closed["status"], closed["expected_cash_cents"], closed["counted_cash_cents"],
                          closed["variance_cents"]), ("Closed", 14550, 14500, -50))
        stored = shift_service.summary_of(self.conn, closed)
        self.assertEqual(stored.expected_cash_cents, 14550)
        self.assertEqual(stored.digital_sales, {"Card": 3000})

    def test_report_fits_every_paper_width(self):
        shift_service.open_shift(self.conn, self.admin, "100", now=T0)
        closed = shift_service.close_shift(self.conn, self.admin, "100", now=T0 + timedelta(hours=1))
        summary = shift_service.summary_of(self.conn, closed)
        for width in (32, 44, 48, 80):
            lines = shift_report.render("A Very Long Business Name For Testing", closed, summary, width)
            self.assertTrue(all(len(line) <= width for line in lines))
            text = "\n".join(lines)
            self.assertIn("EXPECTED CASH", text)
            self.assertIn("Difference", text)

    def test_schema_allows_only_one_open_shift(self):
        shift_service.open_shift(self.conn, self.admin, "0", now=T0)
        with self.assertRaises(Exception):
            insert_row(self.conn, "shifts", opened_by="x", opened_at=at(5), opening_cash_cents=0)


if __name__ == "__main__":
    unittest.main()

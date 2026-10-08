import csv
import os
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import date, datetime
from unittest import mock

from pos_erp import dayrange
from pos_erp.db import connect, ensure_schema
from pos_erp.db.upgrades import LATEST_VERSION, apply_upgrades
from pos_erp.errors import NotFoundError, PermissionDenied, ValidationError
from pos_erp.services import print_service, reporting_service, returns_service, stock_service, till_service
from pos_erp.services.receipt import render_receipt
from tests.helpers import ADMIN, CASHIER, DbTestCase, add_product


class DayRangeTests(unittest.TestCase):
    def test_presets(self):
        today = date(2026, 10, 8)   # a Thursday
        self.assertEqual(dayrange.preset_range("Today", today), (today, today))
        self.assertEqual(dayrange.preset_range("Yesterday", today), (date(2026, 10, 7), date(2026, 10, 7)))
        self.assertEqual(dayrange.preset_range("This week", today), (date(2026, 10, 5), today))
        self.assertEqual(dayrange.preset_range("This month", today), (date(2026, 10, 1), today))
        self.assertEqual(dayrange.preset_range("Last 30 days", today), (date(2026, 9, 9), today))
        with self.assertRaises(ValueError):
            dayrange.preset_range("Custom", today)

    def test_bounds_are_half_open_and_cross_month_ends(self):
        self.assertEqual(dayrange.bounds("2026-10-01", "2026-10-02")[:2], ("2026-10-01 00:00:00", "2026-10-03 00:00:00"))
        self.assertEqual(dayrange.bounds("2026-12-31", "2026-12-31")[1], "2027-01-01 00:00:00")

    def test_bad_input(self):
        for start, end in (("2026/10/01", "2026-10-02"), ("x", "y"), ("2026-10-05", "2026-10-01"),
                           ("1990-01-01", "2026-01-01")):
            with self.assertRaises(ValidationError, msg=f"{start} {end}"):
                dayrange.bounds(start, end)


class UpgradeV3Tests(unittest.TestCase):
    def test_columns_added_and_old_invoices_assumed_paid_exactly(self):
        conn = connect(":memory:")
        ensure_schema(conn)
        conn.execute("INSERT INTO invoices (invoice_no, subtotal_cents, total_cents, payment_method, cashier, created_at)"
                     " VALUES ('INV-X', 500, 500, 'Cash', 'a', '2026-01-01 10:00:00')")
        self.assertEqual(apply_upgrades(conn), [2, 3])
        row = conn.execute("SELECT * FROM invoices").fetchone()
        self.assertEqual((row["tendered_cents"], row["change_cents"], row["status"]), (500, 0, "Completed"))
        self.assertEqual(LATEST_VERSION, 3)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE invoices SET status = 'Weird'")


class CashTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "A", price="10.00", cost="6.00", stock=10)

    def pay(self, **kw):
        return till_service.checkout_with_payment(self.conn, CASHIER, [(self.a, 2)], **kw)

    def test_change_is_computed_and_stored(self):
        invoice = self.pay(tax_rate="10", payment_method="Cash", tendered="50")
        self.assertEqual(invoice.total_cents, 2200)
        meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
        self.assertEqual((meta["tendered_cents"], meta["change_cents"], meta["status"]), (5000, 2800, "Completed"))
        self.assertEqual(self.stock(self.a), 8)

    def test_exact_blank_and_default_method(self):
        for tendered in ("20", "", None):
            invoice = self.pay(tendered=tendered)             # payment method defaults to Cash
            meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
            self.assertEqual((meta["tendered_cents"], meta["change_cents"]), (2000, 0), str(tendered))

    def test_not_enough_cash_rolls_the_whole_sale_back(self):
        with self.assertRaises(ValidationError) as ctx:
            self.pay(tendered="10", customer_name="Sam", customer_phone="0170")
        self.assertIn("less than", str(ctx.exception))
        self.assertEqual(self.stock(self.a), 10)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM invoices"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM customers"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM stock_movements WHERE reason = 'SALE'"), 0)

    def test_invalid_amount_is_rejected_and_rolled_back(self):
        with self.assertRaises(ValidationError):
            self.pay(tendered="abc")
        self.assertEqual(self.stock(self.a), 10)

    def test_non_cash_methods_ignore_the_amount_received(self):
        invoice = self.pay(payment_method="Card", tendered="999")
        meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
        self.assertEqual((meta["tendered_cents"], meta["change_cents"]), (2000, 0))


class VoidTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.b = add_product(self.conn, "B", price="50.00", cost="20.00", stock=10)
        self.invoice = till_service.checkout_with_payment(
            self.conn, ADMIN, [(self.b, 3)], customer_name="Sam", customer_phone="0170")   # 150.00 -> 1 point

    def customer(self):
        row = self.conn.execute("SELECT loyalty_points, total_spent_cents FROM customers WHERE phone = '0170'").fetchone()
        return row[0], row[1]

    def test_void_restores_stock_customer_and_reports(self):
        self.assertEqual((self.stock(self.b), self.customer()), (7, (1, 15000)))
        till_service.void_invoice(self.conn, ADMIN, self.invoice.invoice_no, "Customer changed mind")
        self.assertEqual((self.stock(self.b), self.customer()), (10, (0, 0)))
        meta = till_service.invoice_meta(self.conn, self.invoice.invoice_no)
        self.assertEqual((meta["status"], meta["voided_by"], meta["void_reason"]),
                         ("Voided", "admin", "Customer changed mind"))
        movement = self.conn.execute("SELECT reason, qty_change, note FROM stock_movements WHERE ref_type = 'void'").fetchone()
        self.assertEqual((movement["reason"], movement["qty_change"]), ("RETURN", 3))
        self.assertTrue(movement["note"].startswith("VOID INV-"))
        self.assertEqual(stock_service.reconcile(self.conn, ADMIN), [])
        self.assertEqual(reporting_service.dashboard_stats(self.conn).net_revenue_cents, 0)
        summary = reporting_service.sales_summary(self.conn, ADMIN)
        self.assertEqual((summary.orders, summary.est_gross_profit_cents), (0, 0))
        self.assertEqual(reporting_service.sales_by_category(self.conn, ADMIN), [])
        self.assertTrue(self.scalar("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'Voided invoice%'"))

    def test_cannot_void_twice_or_return_from_a_voided_invoice(self):
        till_service.void_invoice(self.conn, ADMIN, self.invoice.invoice_no, "Mistake")
        with self.assertRaises(ValidationError):
            till_service.void_invoice(self.conn, ADMIN, self.invoice.invoice_no, "Again")
        with self.assertRaises(ValidationError):
            returns_service.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.b, 1)
        with self.assertRaises(ValidationError):
            returns_service.invoice_lines(self.conn, ADMIN, self.invoice.invoice_no)
        self.assertEqual(self.stock(self.b), 10)

    def test_cannot_void_after_a_return(self):
        returns_service.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.b, 1)
        with self.assertRaises(ValidationError) as ctx:
            till_service.void_invoice(self.conn, ADMIN, self.invoice.invoice_no, "Too late")
        self.assertIn("returns", str(ctx.exception))
        self.assertEqual(till_service.invoice_meta(self.conn, self.invoice.invoice_no)["status"], "Completed")
        self.assertEqual(self.stock(self.b), 8)

    def test_reason_permission_and_unknown_invoice(self):
        for reason in ("", "  ", "ab"):
            with self.assertRaises(ValidationError):
                till_service.void_invoice(self.conn, ADMIN, self.invoice.invoice_no, reason)
        with self.assertRaises(PermissionDenied):
            till_service.void_invoice(self.conn, CASHIER, self.invoice.invoice_no, "Not allowed")
        with self.assertRaises(NotFoundError):
            till_service.void_invoice(self.conn, ADMIN, "INV-NOPE", "Whatever")
        self.assertEqual(till_service.invoice_meta(self.conn, self.invoice.invoice_no)["status"], "Completed")


class InvoiceListTests(DbTestCase):
    def sale(self, when, name):
        with mock.patch("pos_erp.timeutil.now", return_value=when):
            return till_service.checkout_with_payment(self.conn, ADMIN, [(self.a, 1)], customer_name=name)

    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "A", price="10.00", stock=50)
        self.first = self.sale(datetime(2026, 10, 1, 10, 0), "Sam")
        self.second = self.sale(datetime(2026, 10, 5, 9, 0), "Rina")

    def numbers(self, **kw):
        return [r["invoice_no"] for r in till_service.list_invoices(self.conn, CASHIER, **kw)]  # cashiers may look

    def test_newest_first_search_and_dates(self):
        self.assertEqual(self.numbers(), [self.second.invoice_no, self.first.invoice_no])
        self.assertEqual(self.numbers(query="sam"), [self.first.invoice_no])
        self.assertEqual(self.numbers(query=self.second.invoice_no), [self.second.invoice_no])
        self.assertEqual(self.numbers(query="%"), [])                                   # wildcard is escaped
        self.assertEqual(self.numbers(date_from="2026-10-05"), [self.second.invoice_no])
        self.assertEqual(self.numbers(date_to="2026-10-01"), [self.first.invoice_no])
        self.assertEqual(self.numbers(date_from="2026-10-02", date_to="2026-10-04"), [])
        with self.assertRaises(ValidationError):
            self.numbers(date_from="yesterday")

    def test_payment_details_need_the_invoices_permission(self):
        invoice, meta = till_service.get_invoice_with_payment(self.conn, CASHIER, self.first.invoice_no)
        self.assertEqual((invoice.invoice_no, meta["status"]), (self.first.invoice_no, "Completed"))
        with self.assertRaises(PermissionDenied):
            till_service.list_invoices(self.conn, type(ADMIN)(9, "x", "Nobody"))


class ReportTests(DbTestCase):
    def sale(self, when, items, **kw):
        with mock.patch("pos_erp.timeutil.now", return_value=when):
            return till_service.checkout_with_payment(self.conn, ADMIN, items, **kw)

    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "A", price="10.00", cost="6.00", stock=100, category="Grocery")
        self.b = add_product(self.conn, "B", price="20.00", cost="5.00", stock=100, category="Electronics")
        self.inv1 = self.sale(datetime(2026, 10, 1, 10, 0), [(self.a, 2), (self.b, 1)])           # 40.00 cash
        self.sale(datetime(2026, 10, 2, 12, 0), [(self.a, 1)], discount="2.00", tax_rate="10",
                  payment_method="Card")                                                           # 8.80 card
        self.sale(datetime(2026, 10, 5, 9, 0), [(self.b, 2)])                                      # 40.00 cash
        voided = self.sale(datetime(2026, 10, 2, 15, 0), [(self.a, 1)])
        till_service.void_invoice(self.conn, ADMIN, voided.invoice_no, "Customer left")
        returns_service.process_return(self.conn, ADMIN, self.inv1.invoice_no, self.a, 1, "Wrong size")  # refund 10.00

    def test_report_for_the_first_two_days(self):
        r = reporting_service.sales_report(self.conn, ADMIN, "2026-10-01", "2026-10-02")
        self.assertEqual((r.orders, r.units, r.gross_cents, r.refunds_cents, r.net_cents), (2, 3, 4880, 1000, 3880))
        self.assertEqual((r.discount_cents, r.tax_cents, r.profit_cents), (200, 80, 2100))
        self.assertEqual((r.voided_orders, r.voided_cents), (1, 1000))
        self.assertEqual([(d.day, d.orders, d.units, d.revenue_cents) for d in r.daily],
                         [("2026-10-01", 1, 2, 3000), ("2026-10-02", 1, 1, 880)])
        self.assertEqual(r.top_products, (("B", 1, 2000), ("A", 2, 1880)))
        self.assertEqual(r.payments, (("Cash", 1, 3000), ("Card", 1, 880)))
        self.assertEqual(r.categories, (("Electronics", 1, 2000), ("Grocery", 2, 1880)))

    def test_single_day_and_empty_range(self):
        r = reporting_service.sales_report(self.conn, ADMIN, "2026-10-05", "2026-10-05")
        self.assertEqual((r.orders, r.net_cents, r.profit_cents, r.voided_orders), (1, 4000, 3000, 0))
        empty = reporting_service.sales_report(self.conn, ADMIN, "2026-09-01", "2026-09-30")
        self.assertEqual((empty.orders, empty.net_cents, empty.daily, empty.top_products), (0, 0, (), ()))

    def test_dashboard_ignores_voided_invoices(self):
        self.assertEqual(reporting_service.dashboard_stats(self.conn).net_revenue_cents, 7880)   # 4000+880+4000-1000
        self.assertEqual(reporting_service.sales_summary(self.conn, ADMIN).orders, 3)

    def test_validation_and_permissions(self):
        with self.assertRaises(PermissionDenied):
            reporting_service.sales_report(self.conn, CASHIER, "2026-10-01", "2026-10-02")
        for call in (reporting_service.low_stock_report, reporting_service.expiry_report,
                     reporting_service.stock_valuation):
            with self.assertRaises(PermissionDenied):
                call(self.conn, CASHIER)
        with self.assertRaises(ValidationError):
            reporting_service.sales_report(self.conn, ADMIN, "2026-10-05", "2026-10-01")

    def test_csv_export(self):
        report = reporting_service.sales_report(self.conn, ADMIN, "2026-10-01", "2026-10-02")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "r.csv")
            reporting_service.export_sales_report_csv(ADMIN, report, path)
            with open(path, newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ["Sales report", "2026-10-01", "2026-10-02"])
        self.assertIn(["Net revenue", "38.80"], rows)
        self.assertIn(["2026-10-01", "1", "2", "30.00"], rows)
        self.assertIn(["B", "1", "20.00"], rows)


class SnapshotReportTests(DbTestCase):
    def test_low_stock(self):
        add_product(self.conn, "Fine", stock=20, min_alert=5)
        add_product(self.conn, "Low", stock=1, min_alert=5)
        rows = reporting_service.low_stock_report(self.conn, ADMIN)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Low")
        self.assertEqual(rows[0][-1], 4)
        self.assertEqual(len(rows[0]), len(reporting_service.LOW_STOCK_HEADER))

    def test_expiry(self):
        add_product(self.conn, "Old", stock=3, cost="2.00", expiry="2000-01")
        add_product(self.conn, "Fresh", stock=3, expiry="2099-01")
        add_product(self.conn, "NoDate", stock=3)
        rows = reporting_service.expiry_report(self.conn, ADMIN)
        self.assertEqual([(r[1], r[4], r[6]) for r in rows], [("Old", "Expired", "6.00")])
        self.assertEqual(len(rows[0]), len(reporting_service.EXPIRY_HEADER))

    def test_stock_valuation(self):
        add_product(self.conn, "A", price="10.00", cost="6.00", stock=10, category="Grocery")
        add_product(self.conn, "B", price="9.00", cost="4.00", stock=5, category="Electronics")
        self.assertEqual(reporting_service.stock_valuation(self.conn, ADMIN),
                         [("Grocery", 1, 10, "60.00", "100.00"), ("Electronics", 1, 5, "20.00", "45.00"),
                          ("TOTAL", 2, 15, "80.00", "145.00")])

    def test_table_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.csv")
            reporting_service.export_table_csv(ADMIN, ("A", "B"), [(1, "x")], path)
            with open(path, newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.reader(handle)), [["A", "B"], ["1", "x"]])


class ReceiptTests(DbTestCase):
    def test_cash_lines_and_void_banner(self):
        a = add_product(self.conn, "A", price="10.00", stock=10)
        invoice = till_service.checkout_with_payment(self.conn, ADMIN, [(a, 2)], tax_rate="10", tendered="50")
        text = render_receipt(invoice, tendered_cents=5000, change_cents=2800)
        for needle in ("TOTAL PAID : $22.00", "Received   : $50.00", "Change     : $28.00"):
            self.assertIn(needle, text)
        self.assertNotIn("VOIDED", text)
        self.assertIn("*** VOIDED ***", render_receipt(invoice, voided=True))
        card = till_service.checkout_with_payment(self.conn, ADMIN, [(a, 1)], payment_method="Card")
        self.assertNotIn("Received", render_receipt(card, tendered_cents=1000, change_cents=0))


class PrintTests(unittest.TestCase):
    def test_sends_the_text_to_lp(self):
        with mock.patch.object(print_service.sys, "platform", "linux"), \
                mock.patch("pos_erp.services.print_service.shutil.which", return_value="/usr/bin/lp"), \
                mock.patch("pos_erp.services.print_service.subprocess.run") as run:
            print_service.print_text("hello ৳")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/lp"])
        self.assertEqual(kwargs["input"], "hello ৳".encode("utf-8"))

    def test_failures_become_print_errors(self):
        with mock.patch.object(print_service.sys, "platform", "linux"):
            with mock.patch("pos_erp.services.print_service.shutil.which", return_value=None):
                with self.assertRaises(print_service.PrintError):
                    print_service.print_text("x")
            failure = subprocess.CalledProcessError(1, "lp", stderr=b"no default destination")
            with mock.patch("pos_erp.services.print_service.shutil.which", return_value="/usr/bin/lp"), \
                    mock.patch("pos_erp.services.print_service.subprocess.run", side_effect=failure):
                with self.assertRaises(print_service.PrintError) as ctx:
                    print_service.print_text("x")
        self.assertIn("no default destination", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

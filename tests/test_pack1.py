import csv
import itertools
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from pos_erp import mailer, timeutil
from pos_erp.errors import PermissionDenied, ValidationError
from pos_erp.services import (backup_service, business_service, credit_service, digest_service, expense_service,
                              mirror_service, print_output, profit_service, reminder_service, returns_service,
                              till_service, user_service)
from tests.helpers import ADMIN, CASHIER, DbTestCase, add_product

TODAY = timeutil.now().date().isoformat()


def fake_clock():
    counter = itertools.count()
    return lambda: datetime(2026, 10, 22, 9, 0, 0) + timedelta(seconds=next(counter))


class BusinessProfileMergeTests(DbTestCase):
    def test_updates_only_touch_the_keys_that_are_passed(self):
        business_service.update_profile(self.conn, ADMIN, {"shop_address": "1 Road", "receipt_paper": "58mm"})
        business_service.update_profile(self.conn, ADMIN, {"country_code": "880", "digest_email": " Owner@Gmail.com "})
        profile = business_service.get_profile(self.conn)
        self.assertEqual((profile["shop_address"], profile["receipt_paper"], profile["country_code"],
                          profile["digest_email"]), ("1 Road", "58mm", "880", "owner@gmail.com"))

    def test_validation(self):
        for bad in ({"country_code": "88a"}, {"country_code": "12345"}, {"reminder_template": "Hi {nme}"},
                    {"reminder_template": "Hi {"}, {"backup_mirror_keep": "0"}, {"backup_mirror_keep": "400"},
                    {"backup_mirror_keep": "x"}, {"digest_email": "nope"}, {"digest_auto": "Maybe"},
                    {"backup_mirror_dir": "x" * 301}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                business_service.update_profile(self.conn, ADMIN, bad)
        business_service.update_profile(self.conn, ADMIN, {"reminder_template": ""})
        self.assertIn("{amount}", business_service.get_profile(self.conn)["reminder_template"])


class MirrorTests(DbTestCase):
    def test_every_automatic_backup_is_copied_and_old_copies_are_pruned(self):
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as mirror:
            business_service.update_profile(self.conn, ADMIN, {"backup_mirror_dir": mirror, "backup_mirror_keep": "2"})
            with mock.patch("pos_erp.timeutil.now", side_effect=fake_clock()):
                for _ in range(3):
                    backup_service.auto_backup(self.conn, local, 10)
            self.assertEqual(len(list(Path(local).glob("backup_*.db"))), 3)
            self.assertEqual(len(list(Path(mirror).glob("backup_*.db"))), 2)
            self.assertTrue(mirror_service.status(self.conn).startswith("OK"))
            copy = next(Path(mirror).glob("backup_*.db"))
            self.assertEqual(sqlite3.connect(copy).execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_a_broken_copy_folder_never_stops_the_backup(self):
        with tempfile.TemporaryDirectory() as local, tempfile.NamedTemporaryFile() as not_a_folder:
            business_service.update_profile(self.conn, ADMIN, {"backup_mirror_dir": not_a_folder.name})
            dest = backup_service.auto_backup(self.conn, local, 5)
            self.assertTrue(dest.exists())
            self.assertTrue(mirror_service.status(self.conn).startswith("FAILED"))

    def test_nothing_happens_without_a_folder(self):
        with tempfile.TemporaryDirectory() as local:
            backup_service.auto_backup(self.conn, local, 5)
        self.assertEqual(mirror_service.status(self.conn), "No copy has been made yet.")

    def test_check_folder_and_backup_now(self):
        with tempfile.NamedTemporaryFile() as not_a_folder:
            with self.assertRaises(ValidationError):
                mirror_service.check_folder(not_a_folder.name)
        with self.assertRaises(ValidationError):
            mirror_service.backup_now(self.conn, ADMIN)
        with tempfile.TemporaryDirectory() as mirror:
            business_service.update_profile(self.conn, ADMIN, {"backup_mirror_dir": mirror})
            with self.assertRaises(PermissionDenied):
                mirror_service.backup_now(self.conn, CASHIER)
            path = mirror_service.backup_now(self.conn, ADMIN)
            self.assertEqual(sqlite3.connect(path).execute("PRAGMA integrity_check").fetchone()[0], "ok")


class ReminderTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Sam', '01712345678')")
        self.cid = credit_service.customer_id_for(self.conn, "01712345678")
        credit_service.post(self.conn, self.cid, "CHARGE", 4000, "admin", note="INV-1")
        credit_service.post(self.conn, self.cid, "PAYMENT", -1000, "admin", method="Cash")

    def test_whatsapp_number(self):
        for phone, code, expected in (("01712345678", "880", "8801712345678"), ("+8801712345678", "", "8801712345678"),
                                      ("008801712345678", "", "8801712345678"), ("1712345678", "880", "8801712345678"),
                                      ("8801712345678", "880", "8801712345678"), ("01712 345-678", "+880", "8801712345678")):
            self.assertEqual(reminder_service.whatsapp_number(phone, code), expected, phone)
        for phone, code in (("01712345678", ""), ("123", "880"), ("", "880")):
            with self.assertRaises(ValidationError, msg=phone):
                reminder_service.whatsapp_number(phone, code)

    def test_reminder_message_and_link(self):
        business_service.update_profile(self.conn, ADMIN, {"country_code": "880"})
        url, text = reminder_service.whatsapp_url(self.conn, CASHIER, "01712345678")
        self.assertIn("Sam", text)
        self.assertIn("$30.00", text)
        self.assertTrue(url.startswith("https://wa.me/8801712345678?text="))
        self.assertNotIn(" ", url)
        business_service.update_profile(self.conn, ADMIN, {"reminder_template": "Dear {name}: {amount} ({shop})"})
        self.assertTrue(reminder_service.reminder_text(self.conn, CASHIER, "01712345678").startswith("Dear Sam: $30.00"))

    def test_no_reminder_without_a_debt_or_a_country_code(self):
        with self.assertRaises(ValidationError):
            reminder_service.whatsapp_url(self.conn, CASHIER, "01712345678")
        credit_service.post(self.conn, self.cid, "PAYMENT", -3000, "admin", method="Cash")
        business_service.update_profile(self.conn, ADMIN, {"country_code": "880"})
        with self.assertRaises(ValidationError):
            reminder_service.reminder_text(self.conn, CASHIER, "01712345678")

    def test_statement(self):
        lines = reminder_service.statement_lines(self.conn, CASHIER, "01712345678")
        text = "\n".join(lines)
        self.assertTrue(all(len(line) <= 80 for line in lines))
        for expected in ("ACCOUNT STATEMENT", "Sam (01712345678)", "Sale on credit", "Payment", "BALANCE DUE"):
            self.assertIn(expected, text)
        self.assertIn("$30.00", text)
        with tempfile.TemporaryDirectory() as folder:
            path = print_output.save_pdf(text, "A4", Path(folder) / "statement.pdf")
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))


class ProfitAndDigestTests(DbTestCase):
    def setUp(self):
        super().setUp()
        user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.pid = add_product(self.conn, "Phone", price="20.00", cost="12.00", stock=50, min_alert=60, barcode="1")
        self.cash = till_service.checkout_with_payment(self.conn, ADMIN, [(self.pid, 2)], tendered="40")
        till_service.checkout_with_payment(self.conn, ADMIN, [(self.pid, 1)], customer_name="Sam",
                                           customer_phone="0170", payment_method=credit_service.CREDIT_METHOD)
        credit_service.receive_payment(self.conn, ADMIN, "0170", "5", "Cash")
        expense_service.add_expense(self.conn, ADMIN, "Rent", "5")

    def test_net_profit(self):
        report = profit_service.profit_report(self.conn, ADMIN, TODAY, TODAY)
        self.assertEqual((report.orders, report.net_revenue_cents, report.gross_profit_cents, report.expenses_cents,
                          report.net_profit_cents), (2, 6000, 2400, 500, 1900))
        self.assertEqual(report.expenses_by_category, (("Rent", 500),))
        with tempfile.TemporaryDirectory() as folder:
            path = profit_service.export_csv(ADMIN, report, Path(folder) / "p.csv")
            rows = list(csv.reader(open(path, encoding="utf-8")))
        self.assertIn(["Net profit", "19.00"], rows)

    def test_returns_and_cancelled_expenses_change_the_profit(self):
        returns_service.process_return(self.conn, ADMIN, self.cash.invoice_no, self.pid, 1, "x")
        expense = expense_service.add_expense(self.conn, ADMIN, "Tea / Snacks", "100")
        expense_service.void_expense(self.conn, ADMIN, expense, "entered twice")
        report = profit_service.profit_report(self.conn, ADMIN, TODAY, TODAY)
        self.assertEqual((report.net_revenue_cents, report.gross_profit_cents, report.net_profit_cents),
                         (4000, 1600, 1100))

    def test_reports_are_for_administrators(self):
        with self.assertRaises(PermissionDenied):
            profit_service.profit_report(self.conn, CASHIER, TODAY, TODAY)

    def test_digest_content(self):
        subject, body = digest_service.build(self.conn, ADMIN)
        self.assertIn(TODAY, subject)
        for expected in ("Orders            : 2", "Net revenue       : $60.00", "Est. gross profit : $24.00",
                         "Expenses          : $5.00", "Est. NET PROFIT   : $19.00", "MONEY OWED BY CUSTOMERS: $15.00",
                         "Sam (0170): $15.00", "LOW STOCK: 1", "Phone: 47 left (minimum 60)"):
            self.assertIn(expected, body)
        self.assertIn("Cash", body)
        with self.assertRaises(ValidationError):
            digest_service.build(self.conn, ADMIN, day="22-10-2026")
        with self.assertRaises(PermissionDenied):
            digest_service.build(self.conn, CASHIER)

    def test_sending(self):
        sent = []
        with self.assertRaises(ValidationError):
            digest_service.send(self.conn, ADMIN, sender=lambda *a: sent.append(a))
        business_service.update_profile(self.conn, ADMIN, {"digest_email": "owner@gmail.com"})
        self.assertEqual(digest_service.send(self.conn, ADMIN, sender=lambda *a: sent.append(a)), "owner@gmail.com")
        self.assertEqual(sent[0][0], "owner@gmail.com")
        self.assertIn("daily summary", sent[0][1])

        def broken(*_args):
            raise mailer.MailError("no network")
        with self.assertRaises(mailer.MailError):
            digest_service.send(self.conn, ADMIN, sender=broken)

    def test_automatic_send_after_closing_a_shift(self):
        sent = []
        self.assertIsNone(digest_service.send_after_close(self.conn, sender=lambda *a: sent.append(a)))
        business_service.update_profile(self.conn, ADMIN, {"digest_email": "owner@gmail.com"})
        self.assertIsNone(digest_service.send_after_close(self.conn, sender=lambda *a: sent.append(a)))
        business_service.update_profile(self.conn, ADMIN, {"digest_auto": "Yes"})
        self.assertEqual(digest_service.send_after_close(self.conn, sender=lambda *a: sent.append(a)),
                         "owner@gmail.com")
        self.assertEqual(len(sent), 1)
        self.assertEqual(digest_service.system_session(self.conn).username, "owner")

    def test_command_line_script(self):
        from scripts import send_digest
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(send_digest.main(["--db", str(Path(folder) / "missing.db")]), 1)


if __name__ == "__main__":
    unittest.main()

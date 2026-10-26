import io
import json
import re
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from pos_erp import config
from pos_erp.bootstrap import open_database
from pos_erp.db import upgrades
from pos_erp.db.connection import transaction
from pos_erp.errors import PermissionDenied, ValidationError
from pos_erp.services import (auth_service, barcode, label_sheet, print_engine, till_service, update_service,
                              user_service)
from tests.helpers import CASHIER, DbTestCase, add_product


def check_pdf(testcase, pdf: bytes) -> None:
    testcase.assertTrue(pdf.startswith(b"%PDF-1.4"))
    testcase.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
    start = int(re.search(rb"startxref\n(\d+)\n%%EOF", pdf).group(1))
    testcase.assertEqual(pdf[start:start + 4], b"xref")
    for number, offset in enumerate(re.findall(rb"(\d{10}) 00000 n ", pdf), start=1):
        testcase.assertTrue(pdf[int(offset):].startswith(f"{number} 0 obj".encode()))


class Code128Tests(unittest.TestCase):
    def test_pattern_table_is_well_formed(self):
        self.assertEqual(len(barcode.PATTERNS), 107)
        self.assertEqual(len(set(barcode.PATTERNS)), 107)
        for pattern in barcode.PATTERNS[:106]:
            self.assertEqual((len(pattern), sum(map(int, pattern))), (6, 11), pattern)
        self.assertEqual((len(barcode.PATTERNS[106]), sum(map(int, barcode.PATTERNS[106]))), (7, 13))

    def test_start_codes_and_checksum(self):
        text = "PJJ123C"
        values = barcode.encode_values(text)
        expected = (104 + sum(i * (ord(c) - 32) for i, c in enumerate(text, start=1))) % 103
        self.assertEqual((values[0], values[-2], values[-1]), (104, expected, 106))
        self.assertEqual(barcode.encode_values("12345678")[0], 105)
        self.assertEqual(barcode.encode_values("1234567")[0], 104)
        self.assertEqual(barcode.encode_values("12345678")[1:5], [12, 34, 56, 78])

    def test_round_trip(self):
        for text in ("A", "Hello-123", "0123456789", "12345678", "  ~!", "8801712345678", "x" * 40):
            self.assertEqual(barcode.decode(barcode.widths(text)), text)

    def test_corrupted_symbols_are_detected(self):
        widths = barcode.widths("HELLO")
        with self.assertRaises(ValueError):
            barcode.decode(widths[:-1])
        broken = list(widths)
        broken[6:12] = [int(c) for c in barcode.PATTERNS[41]]
        with self.assertRaises(ValueError):
            barcode.decode(broken)

    def test_bars_cover_the_symbol(self):
        text = "Phone-1"
        runs = barcode.bar_runs(text)
        self.assertEqual(runs[0][0], 0)
        self.assertEqual(runs[-1][0] + runs[-1][1], barcode.total_modules(text))
        self.assertEqual(sum(width for _offset, width in runs) + sum(barcode.widths(text)[1::2]),
                         barcode.total_modules(text))

    def test_bad_input_is_refused(self):
        for bad in ("", "x" * 41, "caf\u00e9", "\u09e7\u09e8"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                barcode.encode_values(bad)


class LabelSheetTests(unittest.TestCase):
    def test_pages_and_structure(self):
        labels = [label_sheet.Label("12345678", "Phone (black)", "$20.00")] * 30
        pdf = label_sheet.render_sheet(labels)
        check_pdf(self, pdf)
        self.assertIn(b"/Count 2", pdf)
        self.assertIn(rb"Phone \(black\)", pdf)
        self.assertIn(b"/Count 1", label_sheet.render_sheet(labels[:24]))

    def test_skipping_positions_uses_a_partly_used_sheet(self):
        one = [label_sheet.Label("ABC123")]
        plain, skipped = label_sheet.render_sheet(one), label_sheet.render_sheet(one, skip=5)
        self.assertNotEqual(plain, skipped)
        self.assertIn(b"/Count 2", label_sheet.render_sheet(one * 20, skip=5))
        with self.assertRaises(ValidationError):
            label_sheet.render_sheet(one, skip=-1)
        with self.assertRaises(ValidationError):
            label_sheet.render_sheet([label_sheet.Label("caf\u00e9")])


class PaymentRefTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.pid = add_product(self.conn, "Phone", price="20.00", cost="12.00", stock=50, barcode="1")

    def sell(self, **kw):
        kw.setdefault("payment_method", "bKash / Mobile")
        return till_service.checkout_with_payment(self.conn, CASHIER, [(self.pid, 1)], **kw)

    def test_reference_is_stored_searchable_and_printed(self):
        invoice = self.sell(payment_ref="  TX9A81B2 ")
        meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
        self.assertEqual(meta["payment_ref"], "TX9A81B2")
        self.assertEqual([r["invoice_no"] for r in till_service.list_invoices(self.conn, CASHIER, "tx9a81")],
                         [invoice.invoice_no])
        invoice, meta = till_service.get_invoice_with_payment(self.conn, CASHIER, invoice.invoice_no)
        self.assertIn(f"{'Ref':<11}: TX9A81B2", print_engine.prepare_invoice(self.conn, invoice, meta).text)

    def test_no_reference_is_fine_and_cash_receipts_have_no_ref_line(self):
        card = self.sell(payment_method="Card")
        self.assertEqual(till_service.invoice_meta(self.conn, card.invoice_no)["payment_ref"], "")
        cash = till_service.checkout_with_payment(self.conn, CASHIER, [(self.pid, 1)], tendered="50")
        invoice, meta = till_service.get_invoice_with_payment(self.conn, CASHIER, cash.invoice_no)
        self.assertNotIn("Ref ", print_engine.prepare_invoice(self.conn, invoice, meta).text)

    def test_reference_rules(self):
        for kw in ({"payment_method": "Cash", "payment_ref": "TX1"},
                   {"payment_method": "Credit (Due)", "payment_ref": "TX1", "customer_phone": "0170"},
                   {"payment_ref": "x" * 41}):
            with self.assertRaises(ValidationError, msg=str(kw)):
                self.sell(**kw)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM invoices"), 0)
        self.assertEqual(self.stock(self.pid), 50)


class ManagerRoleTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        user_service.create_user(self.conn, self.admin, "mia", "Manager", "temporary-1")
        self.manager = auth_service.authenticate(self.conn, "mia", "temporary-1")

    def test_the_role_is_stored_and_logs_in(self):
        self.assertIn("Manager", user_service.ROLES)
        self.assertEqual(self.manager.role, "Manager")

    def test_what_a_manager_can_and_cannot_do(self):
        for allowed in ("sales.create", "sales.void", "returns.process", "reports.view", "purchasing.manage",
                        "stock.adjust", "system.backup", "customers.lookup"):
            self.assertTrue(self.manager.can(allowed), allowed)
        for denied in ("users.manage", "settings.manage", "system.restore", "lists.manage"):
            self.assertFalse(self.manager.can(denied), denied)
        with self.assertRaises(PermissionDenied):
            user_service.create_user(self.conn, self.manager, "bob", "Cashier", "temporary-1")

    def test_rebuilding_the_users_table_keeps_every_row_and_relationship(self):
        self.conn.execute("UPDATE users SET email = 'owner@gmail.com', recovery_hash = 'h' WHERE id = ?",
                          (self.admin.user_id,))
        self.conn.execute("INSERT INTO email_codes (user_id, purpose, email, code_hash, created_at, expires_at) "
                          "VALUES (?, 'reset', 'owner@gmail.com', 'x', '2026-01-01 00:00:00', '2026-01-01 00:10:00')",
                          (self.admin.user_id,))
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM users ORDER BY id")]
        with transaction(self.conn):
            upgrades._upgrade_v9(self.conn)
        after = [tuple(row) for row in self.conn.execute("SELECT * FROM users ORDER BY id")]
        self.assertEqual(before, after)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM email_codes e JOIN users u ON u.id = e.user_id"), 1)
        self.assertIn("idx_users_email", [row["name"] for row in self.conn.execute("PRAGMA index_list(users)")])
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('x', 'h', 'Boss')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO users (username, password_hash, role, email) "
                              "VALUES ('y', 'h', 'Cashier', 'OWNER@gmail.com')")


class UpdateCheckTests(unittest.TestCase):
    @staticmethod
    def opener(payload):
        return lambda request, timeout: io.BytesIO(json.dumps(payload).encode())

    def test_version_comparison(self):
        for latest, current, newer in (("v2.7.0", "2.6.0", True), ("2.6.0", "2.6.0", False), ("v2.6", "2.6.0", False),
                                       ("v2.10.0", "2.9.9", True), ("v2.5.9", "2.6.0", False), ("2.6.1", "2.6", True)):
            self.assertEqual(update_service.is_newer(latest, current), newer, (latest, current))
        with self.assertRaises(update_service.UpdateError):
            update_service.parse_version("latest")

    def test_check(self):
        info = update_service.check("2.6.0", self.opener({"tag_name": "v2.7.0", "html_url": "https://x/y", "body": "notes"}))
        self.assertEqual((info.latest, info.url, info.newer), ("v2.7.0", "https://x/y", True))
        self.assertFalse(update_service.check("2.7.0", self.opener({"tag_name": "v2.7.0"})).newer)

    def test_failures_become_readable_errors(self):
        def offline(request, timeout):
            raise urllib.error.URLError("no network")

        def missing(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

        with self.assertRaises(update_service.UpdateError) as ctx:
            update_service.check("2.6.0", offline)
        self.assertIn("no network", str(ctx.exception))
        with self.assertRaises(update_service.UpdateError) as ctx:
            update_service.check("2.6.0", missing)
        self.assertIn("No release", str(ctx.exception))
        with self.assertRaises(update_service.UpdateError):
            update_service.check("2.6.0", lambda request, timeout: io.BytesIO(b"not json"))


class OwnerViewTests(unittest.TestCase):
    def test_token_protected_read_only_page(self):
        from scripts import owner_view
        old_symbol = config.CURRENCY_SYMBOL
        self.addCleanup(setattr, config, "CURRENCY_SYMBOL", old_symbol)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shop.db"
            conn = open_database(path)
            user_service.create_first_admin(conn, "owner", "owner-pass-1")
            conn.close()
            server = owner_view.make_server(path, "secret-token", "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                text = urllib.request.urlopen(f"{base}/?token=secret-token", timeout=10).read().decode()
                self.assertIn("daily summary", text)
                self.assertIn("width=device-width", text)
                for bad in ("/", "/?token=wrong", "/?token=secret-token-x", "/other?token=secret-token"):
                    with self.assertRaises(urllib.error.HTTPError, msg=bad) as ctx:
                        urllib.request.urlopen(base + bad, timeout=10)
                    self.assertEqual(ctx.exception.code, 403)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"{base}/?token=secret-token&day=bad", timeout=10)
                self.assertEqual(ctx.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)
        self.assertEqual(owner_view.main(["--db", "/nonexistent/shop.db"]), 1)


if __name__ == "__main__":
    unittest.main()

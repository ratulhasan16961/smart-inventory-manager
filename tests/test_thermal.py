import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pos_erp.errors import PermissionDenied, ValidationError
from pos_erp.permissions import Session
from pos_erp.services import business_service, print_output, print_service, receipt_layout, thermal, user_service
from tests.helpers import DbTestCase


def receipt(**overrides):
    values = dict(
        shop_name="A Very Long Shop Name For Testing", invoice_no="INV-0001", date="2026-10-22 10:00:00",
        cashier="admin", lines=(("Extra Long Product Name For The Receipt Test", 2, 4000), ("Tea", 1, 250)),
        subtotal_cents=4250, discount_cents=100, tax_rate="10", tax_cents=415, total_cents=4565,
        tendered_cents=5000, change_cents=435, address="12 Market Road, Some Long District Name, City",
        phone="0170 000 0000", footer="Thank you for shopping with us, please come again!")
    values.update(overrides)
    return receipt_layout.ReceiptData(**values)


class ThermalHelperTests(unittest.TestCase):
    def test_paper_lookup_and_symbols(self):
        self.assertEqual(thermal.paper_for("58mm").chars, 32)
        self.assertEqual(thermal.paper_for("80mm").chars, 48)
        self.assertEqual(thermal.paper_for("nonsense").name, "80mm")
        self.assertEqual(thermal.ascii_safe("৳100 – ok"), "Tk 100 - ok")

    def test_column_helpers(self):
        self.assertEqual(len(thermal.two_columns("Item", "x2 $10.00", 32)), 32)
        self.assertEqual(len(thermal.two_columns("A" * 80, "x2 $10.00", 32)), 32)
        self.assertEqual(thermal.center("Hi", 10), "    Hi")
        self.assertEqual(thermal.wrap("", 10), [])

    def test_escpos_bytes(self):
        data = thermal.escpos_document(["Hello", "Tk 5"])
        self.assertTrue(data.startswith(b"\x1b@\x1bt\x00"))
        self.assertIn(b"Hello\n", data)
        self.assertTrue(data.endswith(b"\x1dV\x42\x00"))
        self.assertNotIn(b"\x1dV", thermal.escpos_document(["x"], cut=False))
        self.assertTrue(thermal.escpos_document(["x"], open_drawer=True).startswith(b"\x1bp\x00\x19\xfa"))

    def test_pdf_is_well_formed(self):
        pdf = thermal.render_pdf(["Hello (world)", "Total: 10"], thermal.PAPERS["80mm"])
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        start = int(re.search(rb"startxref\n(\d+)\n%%EOF", pdf).group(1))
        self.assertEqual(pdf[start:start + 4], b"xref")
        offsets = re.findall(rb"(\d{10}) 00000 n ", pdf)
        self.assertEqual(len(offsets), 5)
        for number, offset in enumerate(offsets, start=1):
            self.assertTrue(pdf[int(offset):].startswith(f"{number} 0 obj".encode()))
        self.assertIn(rb"Hello \(world\)", pdf)
        width = float(re.search(rb"/MediaBox \[0 0 ([\d.]+) ", pdf).group(1))
        self.assertAlmostEqual(width, 80 / 25.4 * 72, places=1)

    def test_a4_page_is_at_least_a4_high(self):
        pdf = thermal.render_pdf(["x"], thermal.PAPERS["A4"])
        height = float(re.search(rb"/MediaBox \[0 0 [\d.]+ ([\d.]+)\]", pdf).group(1))
        self.assertGreaterEqual(height, 841)


class SendRawTests(unittest.TestCase):
    def test_network_printer(self):
        with mock.patch("pos_erp.services.thermal.socket.create_connection") as create:
            thermal.send_raw(b"data", "tcp://10.0.0.5")
        create.assert_called_once_with(("10.0.0.5", 9100), timeout=10.0)
        create.return_value.__enter__.return_value.sendall.assert_called_once_with(b"data")

    def test_raw_print_queue(self):
        with mock.patch("pos_erp.services.thermal.shutil.which", return_value="/usr/bin/lp"), \
                mock.patch("pos_erp.services.thermal.subprocess.run") as run:
            thermal.send_raw(b"data", "lp:POS80")
        args = run.call_args[0][0]
        self.assertEqual(args, ["/usr/bin/lp", "-d", "POS80", "-o", "raw"])
        self.assertEqual(run.call_args[1]["input"], b"data")

    def test_device_path_and_bad_targets(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "printer.bin"
            thermal.send_raw(b"abc", str(target))
            self.assertEqual(target.read_bytes(), b"abc")
        for bad in ("", "tcp://"):
            with self.assertRaises(ValueError):
                thermal.send_raw(b"x", bad)


class ReceiptLayoutTests(unittest.TestCase):
    def test_every_line_fits_each_paper(self):
        for paper in thermal.PAPERS.values():
            text = receipt_layout.render_text(receipt(), paper.chars)
            for line in text.splitlines():
                self.assertLessEqual(len(line), paper.chars, line)
            self.assertIn("0170 000 0000", text)
            self.assertIn("TOTAL PAID : $45.65", text)
            self.assertIn("Received   : $50.00", text)
            self.assertIn("Change     : $4.35", text)

    def test_credit_sale_lines(self):
        text = receipt_layout.render_text(receipt(payment_method="Credit (Due)", tendered_cents=1500, change_cents=0,
                                                  paid_now_cents=1500, account_balance_cents=3065), 48)
        for expected in ("TOTAL      : $45.65", "Paid now   : $15.00", "On credit  : $30.65", "Account due: $30.65"):
            self.assertIn(expected, text)
        self.assertNotIn("Received", text)

    def test_voided_and_points(self):
        text = receipt_layout.render_text(receipt(voided=True, points_redeemed=2, points_value_cents=200), 48)
        self.assertIn("*** VOIDED ***", text)
        self.assertIn("2 loyalty point(s)", text)

    def test_sample_receipt(self):
        text = receipt_layout.render_text(receipt_layout.sample("Demo Mart", "Thanks", "1 Road", "123"), 32)
        self.assertIn("TEST-0001", text)
        self.assertIn("DEMO MART RECEIPT", text)


class PrintOutputTests(unittest.TestCase):
    def test_thermal_paper_with_target_sends_escpos(self):
        with mock.patch("pos_erp.services.thermal.send_raw") as send:
            print_output.print_document("line one\nline two", "80mm", "tcp://1.2.3.4:9100")
        data, target = send.call_args[0]
        self.assertTrue(data.startswith(b"\x1b@"))
        self.assertEqual(target, "tcp://1.2.3.4:9100")

    def test_a4_and_no_target_use_the_system_printer(self):
        with mock.patch("pos_erp.services.print_service.print_text") as system_print:
            print_output.print_document("text", "A4", "tcp://1.2.3.4:9100")
            print_output.print_document("text", "80mm", "")
        self.assertEqual(system_print.call_count, 2)

    def test_printer_failure_becomes_a_print_error(self):
        with mock.patch("pos_erp.services.thermal.send_raw", side_effect=OSError("offline")):
            with self.assertRaises(print_service.PrintError) as ctx:
                print_output.print_document("text", "58mm", "tcp://1.2.3.4")
        self.assertIn("offline", str(ctx.exception))

    def test_save_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            path = print_output.save_pdf("hello\nworld", "58mm", Path(folder) / "r.pdf")
            self.assertTrue(path.read_bytes().startswith(b"%PDF"))


class BusinessProfileTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")

    def test_defaults_update_and_overwrite(self):
        self.assertEqual(business_service.get_profile(self.conn)["receipt_paper"], "80mm")
        business_service.update_profile(self.conn, self.admin, {"shop_address": "1 Road", "shop_phone": "0170",
                                                                "receipt_paper": "58mm", "printer_target": "lp:POS"})
        profile = business_service.get_profile(self.conn)
        self.assertEqual((profile["shop_address"], profile["receipt_paper"], profile["printer_target"]),
                         ("1 Road", "58mm", "lp:POS"))
        business_service.update_profile(self.conn, self.admin, {**profile, "shop_phone": "0180"})
        self.assertEqual(business_service.get_profile(self.conn)["shop_phone"], "0180")

    def test_validation_and_permissions(self):
        for bad in ({"receipt_paper": "99mm"}, {"shop_address": "x" * 121}, {"printer_target": "tcp://"},
                    {"printer_target": "tcp://host:abc"}, {"printer_target": "lp:"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                business_service.update_profile(self.conn, self.admin, bad)
        with self.assertRaises(PermissionDenied):
            business_service.update_profile(self.conn, Session(2, "sara", "Cashier"), {})

    def test_shop_basics_fall_back_to_defaults(self):
        self.assertTrue(business_service.shop_basics(self.conn)["shop_name"])


if __name__ == "__main__":
    unittest.main()

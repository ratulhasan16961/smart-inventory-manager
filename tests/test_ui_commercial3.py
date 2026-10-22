"""UI smoke tests: credit at the till, receipt window, Register tab (skipped without tkinter or a display)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import tkinter as tk
    import tkinter.filedialog
    import tkinter.messagebox
except ImportError:
    tk = None

from pos_erp import config
from pos_erp.services import credit_service, shift_service, till_service
from tests.helpers import ADMIN, CASHIER, add_product, make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiCommercial3Tests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display available: {exc}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.dialogs = {name: mock.patch(f"tkinter.messagebox.{name}", return_value=True).start() for name in DIALOGS}
        self.addCleanup(mock.patch.stopall)
        self.conn = make_conn()
        self.addCleanup(self.conn.close)
        from pos_erp.ui.widgets import apply_theme
        apply_theme(self.root)
        add_product(self.conn, "Phone", price="20.00", cost="12.00", stock=50, barcode="1")

    def window(self, session=CASHIER):
        from pos_erp.ui.main_window import MainWindow
        return MainWindow(self.root, self.conn, session)

    def fill_cart(self, pos, phone="0170"):
        pos.prod_id_e.insert(0, "1")
        pos.add_to_cart()
        pos.cust_name_e.insert(0, "Sam")
        if phone:
            pos.cust_phone_e.insert(0, phone)

    def test_credit_sale_from_the_pos_tab(self):
        pos = self.window().pos
        self.assertIn(config.PAYMENT_METHODS[0], pos.pay_combo.cget("values"))
        self.assertIn(credit_service.CREDIT_METHOD, pos.pay_combo.cget("values"))
        self.fill_cart(pos)
        pos.pay_combo.set(credit_service.CREDIT_METHOD)
        pos._on_payment_change()
        self.assertEqual(str(pos.received_e.cget("state")), "normal")
        self.assertIn("Paid now", pos.lbl_received.cget("text"))
        pos.received_e.insert(0, "5")
        pos._update_change()
        self.assertIn("On credit: $15.00", pos.lbl_change.cget("text"))
        pos.process_sale()
        self.dialogs["showerror"].assert_not_called()
        row = self.conn.execute("SELECT payment_method, tendered_cents FROM invoices").fetchone()
        self.assertEqual((row[0], row[1]), (credit_service.CREDIT_METHOD, 500))
        self.assertEqual(credit_service.balance_of_phone(self.conn, "0170"), 1500)
        self.assertEqual(pos.received_e.get(), "")

    def test_credit_without_a_phone_number_is_refused(self):
        pos = self.window().pos
        self.fill_cart(pos, phone="")
        pos.pay_combo.set(credit_service.CREDIT_METHOD)
        pos._on_payment_change()
        pos.process_sale()
        self.dialogs["showerror"].assert_called_once()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 0)

    def test_switching_payment_type_clears_the_received_field(self):
        pos = self.window().pos
        pos.received_e.insert(0, "50")
        pos.pay_combo.set(credit_service.CREDIT_METHOD)
        pos._on_payment_change()
        self.assertEqual(pos.received_e.get(), "")
        pos.pay_combo.set("Card")
        pos._on_payment_change()
        self.assertEqual(str(pos.received_e.cget("state")), "disabled")

    def test_lookup_shows_what_the_customer_owes_and_the_ledger_opens(self):
        till_service.checkout_with_payment(self.conn, CASHIER, [(1, 2)], customer_name="Sam", customer_phone="0170",
                                           payment_method=credit_service.CREDIT_METHOD)
        pos = self.window().pos
        pos.cust_phone_e.insert(0, "0170")
        pos.lookup_customer()
        self.assertIn("OWES $40.00", pos.lbl_loyalty.cget("text"))
        pos.show_ledger()
        self.dialogs["showerror"].assert_not_called()
        pos.cust_phone_e.delete(0, tk.END)
        pos.show_ledger()
        self.dialogs["showwarning"].assert_called_once()

    def test_receipt_window_prints_exports_and_saves_a_pdf(self):
        from pos_erp.ui.dialogs.sales_dialogs import show_receipt
        invoice = till_service.checkout_with_payment(self.conn, ADMIN, [(1, 1)], tendered="50")
        receipt = show_receipt(self.window(ADMIN).ctx, invoice)
        with mock.patch("pos_erp.services.print_output.print_document") as printed:
            receipt.print_receipt()
        self.assertIn(invoice.invoice_no, printed.call_args[0][0])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "receipt.pdf"
            with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(target)):
                receipt.save_pdf()
            self.assertTrue(target.read_bytes().startswith(b"%PDF"))
            with mock.patch.object(config, "INVOICE_DIR", Path(folder) / "invoices"):
                receipt.export_txt()
            self.assertTrue((Path(folder) / "invoices" / f"Invoice_{invoice.invoice_no}.txt").exists())
        self.dialogs["showerror"].assert_not_called()

    def test_register_tab_for_cashier_and_admin(self):
        for session in (CASHIER, ADMIN):
            window = self.window(session)
            self.assertIsNotNone(window.register)
        window = self.window(CASHIER)
        self.assertIn("No shift is open", window.register.status.cget("text"))
        shift_service.open_shift(self.conn, CASHIER, "100")
        window.refresh()
        self.assertIn("EXPECTED CASH", window.register.report.cget("text"))
        self.assertFalse(window.register.buttons["history"].enabled)
        self.dialogs["showerror"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

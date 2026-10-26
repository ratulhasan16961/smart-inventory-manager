"""UI smoke tests: labels, payment reference at the till, update check, a Manager's window."""
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

from pos_erp.services import auth_service, till_service, update_service, user_service
from tests.helpers import add_product, make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiPack2Tests(unittest.TestCase):
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
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        add_product(self.conn, "Phone", price="20.00", cost="12.00", stock=50, barcode="1")

    def window(self, session=None):
        from pos_erp.ui.main_window import MainWindow
        return MainWindow(self.root, self.conn, session or self.admin)

    def test_label_dialog_previews_and_saves_a_sheet(self):
        from pos_erp.ui.dialogs.label_dialogs import open_barcode_generator
        dialog = open_barcode_generator(self.window().ctx)
        dialog.code_e.insert(0, "1")
        dialog.lookup()
        self.assertEqual((dialog.code_e.get(), dialog.name_e.get(), dialog.price_e.get()), ("1", "Phone", "$20.00"))
        dialog.preview()
        self.assertGreater(len(dialog.canvas.find_all()), 10)
        dialog.copies_e.delete(0, tk.END)
        dialog.copies_e.insert(0, "30")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "labels.pdf"
            with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(target)):
                dialog.save_pdf()
            self.assertIn(b"/Count 2", target.read_bytes())
        self.dialogs["showerror"].assert_not_called()

    def test_label_dialog_refuses_bad_input(self):
        from pos_erp.ui.dialogs.label_dialogs import open_barcode_generator
        dialog = open_barcode_generator(self.window().ctx)
        dialog.preview()
        dialog.code_e.insert(0, "caf\u00e9")
        dialog.preview()
        self.assertEqual(self.dialogs["showerror"].call_count, 2)

    def test_payment_reference_field_follows_the_payment_method(self):
        pos = self.window().pos
        self.assertEqual(str(pos.ref_e.cget("state")), "disabled")
        pos.pay_combo.set("bKash / Mobile")
        pos._on_payment_change()
        self.assertEqual(str(pos.ref_e.cget("state")), "normal")
        pos.prod_id_e.insert(0, "1")
        pos.add_to_cart()
        pos.ref_e.insert(0, "TX123ABC")
        pos.process_sale()
        self.dialogs["showerror"].assert_not_called()
        row = self.conn.execute("SELECT payment_method, payment_ref FROM invoices").fetchone()
        self.assertEqual((row[0], row[1]), ("bKash / Mobile", "TX123ABC"))
        self.assertEqual(pos.ref_e.get(), "")
        pos.pay_combo.set("Cash")
        pos._on_payment_change()
        self.assertEqual(str(pos.ref_e.cget("state")), "disabled")

    def test_update_check_buttons(self):
        from pos_erp.ui.dialogs.tools_dialogs import open_tools
        dialog = open_tools(self.window().ctx)
        newer = update_service.UpdateInfo("2.6.0", "v9.9.9", "https://example.com/release", "", True)
        with mock.patch("pos_erp.services.update_service.check", return_value=newer), \
                mock.patch("webbrowser.open") as opened:
            dialog.check_updates()
        opened.assert_called_once_with("https://example.com/release")
        current = update_service.UpdateInfo("2.6.0", "v2.6.0", "https://example.com/release", "", False)
        with mock.patch("pos_erp.services.update_service.check", return_value=current), \
                mock.patch("webbrowser.open") as opened:
            dialog.check_updates()
        opened.assert_not_called()
        with mock.patch("pos_erp.services.update_service.check", side_effect=update_service.UpdateError("offline")):
            dialog.check_updates()
        self.dialogs["showerror"].assert_called_once()

    def test_a_managers_window(self):
        user_service.create_user(self.conn, self.admin, "mia", "Manager", "temporary-1")
        window = self.window(auth_service.authenticate(self.conn, "mia", "temporary-1"))
        self.assertIsNotNone(window.dashboard)
        self.assertIsNotNone(window.register)
        self.assertTrue(window.register.buttons["profit"].enabled)
        invoice = till_service.checkout_with_payment(self.conn, self.admin, [(1, 1)], tendered="50")
        till_service.void_invoice(self.conn, window.ctx.session, invoice.invoice_no, "wrong item")
        self.dialogs["showerror"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

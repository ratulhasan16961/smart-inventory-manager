"""UI smoke tests for the ledger and receipt dialogs (skipped when tkinter or a display is unavailable)."""
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

from pos_erp.services import business_service, credit_service, user_service
from tests.helpers import make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiCommercialTests(unittest.TestCase):
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
        from pos_erp.ui.main_window import MainWindow
        self.session = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.ctx = MainWindow(self.root, self.conn, self.session).ctx
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Sam', '0170')")
        credit_service.post(self.conn, credit_service.customer_id_for(self.conn, "0170"), "CHARGE", 4000, "owner")

    def test_ledger_dialog_shows_and_refreshes_the_balance(self):
        from pos_erp.ui.dialogs.ledger_dialogs import open_customer_ledger
        dialog = open_customer_ledger(self.ctx, "0170")
        self.assertEqual(len(dialog.tree.get_children()), 1)
        self.assertIn("$40.00", dialog.balance.cget("text"))
        credit_service.receive_payment(self.conn, self.session, "0170", "10", "Cash")
        dialog.load()
        self.assertIn("$30.00", dialog.balance.cget("text"))
        self.assertEqual(len(dialog.tree.get_children()), 2)

    def test_ledger_dialog_refuses_unknown_or_empty_phone(self):
        from pos_erp.ui.dialogs.ledger_dialogs import open_customer_ledger
        self.assertIsNone(open_customer_ledger(self.ctx, "999"))
        self.assertIsNone(open_customer_ledger(self.ctx, "  "))
        self.dialogs["showerror"].assert_called_once()
        self.dialogs["showwarning"].assert_called_once()

    def test_receivables_list_and_form_helper(self):
        from pos_erp.ui.dialogs.ledger_dialogs import ask_values, open_receivables
        dialog = open_receivables(self.ctx)
        self.assertEqual(len(dialog.tree.get_children()), 1)
        self.assertIn("$40.00", dialog.total.cget("text"))
        received = []
        form = ask_values(self.root, "Test", [("a", "A")], "OK", received.append)
        form.submit()
        self.assertEqual(received, [{"a": ""}])
        self.root.update()
        self.assertFalse(form.winfo_exists())

    def test_receipt_settings_dialog_saves_and_tests(self):
        from pos_erp.ui.dialogs.receipt_dialogs import open_receipt_settings
        dialog = open_receipt_settings(self.ctx)
        dialog.entries["shop_address"].insert(0, "12 Market Road")
        dialog.entries["shop_phone"].insert(0, "0170 000 000")
        dialog.paper_combo.set("58mm")
        dialog.save()
        profile = business_service.get_profile(self.conn)
        self.assertEqual((profile["shop_address"], profile["receipt_paper"]), ("12 Market Road", "58mm"))
        with mock.patch("pos_erp.services.print_output.print_document") as printed:
            dialog.print_test()
        text, paper, _target = printed.call_args[0]
        self.assertEqual(paper, "58mm")
        self.assertIn("12 Market Road", text)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "test.pdf"
            with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(target)):
                dialog.save_test_pdf()
            self.assertTrue(target.read_bytes().startswith(b"%PDF"))
        self.dialogs["showerror"].assert_not_called()

    def test_settings_window_still_opens_with_the_new_buttons(self):
        from pos_erp.ui.dialogs import admin_dialogs
        admin_dialogs.open_settings(self.ctx)
        self.dialogs["showerror"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

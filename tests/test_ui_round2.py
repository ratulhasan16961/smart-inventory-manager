"""UI smoke tests for Round 2 (skipped when tkinter or a display is unavailable)."""
import unittest
from unittest import mock

try:
    import tkinter as tk
    import tkinter.messagebox  # noqa: F401
except ImportError:  # pragma: no cover
    tk = None

from tests.helpers import ADMIN, CASHIER, add_product, make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiRound2Tests(unittest.TestCase):
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
        add_product(self.conn, "Mouse", price="15.00", cost="8.00", stock=10, barcode="111")

    def window(self, session=ADMIN):
        from pos_erp.ui.main_window import MainWindow
        return MainWindow(self.root, self.conn, session)

    def fill_cart(self, pos):
        pos.prod_id_e.insert(0, "111")
        pos.prod_qty_e.insert(0, "2")        # 2 x 15.00 = 30.00
        pos.add_to_cart()

    def paid(self):
        row = self.conn.execute("SELECT tendered_cents, change_cents FROM invoices").fetchone()
        return (row[0], row[1]) if row else None

    def test_cash_sale_shows_and_stores_change(self):
        pos = self.window().pos
        self.fill_cart(pos)
        pos.received_e.insert(0, "50")
        pos.update_summary()
        self.assertIn("Change due: $20.00", pos.lbl_change.cget("text"))
        pos.process_sale()
        self.assertEqual(self.paid(), (5000, 2000))
        self.dialogs["showerror"].assert_not_called()

    def test_short_cash_blocks_the_sale(self):
        pos = self.window().pos
        self.fill_cart(pos)
        pos.received_e.insert(0, "10")
        pos.update_summary()
        self.assertIn("Short by $20.00", pos.lbl_change.cget("text"))
        pos.process_sale()
        self.assertIsNone(self.paid())
        self.assertEqual(len(pos.cart), 1)
        self.dialogs["showerror"].assert_called_once()

    def test_card_payment_turns_the_received_field_off(self):
        pos = self.window().pos
        self.fill_cart(pos)
        pos.pay_combo.set("Card")
        pos._on_payment_change()
        self.assertEqual(str(pos.received_e.cget("state")), "disabled")
        pos.process_sale()
        self.assertEqual(self.paid(), (3000, 0))
        self.dialogs["showerror"].assert_not_called()

    def test_invoices_and_reports_windows_open_for_admin(self):
        from pos_erp.ui.dialogs import report_dialogs, sales_dialogs
        win = self.window()
        self.fill_cart(win.pos)
        win.pos.process_sale()
        sales_dialogs.open_invoices(win.ctx)
        report_dialogs.open_reports(win.ctx)
        self.root.update_idletasks()
        self.dialogs["showerror"].assert_not_called()

    def test_cashier_can_open_invoices_but_not_reports(self):
        from pos_erp.ui.dialogs import report_dialogs, sales_dialogs
        ctx = self.window(CASHIER).ctx
        sales_dialogs.open_invoices(ctx)
        self.dialogs["showerror"].assert_not_called()
        report_dialogs.open_reports(ctx)
        self.dialogs["showerror"].assert_called_once()      # "Access Denied"


if __name__ == "__main__":
    unittest.main()

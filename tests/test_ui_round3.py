"""UI smoke tests for Round 3 (skipped when tkinter or a display is unavailable)."""
import unittest
from unittest import mock

try:
    import tkinter as tk
    import tkinter.messagebox
except ImportError:
    tk = None

from pos_erp.services import purchase_service, till_service
from tests.helpers import ADMIN, CASHIER, add_product, make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiRound3Tests(unittest.TestCase):
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

    def window(self, session=ADMIN):
        from pos_erp.ui.main_window import MainWindow
        return MainWindow(self.root, self.conn, session)

    def names(self, tab):
        return [tab.tree.item(item, "values")[1] for item in tab.tree.get_children()]

    def test_dashboard_tab_only_exists_with_the_reports_permission(self):
        add_product(self.conn, "A", price="10.00", stock=10, barcode="1")
        till_service.checkout_with_payment(self.conn, ADMIN, [(1, 2)])
        admin_window = self.window()
        self.assertIsNotNone(admin_window.dashboard)
        self.assertEqual(len(admin_window.dashboard.data), 30)
        self.assertEqual(admin_window.dashboard.data[-1][1], 2000)
        self.dialogs["showerror"].assert_not_called()

    def test_cashier_has_no_dashboard(self):
        self.assertIsNone(self.window(CASHIER).dashboard)

    def prepare_customer(self):
        add_product(self.conn, "Phone", price="150.00", cost="100.00", stock=20, barcode="999")
        till_service.checkout_with_payment(self.conn, ADMIN, [(1, 2)], customer_name="Sam",
                                           customer_phone="0170")

    def test_lookup_fills_the_name_and_shows_points(self):
        self.prepare_customer()
        pos = self.window(CASHIER).pos
        pos.cust_phone_e.insert(0, "0170")
        pos.lookup_customer()
        self.assertIn("3 points", pos.lbl_loyalty.cget("text"))
        self.assertEqual(pos.cust_name_e.get(), "Sam")
        pos.cust_phone_e.delete(0, tk.END)
        pos.cust_phone_e.insert(0, "0999")
        pos.lookup_customer()
        self.assertIn("New customer", pos.lbl_loyalty.cget("text"))
        self.dialogs["showerror"].assert_not_called()

    def test_redeeming_points_lowers_the_total_and_is_stored(self):
        self.prepare_customer()
        pos = self.window().pos
        pos.cust_phone_e.insert(0, "0170")
        pos.prod_id_e.insert(0, "999")
        pos.add_to_cart()
        pos.points_e.insert(0, "2")
        pos.update_summary()
        self.assertIn("TOTAL: $148.00", pos.lbl_summary.cget("text"))
        pos.process_sale()
        row = self.conn.execute("SELECT points_redeemed, total_cents FROM invoices ORDER BY id DESC").fetchone()
        self.assertEqual((row[0], row[1]), (2, 14800))
        self.assertEqual(self.conn.execute("SELECT loyalty_points FROM customers").fetchone()[0], 2)
        self.dialogs["showerror"].assert_not_called()

    def test_too_many_points_are_refused(self):
        self.prepare_customer()
        pos = self.window().pos
        pos.cust_phone_e.insert(0, "0170")
        pos.prod_id_e.insert(0, "999")
        pos.add_to_cart()
        pos.points_e.insert(0, "50")
        pos.process_sale()
        self.dialogs["showerror"].assert_called_once()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)

    def test_history_window(self):
        from pos_erp.ui.dialogs.customer_dialogs import open_customer_history
        self.prepare_customer()
        ctx = self.window(CASHIER).ctx
        open_customer_history(ctx, "0170")
        self.dialogs["showerror"].assert_not_called()
        open_customer_history(ctx, "0999")
        self.dialogs["showinfo"].assert_called_once()

    def test_clicking_headings_sorts_and_filters_narrow_the_table(self):
        add_product(self.conn, "A", stock=5, min_alert=2, category="General")
        add_product(self.conn, "B", stock=1, min_alert=3, category="Grocery")
        add_product(self.conn, "C", stock=9, min_alert=2, category="General")
        tab = self.window().inventory
        tab.sort_by("Stock")
        self.assertEqual(self.names(tab), ["B", "A", "C"])
        self.assertIn("▲", tab.tree.heading("Stock", option="text"))
        tab.sort_by("Stock")
        self.assertEqual(self.names(tab), ["C", "A", "B"])
        self.assertIn("▼", tab.tree.heading("Stock", option="text"))
        tab.only_low.set(True)
        tab.refresh_table()
        self.assertEqual(self.names(tab), ["B"])
        tab.reset_search()
        self.assertEqual(self.names(tab), ["A", "B", "C"])
        tab.filter_category.set("Grocery")
        tab.refresh_table()
        self.assertEqual(self.names(tab), ["B"])
        self.dialogs["showerror"].assert_not_called()

    def test_reorder_dialog(self):
        from pos_erp.ui.dialogs.reorder_dialog import open_reorder
        ctx = self.window().ctx
        open_reorder(ctx, lambda: None)
        self.dialogs["showinfo"].assert_called_once()
        add_product(self.conn, "Tea", cost="3.00", stock=1, min_alert=5)
        open_reorder(ctx, lambda: None)
        self.dialogs["showwarning"].assert_called_once()
        purchase_service.add_supplier(self.conn, ADMIN, "Acme")
        open_reorder(ctx, lambda: None)
        self.root.update_idletasks()
        self.dialogs["showerror"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

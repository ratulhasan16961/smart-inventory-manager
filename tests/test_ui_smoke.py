"""UI smoke tests: build the real Tk windows against an in-memory database.

Skipped automatically when tkinter or a display is unavailable (for example on CI).
Message boxes are mocked so nothing blocks, and every test checks that no error dialog appeared.
"""
import unittest
from unittest import mock

try:
    import tkinter as tk
    import tkinter.messagebox  # noqa: F401  (imported so it can be patched)
except ImportError:  # pragma: no cover
    tk = None

from pos_erp.services import auth_service, sales_service
from tests.helpers import ADMIN, CASHIER, add_product, make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiSmokeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display available: {exc}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.dialogs = {name: mock.patch(f"tkinter.messagebox.{name}", return_value=True).start()
                        for name in DIALOGS}
        self.addCleanup(mock.patch.stopall)
        self.conn = make_conn()
        self.addCleanup(self.conn.close)
        from pos_erp.ui.widgets import apply_theme
        apply_theme(self.root)

    # ------------------------------------------------------------------ helpers
    def window(self, session=ADMIN):
        from pos_erp.ui.main_window import MainWindow
        return MainWindow(self.root, self.conn, session)

    def assert_no_errors(self):
        self.dialogs["showerror"].assert_not_called()

    def scalar(self, sql):
        return self.conn.execute(sql).fetchone()[0]

    def login_view(self):
        from pos_erp.ui.login import LoginView
        auth_service.ensure_default_users(self.conn)
        sessions = []
        return LoginView(self.root, self.conn, sessions.append), sessions

    # ------------------------------------------------------------------ main window
    def test_main_window_builds_and_shows_stats(self):
        add_product(self.conn, "Mouse", price="15.00", cost="8.00", stock=10, barcode="111")
        win = self.window()
        self.assertEqual(win.cards["items"].cget("text"), "Total Items: 1")
        self.assertEqual(len(win.inventory.tree.get_children()), 1)
        self.assert_no_errors()

    def test_cashier_window_builds(self):
        add_product(self.conn, "Mouse", barcode="111")
        win = self.window(CASHIER)
        self.assertEqual(len(win.inventory.tree.get_children()), 1)
        self.assert_no_errors()

    def test_pos_sale_flow(self):
        add_product(self.conn, "Mouse", price="15.00", cost="8.00", stock=10, barcode="111")
        pos = self.window().pos
        pos.prod_id_e.insert(0, "111")
        pos.prod_qty_e.insert(0, "2")
        pos.add_to_cart()
        self.assertEqual([(l["name"], l["qty"]) for l in pos.cart], [("Mouse", 2)])
        pos.process_sale()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM invoices"), 1)
        self.assertEqual(self.scalar("SELECT stock FROM products"), 8)
        self.assertEqual(pos.cart, [])
        self.assert_no_errors()

    def test_pos_refuses_to_oversell(self):
        add_product(self.conn, "Mouse", stock=3, barcode="111")
        pos = self.window().pos
        pos.prod_id_e.insert(0, "111")
        pos.prod_qty_e.insert(0, "99")
        pos.add_to_cart()
        self.assertEqual(pos.cart, [])
        self.dialogs["showerror"].assert_called_once()

    def test_inventory_form_adds_a_product(self):
        win = self.window()
        fields = win.inventory.fields
        for label, value in (("Name", "Tea"), ("Selling Price", "5.50"), ("Buying Price", "3"),
                             ("Stock", "4"), ("Min Alert", "5")):
            fields[label].insert(0, value)
        win.inventory.add_product()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM products"), 1)
        self.assertEqual(len(win.inventory.tree.get_children()), 1)
        self.dialogs["showinfo"].assert_called()
        self.assert_no_errors()

    def test_every_dialog_opens_without_errors(self):
        from pos_erp.ui.dialogs import admin_dialogs, inventory_dialogs, purchasing_dialogs, sales_dialogs
        pid = add_product(self.conn, "Mouse", price="15.00", barcode="111")
        sales_service.checkout(self.conn, ADMIN, [(pid, 1)])
        ctx = self.window().ctx
        for opener in (sales_dialogs.open_returns, inventory_dialogs.open_barcode_generator,
                       purchasing_dialogs.open_suppliers, purchasing_dialogs.open_purchase_orders,
                       admin_dialogs.open_stock_ledger, admin_dialogs.open_audit_logs,
                       admin_dialogs.open_analytics, admin_dialogs.open_settings, admin_dialogs.open_customers):
            opener(ctx)
        inventory_dialogs.open_edit_product(ctx, pid)
        self.root.update_idletasks()
        self.assert_no_errors()

    # ------------------------------------------------------------------ login
    def test_login_with_valid_password(self):
        view, sessions = self.login_view()
        self.conn.execute("UPDATE users SET must_change_password = 0")
        view.user_entry.insert(0, "admin")
        view.pass_entry.insert(0, "1234")
        view.authenticate()
        self.assertEqual([s.username for s in sessions], ["admin"])

    def test_login_with_wrong_password(self):
        view, sessions = self.login_view()
        view.user_entry.insert(0, "admin")
        view.pass_entry.insert(0, "nope")
        view.authenticate()
        self.assertEqual(sessions, [])
        self.dialogs["showerror"].assert_called_once()

    def test_temporary_password_does_not_open_the_app(self):
        view, sessions = self.login_view()
        view.user_entry.insert(0, "admin")
        view.pass_entry.insert(0, "1234")
        view.authenticate()          # switches to the "choose a new password" screen
        self.assertEqual(sessions, [])
        self.assert_no_errors()


if __name__ == "__main__":
    unittest.main()

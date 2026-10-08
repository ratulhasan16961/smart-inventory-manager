"""UI smoke tests for the Round 1 screens (skipped when tkinter or a display is unavailable)."""
import unittest
from unittest import mock

try:
    import tkinter as tk
    import tkinter.messagebox  # noqa: F401
except ImportError:  # pragma: no cover
    tk = None

from pos_erp.services import user_service
from tests.helpers import ADMIN, CASHIER, make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiRound1Tests(unittest.TestCase):
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

    def test_administration_dialogs_open_for_admin(self):
        from pos_erp.ui.dialogs import admin_dialogs, system_dialogs
        from pos_erp.ui.main_window import MainWindow
        ctx = MainWindow(self.root, self.conn, ADMIN).ctx
        for opener in (system_dialogs.open_shop_settings, system_dialogs.open_user_management,
                       system_dialogs.open_backup_restore, system_dialogs.open_manage_lists,
                       admin_dialogs.open_settings):
            opener(ctx)
        self.root.update_idletasks()
        self.dialogs["showerror"].assert_not_called()

    def test_settings_window_opens_for_cashier_with_buttons_disabled(self):
        from pos_erp.ui.dialogs import admin_dialogs
        from pos_erp.ui.main_window import MainWindow
        ctx = MainWindow(self.root, self.conn, CASHIER).ctx
        admin_dialogs.open_settings(ctx)
        self.root.update_idletasks()
        self.dialogs["showerror"].assert_not_called()

    def test_first_run_setup_screen_creates_the_admin(self):
        from pos_erp.ui.login import LoginView
        sessions = []
        view = LoginView(self.root, self.conn, sessions.append)       # empty users table -> setup screen
        view.user_entry.delete(0, tk.END)
        view.user_entry.insert(0, "owner")
        view.pass_entry.insert(0, "owner-pass-1")
        view.confirm_entry.insert(0, "owner-pass-1")
        view.create_admin()
        self.assertEqual([s.username for s in sessions], ["owner"])
        self.assertFalse(user_service.needs_setup(self.conn))

    def test_setup_rejects_mismatching_passwords(self):
        from pos_erp.ui.login import LoginView
        sessions = []
        view = LoginView(self.root, self.conn, sessions.append)
        view.pass_entry.insert(0, "owner-pass-1")
        view.confirm_entry.insert(0, "different-pass")
        view.create_admin()
        self.assertEqual(sessions, [])
        self.dialogs["showerror"].assert_called_once()
        self.assertTrue(user_service.needs_setup(self.conn))


if __name__ == "__main__":
    unittest.main()

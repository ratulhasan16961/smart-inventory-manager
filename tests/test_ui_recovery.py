"""UI smoke tests for the forgot-password screens (skipped when tkinter or a display is unavailable)."""
import unittest
from unittest import mock

try:
    import tkinter as tk
    import tkinter.messagebox  # noqa: F401
except ImportError:  # pragma: no cover
    tk = None

from pos_erp.errors import AuthenticationError
from pos_erp.services import auth_service, recovery_service, user_service
from tests.helpers import make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiRecoveryTests(unittest.TestCase):
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

    def make_owner(self):
        session = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        return session, recovery_service.issue_key_for_session(self.conn, session)

    def view(self):
        from pos_erp.ui.login import LoginView
        sessions = []
        return LoginView(self.root, self.conn, sessions.append), sessions

    def test_first_run_issues_a_recovery_key_before_logging_in(self):
        view, sessions = self.view()                                  # empty users table -> setup screen
        view.user_entry.delete(0, tk.END)
        view.user_entry.insert(0, "owner")
        view.pass_entry.insert(0, "owner-pass-1")
        view.confirm_entry.insert(0, "owner-pass-1")
        view.create_admin()
        self.assertEqual(sessions, [])
        self.assertTrue(self.conn.execute("SELECT recovery_hash FROM users").fetchone()[0])
        view.continue_after_key()
        self.assertEqual([s.username for s in sessions], ["owner"])

    def test_forgot_password_screen_resets_the_password(self):
        _session, key = self.make_owner()
        view, sessions = self.view()                                  # users exist -> login screen
        view.user_entry.insert(0, "owner")
        view.show_forgot()
        self.assertEqual(view.forgot_user_e.get(), "owner")           # remembered from the login screen
        view.forgot_key_e.insert(0, key)
        view.forgot_new_e.insert(0, "brand-new-pass")
        view.forgot_confirm_e.insert(0, "brand-new-pass")
        view.reset_with_key()
        self.dialogs["showerror"].assert_not_called()
        self.assertEqual(sessions, [])                                # not logged in: the new key is shown first
        view.continue_after_key()                                     # back to the login screen
        self.assertEqual(auth_service.authenticate(self.conn, "owner", "brand-new-pass").username, "owner")

    def test_wrong_key_and_mismatching_passwords_are_refused(self):
        self.make_owner()
        view, _sessions = self.view()
        view.show_forgot()
        view.forgot_user_e.insert(0, "owner")
        view.forgot_key_e.insert(0, "AAAA-AAAA-AAAA-AAAA")
        view.forgot_new_e.insert(0, "brand-new-pass")
        view.forgot_confirm_e.insert(0, "brand-new-pass")
        view.reset_with_key()
        self.assertEqual(self.dialogs["showerror"].call_count, 1)     # wrong key
        view.forgot_confirm_e.delete(0, tk.END)
        view.forgot_confirm_e.insert(0, "different-pass-1")
        view.reset_with_key()
        self.assertEqual(self.dialogs["showerror"].call_count, 2)     # passwords differ
        self.assertEqual(auth_service.authenticate(self.conn, "owner", "owner-pass-1").username, "owner")

    def test_settings_dialog_generates_a_new_key(self):
        from pos_erp.ui.dialogs.recovery_dialogs import open_recovery_key
        from pos_erp.ui.main_window import MainWindow
        session, old_key = self.make_owner()
        dialog = open_recovery_key(MainWindow(self.root, self.conn, session).ctx)
        dialog.generate()                                             # the confirmation dialog is mocked to "yes"
        new_key = dialog.key_entry.get()
        self.assertRegex(new_key, r"^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$")
        self.assertNotEqual(new_key, old_key)
        with self.assertRaises(AuthenticationError):
            recovery_service.reset_password_with_key(self.conn, "owner", old_key, "x-strong-pass-1")
        self.dialogs["showerror"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

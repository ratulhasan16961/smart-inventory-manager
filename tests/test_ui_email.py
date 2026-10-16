"""UI smoke tests for the email reset screens (skipped when tkinter or a display is unavailable)."""
import re
import unittest
from unittest import mock

try:
    import tkinter as tk
    import tkinter.messagebox
except ImportError:
    tk = None

from pos_erp import mailer
from pos_erp.services import auth_service, email_service, user_service
from tests.helpers import make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiEmailTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display available: {exc}")
        self.addCleanup(self.root.destroy)
        self.root.withdraw()
        self.dialogs = {name: mock.patch(f"tkinter.messagebox.{name}", return_value=True).start() for name in DIALOGS}
        self.send = mock.patch("pos_erp.mailer.send_mail").start()
        mock.patch("pos_erp.mailer.is_configured", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = make_conn()
        self.addCleanup(self.conn.close)
        from pos_erp.ui.widgets import apply_theme
        apply_theme(self.root)

    def make_owner(self):
        session = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.conn.execute("UPDATE users SET email = 'owner@gmail.com' WHERE username = 'owner'")
        return session

    def view(self):
        from pos_erp.ui.login import LoginView
        sessions = []
        return LoginView(self.root, self.conn, sessions.append), sessions

    def sent_code(self):
        return re.search(r"\b(\d{6})\b", self.send.call_args[0][2]).group(1)

    def test_email_option_is_disabled_when_email_is_not_set_up(self):
        self.make_owner()
        view, _sessions = self.view()
        with mock.patch("pos_erp.mailer.is_configured", return_value=False):
            view.show_forgot_choice()
        self.assertFalse(view.email_button.enabled)
        view.show_forgot_choice()
        self.assertTrue(view.email_button.enabled)

    def test_full_reset_by_email(self):
        self.make_owner()
        view, sessions = self.view()
        view.show_forgot_choice()
        view.show_forgot_email()
        view.email_e.insert(0, "Owner@Gmail.com")
        view.send_email_code()
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args[0][0], "owner@gmail.com")
        view.email_code_e.insert(0, self.sent_code())
        view.email_new_e.insert(0, "brand-new-pass")
        view.email_confirm_e.insert(0, "brand-new-pass")
        view.reset_with_email_code()
        self.dialogs["showerror"].assert_not_called()
        self.assertEqual(sessions, [])
        self.assertEqual(auth_service.authenticate(self.conn, "owner", "brand-new-pass").username, "owner")

    def test_unregistered_address_sends_nothing_and_shows_no_error(self):
        self.make_owner()
        view, _sessions = self.view()
        view.show_forgot_email()
        view.email_e.insert(0, "stranger@gmail.com")
        view.send_email_code()
        self.send.assert_not_called()
        self.dialogs["showerror"].assert_not_called()
        self.assertIn("If that address is registered", view.email_status.cget("text"))

    def test_send_failure_and_bad_input_are_reported(self):
        self.make_owner()
        view, _sessions = self.view()
        view.show_forgot_email()
        self.send.side_effect = mailer.MailError("no network")
        view.email_e.insert(0, "owner@gmail.com")
        view.send_email_code()
        self.assertEqual(self.dialogs["showerror"].call_count, 1)
        view.email_new_e.insert(0, "brand-new-pass")
        view.email_confirm_e.insert(0, "different-pass-1")
        view.reset_with_email_code()
        self.assertEqual(self.dialogs["showerror"].call_count, 2)
        view.email_confirm_e.delete(0, tk.END)
        view.email_confirm_e.insert(0, "brand-new-pass")
        view.email_code_e.insert(0, "123456")
        view.reset_with_email_code()
        self.assertEqual(self.dialogs["showerror"].call_count, 3)
        auth_service.authenticate(self.conn, "owner", "owner-pass-1")

    def test_account_email_dialog_verifies_an_address(self):
        from pos_erp.ui.dialogs.email_dialogs import open_account_email
        from pos_erp.ui.main_window import MainWindow
        session = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        dialog = open_account_email(MainWindow(self.root, self.conn, session).ctx)
        dialog.email_e.insert(0, "owner@gmail.com")
        dialog.send_code()
        self.send.assert_called_once()
        dialog.code_e.insert(0, self.sent_code())
        dialog.confirm()
        self.assertEqual(email_service.get_email(self.conn, session), "owner@gmail.com")
        self.assertIn("owner@gmail.com", dialog.status.cget("text"))
        self.dialogs["showerror"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

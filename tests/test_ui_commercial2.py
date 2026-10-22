"""UI smoke tests for the register window (skipped when tkinter or a display is unavailable)."""
import unittest
from unittest import mock

try:
    import tkinter as tk
    import tkinter.messagebox
except ImportError:
    tk = None

from pos_erp.services import expense_service, shift_service, user_service
from tests.helpers import make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiRegisterTests(unittest.TestCase):
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
        from pos_erp.ui.main_window import MainWindow
        from pos_erp.ui.widgets import apply_theme
        apply_theme(self.root)
        self.session = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.ctx = MainWindow(self.root, self.conn, self.session).ctx

    def test_register_window_follows_the_shift(self):
        from pos_erp.ui.dialogs.register_dialogs import open_register
        dialog = open_register(self.ctx)
        self.assertIn("No shift is open", dialog.status.cget("text"))
        self.assertFalse(dialog.buttons["close"].enabled)
        shift_service.open_shift(self.conn, self.session, "100")
        expense_service.add_expense(self.conn, self.session, "Rent", "10")
        dialog.refresh()
        self.assertIn("EXPECTED CASH", dialog.report.cget("text"))
        self.assertIn("$90.00", dialog.report.cget("text"))
        self.assertTrue(dialog.buttons["close"].enabled)
        self.assertEqual(len(dialog.tree.get_children()), 1)
        with mock.patch("pos_erp.services.print_output.print_document") as printed:
            dialog.print_summary()
        self.assertIn("SHIFT SUMMARY", printed.call_args[0][0])
        self.dialogs["showerror"].assert_not_called()

    def test_history_and_expense_windows_open_for_administrators(self):
        from pos_erp.ui.dialogs.expense_dialogs import open_expenses
        from pos_erp.ui.dialogs.register_dialogs import open_shift_history
        shift_service.open_shift(self.conn, self.session, "0")
        shift_service.close_shift(self.conn, self.session, "0")
        history = open_shift_history(self.ctx)
        self.assertEqual(len(history.tree.get_children()), 1)
        expense_service.add_expense(self.conn, self.session, "Rent", "10")
        window = open_expenses(self.ctx)
        self.assertEqual(len(window.tree.get_children()), 1)
        self.assertIn("Rent", window.totals.cget("text"))
        self.dialogs["showerror"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

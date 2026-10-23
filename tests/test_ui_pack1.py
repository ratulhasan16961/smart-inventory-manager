"""UI smoke tests for backup/reminder settings, WhatsApp and statement buttons, net profit and the auto summary."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import tkinter as tk
    import tkinter.filedialog  # noqa: F401
    import tkinter.messagebox  # noqa: F401
except ImportError:  # pragma: no cover
    tk = None

from pos_erp.services import business_service, credit_service, user_service
from tests.helpers import make_conn

DIALOGS = ("showinfo", "showwarning", "showerror", "askyesno")


@unittest.skipIf(tk is None, "tkinter is not installed")
class UiPack1Tests(unittest.TestCase):
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
        self.window = MainWindow(self.root, self.conn, self.session)
        self.ctx = self.window.ctx

    def test_tools_dialog_saves_tests_the_folder_and_sends_the_summary(self):
        from pos_erp.ui.dialogs.tools_dialogs import open_tools
        dialog = open_tools(self.ctx)
        with tempfile.TemporaryDirectory() as folder:
            dialog.entries["backup_mirror_dir"].delete(0, tk.END)
            dialog.entries["backup_mirror_dir"].insert(0, folder)
            dialog.entries["country_code"].insert(0, "880")
            dialog.entries["digest_email"].insert(0, "owner@gmail.com")
            dialog.auto_combo.set("Yes")
            dialog.save()
            profile = business_service.get_profile(self.conn)
            self.assertEqual((profile["backup_mirror_dir"], profile["country_code"], profile["digest_auto"]),
                             (folder, "880", "Yes"))
            dialog.test_folder()
            dialog.backup_now()
            self.assertEqual(len(list(Path(folder).glob("backup_*.db"))), 1)
            self.assertTrue(dialog.status.cget("text").startswith("OK"))
        with mock.patch("pos_erp.mailer.send_mail") as send:
            dialog.send_summary()
        self.assertEqual(send.call_args[0][0], "owner@gmail.com")
        self.dialogs["showerror"].assert_not_called()

    def test_ledger_dialog_whatsapp_and_statement(self):
        from pos_erp.ui.dialogs.ledger_dialogs import open_customer_ledger
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Sam', '01712345678')")
        credit_service.post(self.conn, credit_service.customer_id_for(self.conn, "01712345678"), "CHARGE", 4000, "owner")
        business_service.update_profile(self.conn, self.session, {"country_code": "880"})
        dialog = open_customer_ledger(self.ctx, "01712345678")
        with mock.patch("webbrowser.open") as opened:
            dialog.whatsapp()
        self.assertTrue(opened.call_args[0][0].startswith("https://wa.me/8801712345678?text="))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "statement.pdf"
            with mock.patch("tkinter.filedialog.asksaveasfilename", return_value=str(target)):
                dialog.statement_pdf()
            self.assertTrue(target.read_bytes().startswith(b"%PDF"))
        self.dialogs["showerror"].assert_not_called()

    def test_whatsapp_without_a_debt_is_a_message_not_a_crash(self):
        from pos_erp.ui.dialogs.ledger_dialogs import open_customer_ledger
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Ana', '0180')")
        dialog = open_customer_ledger(self.ctx, "0180")
        with mock.patch("webbrowser.open") as opened:
            dialog.whatsapp()
        opened.assert_not_called()
        self.dialogs["showerror"].assert_called_once()

    def test_net_profit_window_and_the_automatic_summary_after_closing_a_shift(self):
        from pos_erp.ui.dialogs.profit_dialogs import open_profit
        profit = open_profit(self.ctx)
        self.assertIn("NET PROFIT", profit.summary.cget("text"))
        register = self.window.register
        self.assertTrue(register.buttons["profit"].enabled)
        register._auto_digest()
        self.dialogs["showinfo"].assert_not_called()
        business_service.update_profile(self.conn, self.session, {"digest_email": "owner@gmail.com",
                                                                  "digest_auto": "Yes"})
        with mock.patch("pos_erp.mailer.send_mail") as send:
            register._auto_digest()
        send.assert_called_once()
        self.dialogs["showinfo"].assert_called_once()
        self.dialogs["showerror"].assert_not_called()


if __name__ == "__main__":
    unittest.main()

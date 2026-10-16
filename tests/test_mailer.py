import contextlib
import io
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pos_erp import mailer


class LoadSettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "mail.env"

    def test_not_configured_without_a_file_or_environment(self):
        self.assertIsNone(mailer.load_settings(env={}, path=self.path))

    def test_file_values_defaults_and_app_password_spaces(self):
        self.path.write_text("# my shop\nPOS_SMTP_USER=shop@gmail.com\nPOS_SMTP_PASSWORD='abcd efgh ijkl mnop'\n"
                             "POS_SMTP_FROM_NAME=Demo Mart\n\nnot a setting\n")
        s = mailer.load_settings(env={}, path=self.path)
        self.assertEqual((s.user, s.password, s.host, s.port, s.sender_name, s.console),
                         ("shop@gmail.com", "abcdefghijklmnop", "smtp.gmail.com", 587, "Demo Mart", False))

    def test_environment_overrides_the_file(self):
        self.path.write_text("POS_SMTP_USER=file@gmail.com\nPOS_SMTP_PASSWORD=filepass\n")
        s = mailer.load_settings(env={"POS_SMTP_USER": "env@gmail.com", "POS_SMTP_PORT": "465",
                                      "POS_SMTP_HOST": "smtp.example.com", "OTHER": "ignored"}, path=self.path)
        self.assertEqual((s.user, s.password, s.host, s.port), ("env@gmail.com", "filepass", "smtp.example.com", 465))

    def test_incomplete_settings_and_bad_port(self):
        self.assertIsNone(mailer.load_settings(env={"POS_SMTP_USER": "a@gmail.com"}, path=self.path))
        s = mailer.load_settings(env={"POS_SMTP_USER": "a@gmail.com", "POS_SMTP_PASSWORD": "x",
                                      "POS_SMTP_PORT": "abc"}, path=self.path)
        self.assertEqual(s.port, 587)

    def test_console_mode_needs_no_credentials(self):
        s = mailer.load_settings(env={"POS_MAIL_MODE": "Console"}, path=self.path)
        self.assertTrue(s.console)


class SendMailTests(unittest.TestCase):
    settings = mailer.MailSettings("shop@gmail.com", "secret", sender_name="Demo Mart")

    def test_starttls_login_and_message(self):
        with mock.patch("pos_erp.mailer.smtplib.SMTP") as smtp_class:
            mailer.send_mail("sam@gmail.com", "Hello", "Body text", settings=self.settings)
        smtp = smtp_class.return_value.__enter__.return_value
        smtp_class.assert_called_once_with("smtp.gmail.com", 587, timeout=mailer.TIMEOUT_SECONDS)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("shop@gmail.com", "secret")
        message = smtp.send_message.call_args[0][0]
        self.assertEqual((message["To"], message["Subject"]), ("sam@gmail.com", "Hello"))
        self.assertIn("shop@gmail.com", message["From"])
        self.assertIn("Body text", message.get_content())

    def test_port_465_uses_implicit_ssl(self):
        settings = mailer.MailSettings("shop@gmail.com", "secret", port=465)
        with mock.patch("pos_erp.mailer.smtplib.SMTP_SSL") as ssl_class:
            mailer.send_mail("sam@gmail.com", "Hi", "Body", settings=settings)
        ssl_class.return_value.__enter__.return_value.send_message.assert_called_once()

    def test_login_refused_gives_a_gmail_hint(self):
        with mock.patch("pos_erp.mailer.smtplib.SMTP") as smtp_class:
            smtp_class.return_value.__enter__.return_value.login.side_effect = (
                smtplib.SMTPAuthenticationError(535, b"bad credentials"))
            with self.assertRaises(mailer.MailError) as ctx:
                mailer.send_mail("sam@gmail.com", "Hi", "Body", settings=self.settings)
        self.assertIn("App Password", str(ctx.exception))

    def test_network_problems_become_mail_errors(self):
        with mock.patch("pos_erp.mailer.smtplib.SMTP", side_effect=OSError("no route to host")):
            with self.assertRaises(mailer.MailError) as ctx:
                mailer.send_mail("sam@gmail.com", "Hi", "Body", settings=self.settings)
        self.assertIn("no route to host", str(ctx.exception))

    def test_not_configured(self):
        with mock.patch("pos_erp.mailer.load_settings", return_value=None):
            with self.assertRaises(mailer.MailError):
                mailer.send_mail("sam@gmail.com", "Hi", "Body")

    def test_console_mode_prints_instead_of_sending(self):
        console = mailer.MailSettings("console", "", host="console", port=0, console=True)
        out = io.StringIO()
        with mock.patch("pos_erp.mailer.smtplib.SMTP") as smtp_class, contextlib.redirect_stdout(out):
            mailer.send_mail("sam@gmail.com", "Your code", "Code is 123456", settings=console)
        smtp_class.assert_not_called()
        self.assertIn("Code is 123456", out.getvalue())
        self.assertIn("sam@gmail.com", out.getvalue())


if __name__ == "__main__":
    unittest.main()

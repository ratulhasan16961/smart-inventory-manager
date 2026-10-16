import re
import unittest
from datetime import timedelta

from pos_erp import mailer, timeutil
from pos_erp.errors import AuthenticationError, ConflictError, PermissionDenied, ValidationError
from pos_erp.permissions import Session
from pos_erp.services import auth_service, user_service
from pos_erp.services import email_service as mail
from tests.helpers import DbTestCase


class Outbox:
    def __init__(self):
        self.messages = []

    def __call__(self, to, subject, body):
        self.messages.append((to, subject, body))

    def code(self):
        return re.search(r"\b(\d{6})\b", self.messages[-1][2]).group(1)


class BrokenMail:
    def __call__(self, to, subject, body):
        raise mailer.MailError("no network")


def wrong(code):
    return "000000" if code != "000000" else "111111"


class ValidateEmailTests(unittest.TestCase):
    def test_validation_and_normalisation(self):
        self.assertEqual(mail.validate_email("  Name+Tag@Gmail.COM "), "name+tag@gmail.com")
        for bad in ("", "abc", "a@b", "a b@c.com", "@x.com", "a@@x.com", "x" * 260 + "@gmail.com"):
            with self.assertRaises(ValidationError, msg=bad):
                mail.validate_email(bad)


class VerificationTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.outbox = Outbox()
        self.t0 = timeutil.now()

    def request(self, email="Owner@Gmail.com", **kw):
        kw.setdefault("now", self.t0)
        mail.request_verification(self.conn, self.admin, email, sender=self.outbox, **kw)

    def test_verification_sets_the_email(self):
        self.assertIsNone(mail.get_email(self.conn, self.admin))
        self.request()
        self.assertEqual(self.outbox.messages[0][0], "owner@gmail.com")
        self.assertIsNone(mail.get_email(self.conn, self.admin))
        address = mail.confirm_verification(self.conn, self.admin, self.outbox.code(),
                                            now=self.t0 + timedelta(minutes=1))
        self.assertEqual(address, "owner@gmail.com")
        self.assertEqual(mail.get_email(self.conn, self.admin), "owner@gmail.com")
        self.assertNotIn(self.outbox.code(), str(self.conn.execute("SELECT code_hash FROM email_codes").fetchall()))

    def test_wrong_codes_use_up_the_code(self):
        self.request()
        code = self.outbox.code()
        for _ in range(mail.MAX_CODE_ATTEMPTS):
            with self.assertRaises(ValidationError):
                mail.confirm_verification(self.conn, self.admin, wrong(code), now=self.t0)
        with self.assertRaises(ValidationError):
            mail.confirm_verification(self.conn, self.admin, code, now=self.t0)
        self.assertIsNone(mail.get_email(self.conn, self.admin))

    def test_expired_code(self):
        self.request()
        with self.assertRaises(ValidationError):
            mail.confirm_verification(self.conn, self.admin, self.outbox.code(),
                                      now=self.t0 + timedelta(minutes=mail.CODE_TTL_MINUTES + 1))

    def test_requests_are_throttled(self):
        self.request()
        with self.assertRaises(ValidationError):
            self.request(now=self.t0 + timedelta(seconds=30))
        for step in range(1, mail.MAX_SENDS_PER_HOUR):
            self.request(now=self.t0 + timedelta(seconds=61 * step))
        self.assertEqual(len(self.outbox.messages), mail.MAX_SENDS_PER_HOUR)
        with self.assertRaises(ValidationError):
            self.request(now=self.t0 + timedelta(seconds=61 * mail.MAX_SENDS_PER_HOUR))

    def test_address_used_by_another_account_is_refused(self):
        other = user_service.create_user(self.conn, self.admin, "sara", "Cashier", "temporary-1")
        self.conn.execute("UPDATE users SET email = 'sara@gmail.com' WHERE id = ?", (other,))
        with self.assertRaises(ConflictError):
            self.request(email="SARA@gmail.com")
        self.assertEqual(self.outbox.messages, [])

    def test_a_failed_send_leaves_no_usable_code(self):
        with self.assertRaises(mailer.MailError):
            mail.request_verification(self.conn, self.admin, "owner@gmail.com", sender=BrokenMail(), now=self.t0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM email_codes WHERE used = 0"), 0)

    def test_remove_and_permissions(self):
        self.request()
        mail.confirm_verification(self.conn, self.admin, self.outbox.code(), now=self.t0)
        mail.remove_email(self.conn, self.admin)
        self.assertIsNone(mail.get_email(self.conn, self.admin))
        nobody = Session(9, "x", "Nobody")
        for call in (lambda: mail.get_email(self.conn, nobody), lambda: mail.remove_email(self.conn, nobody),
                     lambda: mail.request_verification(self.conn, nobody, "a@b.com", sender=self.outbox)):
            with self.assertRaises(PermissionDenied):
                call()


class ResetByEmailTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.conn.execute("UPDATE users SET email = 'owner@gmail.com' WHERE username = 'owner'")
        self.outbox = Outbox()
        self.t0 = timeutil.now()

    def request(self, email="owner@gmail.com", **kw):
        kw.setdefault("now", self.t0)
        mail.request_reset_code(self.conn, email, sender=self.outbox, **kw)

    def reset(self, code=None, password="brand-new-pass", email="owner@gmail.com", **kw):
        kw.setdefault("now", self.t0 + timedelta(minutes=1))
        mail.reset_password_with_code(self.conn, email, self.outbox.code() if code is None else code, password, **kw)

    def test_full_flow(self):
        self.conn.execute("UPDATE users SET locked_until = '2999-01-01 00:00:00', failed_attempts = 3")
        self.request(email="  OWNER@Gmail.com ")
        to, subject, body = self.outbox.messages[0]
        self.assertEqual(to, "owner@gmail.com")
        self.assertIn("owner", body)
        self.reset(email="Owner@gmail.com")
        self.assertEqual(auth_service.authenticate(self.conn, "owner", "brand-new-pass").username, "owner")
        with self.assertRaises(AuthenticationError):
            auth_service.authenticate(self.conn, "owner", "owner-pass-1")
        self.assertTrue(self.scalar("SELECT COUNT(*) FROM audit_logs WHERE action = 'Password reset with an emailed code'"))

    def test_code_works_only_once(self):
        self.request()
        code = self.outbox.code()
        self.reset(code=code)
        with self.assertRaises(AuthenticationError) as ctx:
            self.reset(code=code, password="another-new-pass")
        self.assertEqual(str(ctx.exception), mail.INVALID_CODE)

    def test_wrong_codes_kill_the_code(self):
        self.request()
        code = self.outbox.code()
        for _ in range(mail.MAX_CODE_ATTEMPTS):
            with self.assertRaises(AuthenticationError):
                self.reset(code=wrong(code))
        with self.assertRaises(AuthenticationError):
            self.reset(code=code)
        auth_service.authenticate(self.conn, "owner", "owner-pass-1")

    def test_expired_code(self):
        self.request()
        with self.assertRaises(AuthenticationError):
            self.reset(now=self.t0 + timedelta(minutes=mail.CODE_TTL_MINUTES + 1))

    def test_a_weak_password_does_not_use_up_the_code(self):
        self.request()
        with self.assertRaises(ValidationError):
            self.reset(password="short")
        self.reset()

    def test_requests_are_throttled_and_the_newest_code_wins(self):
        self.request()
        self.request(now=self.t0 + timedelta(seconds=30))
        self.assertEqual(len(self.outbox.messages), 1)
        first = self.outbox.code()
        self.request(now=self.t0 + timedelta(seconds=61))
        self.assertEqual(len(self.outbox.messages), 2)
        self.assertNotEqual(first, self.outbox.code())
        with self.assertRaises(AuthenticationError):
            self.reset(code=first, now=self.t0 + timedelta(seconds=90))
        self.reset(now=self.t0 + timedelta(seconds=90))
        for step in range(5):
            self.request(now=self.t0 + timedelta(minutes=10 + step * 2))
        self.assertLessEqual(len(self.outbox.messages), 2 + mail.MAX_SENDS_PER_HOUR)

    def test_unknown_and_disabled_addresses_get_no_email_and_no_hint(self):
        self.request(email="stranger@gmail.com")
        self.assertEqual(self.outbox.messages, [])
        sara = user_service.create_user(self.conn, self.admin, "sara", "Cashier", "temporary-1")
        self.conn.execute("UPDATE users SET email = 'sara@gmail.com' WHERE id = ?", (sara,))
        user_service.set_active(self.conn, self.admin, sara, False)
        self.request(email="sara@gmail.com")
        self.assertEqual(self.outbox.messages, [])
        with self.assertRaises(AuthenticationError) as ctx:
            self.reset(code="123456", email="stranger@gmail.com")
        self.assertEqual(str(ctx.exception), mail.INVALID_CODE)

    def test_a_failed_send_is_reported_and_leaves_no_usable_code(self):
        with self.assertRaises(mailer.MailError):
            mail.request_reset_code(self.conn, "owner@gmail.com", sender=BrokenMail(), now=self.t0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM email_codes WHERE used = 0"), 0)

    def test_invalid_address_is_rejected_up_front(self):
        with self.assertRaises(ValidationError):
            self.request(email="not-an-email")


if __name__ == "__main__":
    unittest.main()

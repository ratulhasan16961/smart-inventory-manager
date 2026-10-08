import unittest
from datetime import timedelta

from pos_erp import timeutil
from pos_erp.errors import AuthenticationError, ConflictError, ValidationError
from pos_erp.permissions import Session
from pos_erp.services import auth_service as auth
from tests.helpers import DbTestCase


class AuthTests(DbTestCase):
    def setUp(self):
        super().setUp()
        auth.ensure_default_users(self.conn)

    def test_default_users_seeded_once_and_flagged(self):
        auth.ensure_default_users(self.conn)  # idempotent: must NOT reset anything
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM users"), 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM users WHERE must_change_password = 1"), 2)

    def test_login_forces_password_change_and_password_survives_restart(self):
        session = auth.authenticate(self.conn, "admin", "1234")
        self.assertTrue(session.must_change_password)
        session = auth.change_password(self.conn, session, "correct-horse")
        self.assertFalse(session.must_change_password)
        auth.ensure_default_users(self.conn)               # "app restart"
        self.assertFalse(auth.authenticate(self.conn, "admin", "correct-horse").must_change_password)
        with self.assertRaises(AuthenticationError):
            auth.authenticate(self.conn, "admin", "1234")  # old default no longer works

    def test_generic_error_for_unknown_user_and_bad_password(self):
        for user, pw in (("nobody", "x"), ("admin", "wrong")):
            with self.assertRaises(AuthenticationError) as ctx:
                auth.authenticate(self.conn, user, pw)
            self.assertEqual(str(ctx.exception), "Invalid username or password.")
        self.assertGreaterEqual(self.scalar("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'Failed login%'"), 2)

    def test_lockout_after_repeated_failures_and_expiry(self):
        start = timeutil.now()
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                auth.authenticate(self.conn, "admin", "bad", now=start)
        with self.assertRaises(AuthenticationError) as ctx:  # even the right password is refused while locked
            auth.authenticate(self.conn, "admin", "1234", now=start + timedelta(minutes=1))
        self.assertIn("locked", str(ctx.exception).lower())
        later = start + timedelta(minutes=6)
        self.assertEqual(auth.authenticate(self.conn, "admin", "1234", now=later).username, "admin")

    def test_disabled_user_cannot_login(self):
        self.conn.execute("UPDATE users SET is_active = 0 WHERE username = 'cashier'")
        with self.assertRaises(AuthenticationError):
            auth.authenticate(self.conn, "cashier", "1234")

    def test_password_policy(self):
        session = Session(1, "admin", "Admin")
        with self.assertRaises(ValidationError):
            auth.change_password(self.conn, session, "short")
        with self.assertRaises(ValidationError):
            auth.change_password(self.conn, session, "ADMIN")

    def test_update_credentials(self):
        session = auth.authenticate(self.conn, "admin", "1234")
        new = auth.update_credentials(self.conn, session, "boss", "a-strong-pass")
        self.assertEqual(new.username, "boss")
        self.assertEqual(auth.authenticate(self.conn, "boss", "a-strong-pass").role, "Admin")
        with self.assertRaises(ConflictError):
            auth.update_credentials(self.conn, new, "cashier", "another-strong")
        with self.assertRaises(ValidationError):
            auth.update_credentials(self.conn, new, "  ", "another-strong")


if __name__ == "__main__":
    unittest.main()

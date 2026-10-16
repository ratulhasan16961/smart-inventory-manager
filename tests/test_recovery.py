import re
import unittest
from datetime import timedelta

from pos_erp import timeutil
from pos_erp.errors import AuthenticationError, NotFoundError, PermissionDenied, ValidationError
from pos_erp.permissions import Session
from pos_erp.services import auth_service, user_service
from pos_erp.services import recovery_service as rec
from tests.helpers import DbTestCase

KEY_PATTERN = re.compile(r"^[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){3}$")


class RecoveryTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.key = rec.issue_key_for_session(self.conn, self.admin)

    def reset(self, key=None, password="brand-new-pass", username="owner", **kw):
        return rec.reset_password_with_key(self.conn, username, self.key if key is None else key, password, **kw)

    def test_key_format_and_storage(self):
        self.assertRegex(self.key, KEY_PATTERN)
        self.assertEqual(len({rec.generate_key() for _ in range(50)}), 50)
        stored = self.scalar("SELECT recovery_hash FROM users WHERE username = 'owner'")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertNotIn(self.key.replace("-", ""), stored)
        self.assertEqual(rec.normalize_key(" abcd-efgh ijkl_mnpq "), "ABCDEFGHIJKLMNPQ")

    def test_reset_sets_the_password_and_rotates_the_key(self):
        new_key = self.reset(key=self.key.lower(), username="OWNER")
        self.assertRegex(new_key, KEY_PATTERN)
        self.assertNotEqual(new_key, self.key)
        session = auth_service.authenticate(self.conn, "owner", "brand-new-pass")
        self.assertFalse(session.must_change_password)
        with self.assertRaises(AuthenticationError):
            auth_service.authenticate(self.conn, "owner", "owner-pass-1")
        with self.assertRaises(AuthenticationError):
            self.reset()
        self.reset(key=new_key, password="another-new-pass")
        self.assertGreaterEqual(
            self.scalar("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'Password reset with a recovery key%'"), 2)

    def test_reset_clears_a_login_lockout(self):
        self.conn.execute("UPDATE users SET locked_until = '2999-01-01 00:00:00', failed_attempts = 3 "
                          "WHERE username = 'owner'")
        with self.assertRaises(AuthenticationError):
            auth_service.authenticate(self.conn, "owner", "owner-pass-1")
        self.reset()
        self.assertEqual(auth_service.authenticate(self.conn, "owner", "brand-new-pass").username, "owner")

    def test_a_weak_new_password_does_not_use_up_the_key(self):
        with self.assertRaises(ValidationError):
            self.reset(password="short")
        self.assertEqual(self.scalar("SELECT recovery_attempts FROM users WHERE username = 'owner'"), 0)
        self.reset()

    def test_wrong_keys_lock_recovery_for_a_while(self):
        now = timeutil.now()
        for _ in range(4):
            with self.assertRaises(AuthenticationError) as ctx:
                self.reset(key="AAAA-AAAA-AAAA-AAAA", now=now)
            self.assertEqual(str(ctx.exception), rec.INVALID)
        with self.assertRaises(AuthenticationError) as ctx:
            self.reset(key="AAAA-AAAA-AAAA-AAAA", now=now)
        self.assertIn("locked", str(ctx.exception).lower())
        with self.assertRaises(AuthenticationError) as ctx:
            self.reset(now=now + timedelta(minutes=1))
        self.assertIn("Try again", str(ctx.exception))
        self.reset(now=now + timedelta(minutes=rec.KEY_LOCKOUT_MINUTES + 1))

    def test_unknown_disabled_and_keyless_users_get_the_same_message(self):
        user_service.create_user(self.conn, self.admin, "sara", "Cashier", "temporary-1")
        tom_id = user_service.create_user(self.conn, self.admin, "tom", "Cashier", "temporary-2")
        tom_key = rec.issue_key_for_session(self.conn, Session(tom_id, "tom", "Cashier"))
        user_service.set_active(self.conn, self.admin, tom_id, False)
        messages = set()
        for username, key in (("nobody", self.key), ("sara", self.key), ("tom", tom_key)):
            with self.assertRaises(AuthenticationError) as ctx:
                self.reset(key=key, username=username)
            messages.add(str(ctx.exception))
        self.assertEqual(messages, {rec.INVALID})

    def test_a_new_key_replaces_the_old_one(self):
        newer = rec.issue_key_for_session(self.conn, self.admin)
        self.assertNotEqual(newer, self.key)
        with self.assertRaises(AuthenticationError):
            self.reset()
        self.reset(key=newer)

    def test_has_key_and_permission(self):
        self.assertTrue(rec.has_key(self.conn, self.admin))
        cashier_id = user_service.create_user(self.conn, self.admin, "sara", "Cashier", "temporary-1")
        sara = Session(cashier_id, "sara", "Cashier")
        self.assertFalse(rec.has_key(self.conn, sara))
        rec.issue_key_for_session(self.conn, sara)
        self.assertTrue(rec.has_key(self.conn, sara))
        with self.assertRaises(PermissionDenied):
            rec.issue_key_for_session(self.conn, Session(9, "x", "Nobody"))

    def test_emergency_reset(self):
        self.conn.execute("UPDATE users SET locked_until = '2999-01-01 00:00:00', failed_attempts = 4")
        new_key = rec.emergency_reset(self.conn, "Owner", "emergency-pass-9")
        self.assertRegex(new_key, KEY_PATTERN)
        self.assertEqual(auth_service.authenticate(self.conn, "owner", "emergency-pass-9").role, "Admin")
        with self.assertRaises(AuthenticationError):
            self.reset()
        self.reset(key=new_key)
        self.assertTrue(self.scalar("SELECT COUNT(*) FROM audit_logs WHERE action LIKE '%emergency reset%'"))

    def test_emergency_reset_validation(self):
        with self.assertRaises(ValidationError):
            rec.emergency_reset(self.conn, "owner", "short")
        with self.assertRaises(NotFoundError):
            rec.emergency_reset(self.conn, "ghost", "emergency-pass-9")
        cashier_id = user_service.create_user(self.conn, self.admin, "sara", "Cashier", "temporary-1")
        user_service.set_active(self.conn, self.admin, cashier_id, False)
        with self.assertRaises(ValidationError):
            rec.emergency_reset(self.conn, "sara", "emergency-pass-9")


if __name__ == "__main__":
    unittest.main()

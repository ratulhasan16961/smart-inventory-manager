import unittest
from decimal import Decimal

from pos_erp import money, security
from pos_erp.permissions import Session


class MoneyTests(unittest.TestCase):
    def test_to_cents_half_up(self):
        self.assertEqual(money.to_cents("12.5"), 1250)
        self.assertEqual(money.to_cents(0.285), 29)      # float artefacts do not matter
        self.assertEqual(money.to_cents("2.675"), 268)
        self.assertEqual(money.to_cents(Decimal("5")), 500)

    def test_to_cents_rejects_garbage(self):
        for bad in ("abc", "", "nan", "inf", True):
            with self.assertRaises(ValueError):
                money.to_cents(bad)

    def test_parse_rate_bounds(self):
        self.assertEqual(money.parse_rate("7.5"), Decimal("7.5"))
        for bad in ("-1", "101", "x"):
            with self.assertRaises(ValueError):
                money.parse_rate(bad)

    def test_allocate_sums_exactly(self):
        for total, weights in ((100, [1, 1, 1]), (7, [3, 3, 3]), (1000, [999, 1]), (5, [0, 10])):
            parts = money.allocate(total, weights)
            self.assertEqual(sum(parts), total)
        self.assertEqual(money.allocate(0, [5, 5]), [0, 0])

    def test_formatting(self):
        self.assertEqual(money.fmt(123456), "$1,234.56")
        self.assertEqual(money.fmt(-250), "-$2.50")
        self.assertEqual(money.plain(5), "0.05")


class SecurityTests(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self):
        a, b = security.hash_password("secret-pass"), security.hash_password("secret-pass")
        self.assertNotEqual(a, b)
        self.assertTrue(security.verify_password("secret-pass", a))
        self.assertFalse(security.verify_password("wrong", a))

    def test_legacy_sha256_verifies_but_needs_rehash(self):
        stored = security.legacy_sha256("1234")
        self.assertTrue(security.verify_password("1234", stored))
        self.assertTrue(security.needs_rehash(stored))
        self.assertFalse(security.needs_rehash(security.hash_password("x")))

    def test_malformed_hash_is_rejected_not_crashing(self):
        for stored in ("", "scrypt$bad", "plaintext", "scrypt$1$2$3$!!$!!"):
            self.assertFalse(security.verify_password("x", stored))


class PermissionTests(unittest.TestCase):
    def test_roles(self):
        admin, cashier = Session(1, "a", "Admin"), Session(2, "c", "Cashier")
        self.assertTrue(admin.can("returns.process"))
        self.assertTrue(cashier.can("sales.create"))
        for perm in ("returns.process", "inventory.manage", "inventory.export", "purchasing.manage", "audit.view"):
            self.assertFalse(cashier.can(perm), perm)
        self.assertFalse(Session(3, "x", "Unknown").can("sales.create"))


if __name__ == "__main__":
    unittest.main()

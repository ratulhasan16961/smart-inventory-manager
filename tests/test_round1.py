import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pos_erp import config, money
from pos_erp.db import connect, ensure_schema
from pos_erp.db.upgrades import LATEST_VERSION, applied_version, apply_upgrades
from pos_erp.errors import (AuthenticationError, ConflictError, PermissionDenied, ValidationError)
from pos_erp.permissions import Session
from pos_erp.services import auth_service, backup_service, lists_service, settings_service, user_service
from pos_erp.services import inventory_service as inv
from pos_erp.services.inventory_service import ProductForm
from tests.helpers import ADMIN, CASHIER, DbTestCase, add_product
from tests.test_migrations import build_legacy_db


class UpgradeTests(unittest.TestCase):
    def test_framework_applies_once_and_records_versions(self):
        conn = connect(":memory:")
        ensure_schema(conn)
        self.assertEqual(applied_version(conn), 1)
        self.assertEqual(apply_upgrades(conn), [2])
        self.assertEqual(applied_version(conn), LATEST_VERSION)
        self.assertEqual(apply_upgrades(conn), [])
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"settings", "categories", "warehouses", "schema_migrations"} <= tables)

    def test_upgrade_keeps_existing_values_and_normalises_expiry(self):
        conn = connect(":memory:")
        ensure_schema(conn)
        conn.execute("INSERT INTO products (name, category, warehouse, expiry) VALUES ('Tea', 'Snacks', 'Annex', '2027-5')")
        conn.execute("INSERT INTO products (name, category, expiry) VALUES ('Mouse', 'Electronics', '2028-12')")
        apply_upgrades(conn)
        expiry = {r["name"]: r["expiry"] for r in conn.execute("SELECT name, expiry FROM products")}
        self.assertEqual(expiry, {"Tea": "2027-05", "Mouse": "2028-12"})
        categories = [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY id")]
        self.assertEqual(categories[:4], ["Electronics", "Grocery", "Clothing", "General"])  # defaults first
        self.assertIn("Snacks", categories)
        self.assertIn("Annex", [r[0] for r in conn.execute("SELECT name FROM warehouses")])

    def test_database_newer_than_the_app_is_refused(self):
        conn = connect(":memory:")
        ensure_schema(conn)
        apply_upgrades(conn)
        conn.execute("INSERT INTO schema_migrations VALUES (99, 'x')")
        with self.assertRaises(RuntimeError):
            apply_upgrades(conn)

    def test_file_backup_is_taken_before_upgrading(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = connect(os.path.join(folder, "t.db"))
            ensure_schema(conn)
            apply_upgrades(conn)
            conn.close()
            self.assertTrue([f for f in os.listdir(folder) if ".pre-upgrade-" in f])


class SettingsTests(DbTestCase):
    def test_defaults_then_update_applies_currency(self):
        self.assertEqual(settings_service.get_all(self.conn)["shop_name"], "Enterprise POS")
        clean = settings_service.update(self.conn, ADMIN, {"shop_name": " Tea House ", "currency_symbol": "৳",
                                                           "default_tax_rate": "7.50", "receipt_footer": "Come again!"})
        self.assertEqual(clean["default_tax_rate"], "7.5")
        self.assertEqual(settings_service.get(self.conn, "shop_name"), "Tea House")
        self.assertEqual(config.CURRENCY_SYMBOL, "৳")
        self.assertEqual(money.fmt(12345), "৳123.45")

    def test_partial_update_keeps_other_values(self):
        settings_service.update(self.conn, ADMIN, {"receipt_footer": "Bye"})
        self.assertEqual(settings_service.get(self.conn, "shop_name"), "Enterprise POS")
        self.assertEqual(settings_service.get(self.conn, "receipt_footer"), "Bye")

    def test_validation_and_permissions(self):
        for bad in ({"shop_name": " "}, {"shop_name": "x" * 31}, {"currency_symbol": ""}, {"currency_symbol": "12345"},
                    {"default_tax_rate": "101"}, {"default_tax_rate": "abc"}, {"receipt_footer": "x" * 42}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                settings_service.update(self.conn, ADMIN, bad)
        with self.assertRaises(PermissionDenied):
            settings_service.update(self.conn, CASHIER, {"shop_name": "Hacked"})


class ListsTests(DbTestCase):
    def test_defaults_available(self):
        self.assertEqual(lists_service.names(self.conn, "categories")[:4],
                         ["Electronics", "Grocery", "Clothing", "General"])
        self.assertEqual(lists_service.names(self.conn, "warehouses"), ["Central Warehouse", "Branch Warehouse"])

    def test_add_rules(self):
        lists_service.add(self.conn, ADMIN, "categories", "Toys")
        self.assertIn("Toys", config.CATEGORIES)            # published to the product form immediately
        with self.assertRaises(ConflictError):
            lists_service.add(self.conn, ADMIN, "categories", "toys")
        with self.assertRaises(ValidationError):
            lists_service.add(self.conn, ADMIN, "categories", "  ")
        with self.assertRaises(ValueError):
            lists_service.names(self.conn, "colours")
        with self.assertRaises(PermissionDenied):
            lists_service.add(self.conn, CASHIER, "categories", "Nope")

    def test_rename_updates_products_too(self):
        add_product(self.conn, "Apple", category="Grocery")
        self.assertEqual(lists_service.rename(self.conn, ADMIN, "categories", "Grocery", "Food"), 1)
        self.assertEqual(self.scalar("SELECT category FROM products WHERE name = 'Apple'"), "Food")
        names = lists_service.names(self.conn, "categories")
        self.assertIn("Food", names)
        self.assertNotIn("Grocery", names)
        with self.assertRaises(ConflictError):
            lists_service.rename(self.conn, ADMIN, "categories", "Food", "Clothing")
        with self.assertRaises(ValidationError):
            lists_service.rename(self.conn, ADMIN, "categories", "General", "Misc")

    def test_remove_rules(self):
        add_product(self.conn, "Apple", category="Grocery")
        with self.assertRaises(ValidationError):
            lists_service.remove(self.conn, ADMIN, "categories", "Grocery")       # still in use
        lists_service.remove(self.conn, ADMIN, "categories", "Clothing")
        self.assertNotIn("Clothing", lists_service.names(self.conn, "categories"))
        with self.assertRaises(ValidationError):
            lists_service.remove(self.conn, ADMIN, "categories", "General")
        lists_service.remove(self.conn, ADMIN, "warehouses", "Branch Warehouse")
        with self.assertRaises(ValidationError):
            lists_service.remove(self.conn, ADMIN, "warehouses", "Central Warehouse")  # the last one must stay

    def test_apply_registers_values_that_products_already_use(self):
        add_product(self.conn, "Lego", category="Toys", warehouse="Annex")
        lists_service.apply(self.conn)
        self.assertIn("Toys", config.CATEGORIES)
        self.assertIn("Annex", config.WAREHOUSES)


class ExpiryNormaliseTests(DbTestCase):
    def test_new_products_store_two_digit_months(self):
        pid = add_product(self.conn, "Tea", expiry="2027-5")
        self.assertEqual(inv.get_product(self.conn, ADMIN, pid)["expiry"], "2027-05")
        inv.update_product(self.conn, ADMIN, pid, ProductForm(name="Tea", price="10", buying_price="6", stock="10",
                                                               min_alert="2", expiry="2028-1"))
        self.assertEqual(inv.get_product(self.conn, ADMIN, pid)["expiry"], "2028-01")


class UserTests(DbTestCase):
    def first_admin(self):
        return user_service.create_first_admin(self.conn, "owner", "owner-pass-1")

    def test_first_run_setup_only_once(self):
        self.assertTrue(user_service.needs_setup(self.conn))
        session = self.first_admin()
        self.assertEqual((session.role, session.must_change_password), ("Admin", False))
        self.assertFalse(user_service.needs_setup(self.conn))
        with self.assertRaises(ConflictError):
            self.first_admin()
        self.assertEqual(auth_service.authenticate(self.conn, "owner", "owner-pass-1").role, "Admin")

    def test_first_run_validation(self):
        for name, password in (("ab", "long-enough-1"), ("bad name!", "long-enough-1"), ("owner", "short"),
                               ("owner12345", "owner12345")):
            with self.assertRaises(ValidationError, msg=name):
                user_service.create_first_admin(self.conn, name, password)
        self.assertTrue(user_service.needs_setup(self.conn))

    def test_create_user_with_temporary_password(self):
        admin = self.first_admin()
        user_service.create_user(self.conn, admin, "Sara", "Cashier", "temporary-1")
        session = auth_service.authenticate(self.conn, "sara", "temporary-1")     # usernames ignore case
        self.assertEqual((session.role, session.must_change_password), ("Cashier", True))
        with self.assertRaises(ConflictError):
            user_service.create_user(self.conn, admin, "SARA", "Cashier", "temporary-2")
        with self.assertRaises(ValidationError):
            user_service.create_user(self.conn, admin, "bob", "Boss", "temporary-1")
        with self.assertRaises(ValidationError):
            user_service.create_user(self.conn, admin, "bob", "Cashier", "short")
        with self.assertRaises(PermissionDenied):
            user_service.create_user(self.conn, session, "other-user", "Cashier", "temporary-1")

    def test_disable_enable_and_last_admin_protection(self):
        a = self.first_admin()
        cashier_id = user_service.create_user(self.conn, a, "sara", "Cashier", "temporary-1")
        user_service.set_active(self.conn, a, cashier_id, False)
        with self.assertRaises(AuthenticationError):
            auth_service.authenticate(self.conn, "sara", "temporary-1")
        user_service.set_active(self.conn, a, cashier_id, True)
        self.assertEqual(auth_service.authenticate(self.conn, "sara", "temporary-1").username, "sara")
        with self.assertRaises(ValidationError):
            user_service.set_active(self.conn, a, a.user_id, False)               # not your own account
        b_id = user_service.create_user(self.conn, a, "second", "Admin", "temporary-2")
        b = Session(b_id, "second", "Admin")
        user_service.set_active(self.conn, b, a.user_id, False)                   # fine: b stays active
        with self.assertRaises(ValidationError) as ctx:
            user_service.set_active(self.conn, a, b_id, False)                    # b is now the last admin
        self.assertIn("administrator", str(ctx.exception))

    def test_set_role(self):
        a = self.first_admin()
        cid = user_service.create_user(self.conn, a, "sara", "Cashier", "temporary-1")
        user_service.set_role(self.conn, a, cid, "Admin")
        self.assertEqual(self.scalar("SELECT role FROM users WHERE id = ?", cid), "Admin")
        with self.assertRaises(ValidationError):
            user_service.set_role(self.conn, a, a.user_id, "Cashier")             # not your own role
        sara = Session(cid, "sara", "Admin")
        user_service.set_role(self.conn, sara, a.user_id, "Cashier")              # sara stays admin
        with self.assertRaises(ValidationError):
            user_service.set_role(self.conn, a, cid, "Cashier")                   # sara is now the last admin

    def test_reset_password_unlocks_and_forces_a_change(self):
        a = self.first_admin()
        cid = user_service.create_user(self.conn, a, "sara", "Cashier", "temporary-1")
        session = auth_service.authenticate(self.conn, "sara", "temporary-1")
        auth_service.change_password(self.conn, session, "my-own-password")
        self.conn.execute("UPDATE users SET locked_until = '2999-01-01 00:00:00', failed_attempts = 3 WHERE id = ?", (cid,))
        user_service.reset_password(self.conn, a, cid, "reset-pass-9")
        with self.assertRaises(AuthenticationError):
            auth_service.authenticate(self.conn, "sara", "my-own-password")
        self.assertTrue(auth_service.authenticate(self.conn, "sara", "reset-pass-9").must_change_password)

    def test_list_users_and_permissions(self):
        a = self.first_admin()
        user_service.create_user(self.conn, a, "sara", "Cashier", "temporary-1")
        self.assertEqual([r["username"] for r in user_service.list_users(self.conn, a)], ["owner", "sara"])
        with self.assertRaises(PermissionDenied):
            user_service.list_users(self.conn, CASHIER)


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.live = os.path.join(self.dir.name, "live.db")
        self.conn = connect(self.live)
        self.addCleanup(self.conn.close)
        ensure_schema(self.conn)
        apply_upgrades(self.conn)

    def names(self):
        return [r["name"] for r in self.conn.execute("SELECT name FROM products ORDER BY id")]

    def test_restore_goes_back_to_the_backup_and_keeps_a_safety_copy(self):
        add_product(self.conn, "Before")
        backup = backup_service.create_backup(self.conn, ADMIN, Path(self.dir.name) / "b.db")
        add_product(self.conn, "After")
        safety = backup_service.restore_backup(self.conn, ADMIN, backup, Path(self.dir.name) / "safe")
        self.assertEqual(self.names(), ["Before"])
        copy = sqlite3.connect(str(safety))
        self.assertEqual([r[0] for r in copy.execute("SELECT name FROM products ORDER BY id")], ["Before", "After"])
        copy.close()
        self.assertTrue(backup_service.health_check(self.conn, ADMIN).ok)
        add_product(self.conn, "Again")                         # the connection is still fully usable
        self.assertEqual(self.names(), ["Before", "Again"])

    def test_old_format_backup_is_upgraded_on_restore(self):
        legacy = os.path.join(self.dir.name, "old.db")
        build_legacy_db(legacy)
        backup_service.restore_backup(self.conn, ADMIN, legacy, Path(self.dir.name) / "safe")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM products WHERE is_active = 1").fetchone()[0], 4)
        self.assertEqual(applied_version(self.conn), LATEST_VERSION)
        self.assertTrue(backup_service.health_check(self.conn, ADMIN).ok)

    def test_bad_files_are_rejected_and_nothing_changes(self):
        add_product(self.conn, "Keep")
        junk = Path(self.dir.name) / "junk.db"
        junk.write_text("this is not a database")
        other = os.path.join(self.dir.name, "other.db")
        raw = sqlite3.connect(other)
        raw.execute("CREATE TABLE x (a)")
        raw.commit()
        raw.close()
        newer = os.path.join(self.dir.name, "newer.db")
        raw = sqlite3.connect(newer)
        raw.execute("CREATE TABLE users (a)")
        raw.execute("CREATE TABLE products (a)")
        raw.execute("PRAGMA user_version = 99")
        raw.commit()
        raw.close()
        for bad in (junk, other, newer, Path(self.dir.name) / "missing.db"):
            with self.assertRaises(ValidationError, msg=str(bad)):
                backup_service.restore_backup(self.conn, ADMIN, bad, Path(self.dir.name) / "safe")
        self.assertEqual(self.names(), ["Keep"])

    def test_permissions(self):
        with self.assertRaises(PermissionDenied):
            backup_service.restore_backup(self.conn, CASHIER, self.live, self.dir.name)
        with self.assertRaises(PermissionDenied):
            backup_service.health_check(self.conn, CASHIER)

    def test_health_check_flags_a_stock_mismatch(self):
        pid = add_product(self.conn, "Tea", stock=5)
        self.assertTrue(backup_service.health_check(self.conn, ADMIN).ok)
        self.conn.execute("UPDATE products SET stock = 99 WHERE id = ?", (pid,))      # bypass the ledger
        report = backup_service.health_check(self.conn, ADMIN)
        self.assertFalse(report.ok)
        self.assertEqual(report.ledger_problems, 1)

    def test_list_backups_newest_first_and_only_database_files(self):
        folder = Path(self.dir.name) / "bk"
        folder.mkdir()
        for name, stamp in (("a.db", 1000), ("b.db", 2000)):
            (folder / name).write_bytes(b"x")
            os.utime(folder / name, (stamp, stamp))
        (folder / "notes.txt").write_text("hi")
        self.assertEqual([p.name for p in backup_service.list_backups(folder)], ["b.db", "a.db"])
        self.assertEqual(backup_service.list_backups(Path(self.dir.name) / "nope"), [])


if __name__ == "__main__":
    unittest.main()

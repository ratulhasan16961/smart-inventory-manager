import os
import sqlite3
import tempfile
import unittest

from pos_erp.db import connect, ensure_schema
from pos_erp.db.migrations import current_version
from pos_erp.db.schema import SCHEMA_VERSION
from pos_erp.security import legacy_sha256
from pos_erp.services import auth_service

LEGACY_DDL = """
CREATE TABLE inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT, buying_price REAL,
    price REAL, stock INTEGER, min_alert INTEGER, batch TEXT, barcode TEXT UNIQUE, warehouse TEXT, expiry TEXT);
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT);
CREATE TABLE suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT, email TEXT, address TEXT);
CREATE TABLE purchase_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, po_number TEXT, supplier_name TEXT,
    product_name TEXT, quantity INTEGER, total_cost REAL, status TEXT DEFAULT 'Completed', date_created TEXT);
CREATE TABLE customers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT UNIQUE,
    loyalty_points INTEGER DEFAULT 0, total_spent REAL DEFAULT 0.0);
CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_no TEXT, customer_name TEXT, customer_phone TEXT,
    product_id INTEGER, quantity INTEGER, subtotal REAL, discount REAL, tax REAL, total_price REAL,
    payment_method TEXT, cashier_name TEXT, sale_date TEXT);
CREATE TABLE audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, timestamp TEXT);
CREATE TABLE sales_returns (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_no TEXT, product_id INTEGER,
    quantity INTEGER, refund_amount REAL, reason TEXT, return_date TEXT);
"""


def build_legacy_db(path):
    raw = sqlite3.connect(path)
    raw.executescript(LEGACY_DDL)
    raw.executemany(
        "INSERT INTO inventory (name, category, buying_price, price, stock, min_alert, batch, barcode, warehouse, expiry)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("Wireless Mouse", "Electronics", 8.0, 15.0, 25, 5, "BT-01", "111", "Central Warehouse", "2030-01"),
         ("Keyboard", "Electronics", 35.0, 55.5, 12, 3, "KB-99", "", "Central Warehouse", ""),     # blank barcode
         ("Cable", "Electronics", 2.5, 6.0, 50, 10, "CB-05", "222", "", ""),
         ("Gone", "General", 1.0, 2.0, 0, 0, "", None, "", "")])
    raw.execute("INSERT INTO users (username, password, role) VALUES ('admin', ?, 'Admin')",
                (legacy_sha256("1234")[len("sha256$"):],))
    raw.execute("INSERT INTO users (username, password, role) VALUES ('boss', ?, 'Admin')",
                (legacy_sha256("hunter2hunter2")[len("sha256$"):],))
    raw.execute("INSERT INTO suppliers (name, phone) VALUES ('Acme', '555')")
    raw.execute("INSERT INTO customers (name, phone, loyalty_points, total_spent) VALUES ('Sam', '0170', 1, 120.5)")
    # Original-style multi-line invoice: whole invoice discount/tax/total repeated on every line.
    # subtotal 15*2 + 55.5 = 85.5, discount 5.5, tax 8.0 -> total 88.0
    for pid, qty, sub in ((1, 2, 30.0), (2, 1, 55.5)):
        raw.execute("INSERT INTO sales (invoice_no, customer_name, customer_phone, product_id, quantity, subtotal,"
                    " discount, tax, total_price, payment_method, cashier_name, sale_date)"
                    " VALUES ('INV-A','Sam','0170',?,?,?,5.5,8.0,88.0,'Cash','admin','2026-01-01 10:00:00')",
                    (pid, qty, sub))
    # Revised-style invoice: per-line shares (two equal lines -> identical values on both rows!)
    for pid in (1, 3):
        raw.execute("INSERT INTO sales (invoice_no, customer_name, customer_phone, product_id, quantity, subtotal,"
                    " discount, tax, total_price, payment_method, cashier_name, sale_date)"
                    " VALUES ('INV-B','Walk-in Customer','N/A',?,1,10.0,1.0,0.9,9.9,'Card','cashier','2026-01-02 11:00:00')",
                    (pid,))
    # Sale of a product that was later hard-deleted (id 99)
    raw.execute("INSERT INTO sales (invoice_no, customer_name, customer_phone, product_id, quantity, subtotal,"
                " discount, tax, total_price, payment_method, cashier_name, sale_date)"
                " VALUES ('INV-C','Walk-in Customer','N/A',99,1,4.0,0,0,4.0,'Cash','admin','2026-01-03 09:00:00')")
    raw.execute("INSERT INTO sales_returns (invoice_no, product_id, quantity, refund_amount, reason, return_date)"
                " VALUES ('INV-A', 1, 1, NULL, '', '2026-01-04 10:00:00')")      # amount never saved by old app
    raw.execute("INSERT INTO sales_returns (invoice_no, product_id, quantity, refund_amount, reason, return_date)"
                " VALUES ('INV-NOPE', 1, 1, 1.0, '', '2026-01-04 10:00:00')")    # orphan -> skipped
    raw.execute("INSERT INTO audit_logs (username, action, timestamp) VALUES ('admin','User logged in','2026-01-01 09:00:00')")
    raw.commit()
    raw.close()


class FreshDatabaseTests(unittest.TestCase):
    def test_creates_current_schema_once(self):
        conn = connect(":memory:")
        self.assertTrue(ensure_schema(conn))
        self.assertEqual(current_version(conn), SCHEMA_VERSION)
        self.assertFalse(ensure_schema(conn))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ("products", "invoices", "invoice_items", "stock_movements", "purchase_orders", "users"):
            self.assertIn(name, tables)

    def test_constraints_are_enforced(self):
        conn = connect(":memory:")
        ensure_schema(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO products (name, stock) VALUES ('x', -1)")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price_cents,"
                         " unit_cost_cents, line_subtotal_cents, line_total_cents) VALUES (999, 1, 'x', 1, 0, 0, 0, 0)")


class LegacyUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "legacy.db")
        build_legacy_db(self.path)
        self.conn = connect(self.path)
        self.assertTrue(ensure_schema(self.conn))

    def tearDown(self):
        self.conn.close()
        self.dir.cleanup()

    def q(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()

    def test_backup_file_created_and_version_set(self):
        self.assertEqual(current_version(self.conn), SCHEMA_VERSION)
        self.assertTrue([f for f in os.listdir(self.dir.name) if ".pre-migration-" in f])
        self.assertFalse(ensure_schema(self.conn))  # idempotent

    def test_products_converted_with_ids_preserved(self):
        rows = {r["id"]: r for r in self.q("SELECT * FROM products")}
        self.assertEqual(rows[1]["price_cents"], 1500)
        self.assertEqual(rows[2]["price_cents"], 5550)
        self.assertEqual(rows[2]["cost_cents"], 3500)
        self.assertIsNone(rows[2]["barcode"])      # the legacy '' barcode became NULL (many NULLs are allowed)
        self.assertIsNone(rows[4]["barcode"])
        self.assertEqual(rows[1]["barcode"], "111")

    def test_ledger_matches_stock_from_day_one(self):
        bad = self.q("SELECT p.id FROM products p LEFT JOIN stock_movements m ON m.product_id = p.id "
                     "GROUP BY p.id HAVING p.stock <> COALESCE(SUM(m.qty_change), 0)")
        self.assertEqual(bad, [])

    def test_users_migrated_and_default_password_flagged(self):
        users = {r["username"]: r for r in self.q("SELECT * FROM users")}
        self.assertEqual(users["admin"]["must_change_password"], 1)
        self.assertEqual(users["boss"]["must_change_password"], 0)
        session = auth_service.authenticate(self.conn, "boss", "hunter2hunter2")   # legacy hash still works
        self.assertEqual(session.role, "Admin")
        self.assertTrue(self.q("SELECT password_hash FROM users WHERE username='boss'")[0][0].startswith("scrypt$"))

    def test_original_style_invoice_is_not_overcounted(self):
        inv = self.q("SELECT * FROM invoices WHERE invoice_no = 'INV-A'")[0]
        self.assertEqual((inv["subtotal_cents"], inv["discount_cents"], inv["tax_cents"], inv["total_cents"]),
                         (8550, 550, 800, 8800))
        lines = self.q("SELECT * FROM invoice_items WHERE invoice_id = ?", inv["id"])
        self.assertEqual(sum(l["line_total_cents"] for l in lines), inv["total_cents"])

    def test_revised_style_invoice_with_equal_lines_is_summed(self):
        inv = self.q("SELECT * FROM invoices WHERE invoice_no = 'INV-B'")[0]
        self.assertEqual(inv["total_cents"], 1980)       # 2 x 9.90, NOT 9.90
        self.assertEqual(inv["payment_method"], "Card")

    def test_deleted_product_history_is_kept(self):
        item = self.q("SELECT p.name, p.is_active FROM invoice_items ii JOIN products p ON p.id = ii.product_id "
                      "JOIN invoices i ON i.id = ii.invoice_id WHERE i.invoice_no = 'INV-C'")[0]
        self.assertEqual((item["name"], item["is_active"]), ("Deleted product #99", 0))

    def test_returns_migrated_and_orphans_skipped(self):
        rows = self.q("SELECT * FROM sales_returns")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["refund_cents"], 1544)  # half of line total 30.00 - 1.93 discount + 2.81 tax = 30.88
        self.assertEqual(self.q("SELECT COUNT(*) FROM invoice_items WHERE id = ?", rows[0]["invoice_item_id"])[0][0], 1)

    def test_other_tables_and_cleanup(self):
        self.assertEqual(self.q("SELECT total_spent_cents FROM customers")[0][0], 12050)
        self.assertEqual(self.q("SELECT name FROM suppliers")[0][0], "Acme")
        self.assertTrue(self.q("SELECT 1 FROM audit_logs WHERE action = 'User logged in'"))
        names = {r[0] for r in self.q("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse([n for n in names if n.startswith("legacy_")])
        self.assertEqual(self.q("PRAGMA foreign_key_check"), [])


if __name__ == "__main__":
    unittest.main()

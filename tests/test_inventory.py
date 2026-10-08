import csv
import os
import tempfile
import unittest
from datetime import datetime

from pos_erp.errors import (ConflictError, InsufficientStockError, NotFoundError, PermissionDenied,
                            ValidationError)
from pos_erp.services import inventory_service as inv
from pos_erp.services import stock_service
from pos_erp.services.inventory_service import ProductForm
from tests.helpers import ADMIN, CASHIER, DbTestCase, add_product


class ProductTests(DbTestCase):
    def test_add_stores_cents_and_opening_ledger_row(self):
        pid = add_product(self.conn, price="15.50", cost="8.25", stock=25)
        row = inv.get_product(self.conn, ADMIN, pid)
        self.assertEqual((row["price_cents"], row["cost_cents"], row["stock"]), (1550, 825, 25))
        m = stock_service.list_movements(self.conn, ADMIN)[0]
        self.assertEqual((m["reason"], m["qty_change"], m["stock_after"]), ("OPENING", 25, 25))
        self.assertEqual(stock_service.reconcile(self.conn, ADMIN), [])

    def test_blank_barcodes_do_not_collide_but_real_duplicates_do(self):
        add_product(self.conn, "A")
        add_product(self.conn, "B")                      # two blank barcodes: OK
        add_product(self.conn, "C", barcode="X1")
        with self.assertRaises(ConflictError):
            add_product(self.conn, "D", barcode="X1")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM products"), 3)   # failed add left nothing behind

    def test_validation(self):
        for kwargs in ({"name": " "}, {"price": "abc"}, {"price": "-1"}, {"stock": "2.5"}, {"expiry": "2027/03"}):
            form = ProductForm(**{"name": "X", **kwargs})
            with self.assertRaises(ValidationError, msg=str(kwargs)):
                inv.add_product(self.conn, ADMIN, form)

    def test_permissions_enforced_in_service(self):
        with self.assertRaises(PermissionDenied):
            inv.add_product(self.conn, CASHIER, ProductForm(name="X"))
        pid = add_product(self.conn)
        for call in (lambda: inv.deactivate_product(self.conn, CASHIER, pid),
                     lambda: inv.export_csv(self.conn, CASHIER, "x.csv"),
                     lambda: inv.adjust_stock(self.conn, CASHIER, pid, 1, "x")):
            with self.assertRaises(PermissionDenied):
                call()
        self.assertEqual(inv.get_product(self.conn, CASHIER, pid)["id"], pid)   # viewing is allowed

    def test_edit_logs_old_and_new_values_and_requires_reason_for_stock(self):
        pid = add_product(self.conn, price="10.00", stock=10)
        form = ProductForm(name="Widget", price="12.00", buying_price="6.00", stock="14", min_alert="2")
        with self.assertRaises(ValidationError):
            inv.update_product(self.conn, ADMIN, pid, form)                       # no reason given
        self.assertEqual(self.stock(pid), 10)
        inv.update_product(self.conn, ADMIN, pid, form, stock_note="Stock take")
        self.assertEqual(self.stock(pid), 14)
        log = self.scalar("SELECT action FROM audit_logs WHERE action LIKE 'Updated product%' ORDER BY id DESC")
        self.assertIn("price 10.00 -> 12.00", log)
        self.assertIn("stock 10 -> 14 (Stock take)", log)
        m = stock_service.list_movements(self.conn, ADMIN)[0]
        self.assertEqual((m["reason"], m["qty_change"], m["note"]), ("ADJUSTMENT", 4, "Stock take"))

    def test_adjust_stock_rules(self):
        pid = add_product(self.conn, stock=5)
        self.assertEqual(inv.adjust_stock(self.conn, ADMIN, pid, -2, "Damaged"), 3)
        with self.assertRaises(ValidationError):
            inv.adjust_stock(self.conn, ADMIN, pid, -1, " ")
        with self.assertRaises(ValidationError):
            inv.adjust_stock(self.conn, ADMIN, pid, 0, "x")
        with self.assertRaises(InsufficientStockError):
            inv.adjust_stock(self.conn, ADMIN, pid, -10, "Oops")
        self.assertEqual(self.stock(pid), 3)

    def test_soft_delete_keeps_history_and_frees_barcode(self):
        pid = add_product(self.conn, barcode="B1")
        inv.deactivate_product(self.conn, ADMIN, pid)
        self.assertEqual(inv.list_products(self.conn, ADMIN), [])
        with self.assertRaises(NotFoundError):
            inv.find_for_sale(self.conn, ADMIN, "B1")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM products WHERE id = ?", pid), 1)
        add_product(self.conn, "Replacement", barcode="B1")   # barcode can be reused

    def test_search_and_lookup(self):
        a = add_product(self.conn, "Green Tea", barcode="9001", category="Grocery")
        add_product(self.conn, "Mouse", category="Electronics")
        self.assertEqual([r["name"] for r in inv.list_products(self.conn, ADMIN, "tea")], ["Green Tea"])
        self.assertEqual([r["name"] for r in inv.list_products(self.conn, ADMIN, "electr")], ["Mouse"])
        self.assertEqual(len(inv.list_products(self.conn, ADMIN, "%")), 0)    # wildcard is escaped
        self.assertEqual(inv.find_for_sale(self.conn, ADMIN, "9001")["id"], a)
        self.assertEqual(inv.find_for_sale(self.conn, ADMIN, str(a))["id"], a)

    def test_expiry_flags(self):
        today = datetime(2026, 10, 5)
        self.assertEqual(inv.expiry_status("2026-09", today), "expired")
        self.assertEqual(inv.expiry_status("2026-10", today), "expiring")   # valid until end of October
        self.assertIsNone(inv.expiry_status("2027-06", today))
        self.assertIsNone(inv.expiry_status("", today))
        self.assertIsNone(inv.expiry_status("garbage", today))


class CsvTests(DbTestCase):
    def write_csv(self, rows, header="name,category,buying_price,price,stock,min_alert,batch,barcode,warehouse,expiry"):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write(header + "\n")
            for r in rows:
                f.write(r + "\n")
        return path

    def test_export_import_roundtrip_keeps_ids(self):
        a = add_product(self.conn, "Mouse", price="15.00", cost="8.00", stock=25, barcode="111")
        b = add_product(self.conn, "Keyboard", price="55.50", cost="35.00", stock=12)
        path = self.write_csv([])
        inv.export_csv(self.conn, ADMIN, path)
        with open(path, newline="") as f:
            self.assertEqual(next(csv.reader(f)), list(inv.CSV_COLUMNS))
        report = inv.import_csv(self.conn, ADMIN, path)    # re-importing the export changes nothing
        self.assertTrue(report.ok)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM products"), 2)
        self.assertEqual(self.scalar("SELECT id FROM products WHERE barcode = '111'"), a)
        self.assertEqual(inv.get_product(self.conn, ADMIN, b)["price_cents"], 5550)

    def test_import_upserts_by_barcode_then_name_and_records_ledger(self):
        a = add_product(self.conn, "Mouse", price="15.00", stock=10, barcode="111")
        b = add_product(self.conn, "Keyboard", price="50.00", stock=5)
        path = self.write_csv(["Mouse v2,Electronics,8,16.00,15,2,,111,Central,",     # barcode match -> update
                               "keyboard,Electronics,30,55.00,5,1,,,Central,",         # name match -> update
                               "Cable,Electronics,2,6.00,40,5,,222,Central,2030-01"])  # new
        report = inv.import_csv(self.conn, ADMIN, path)
        self.assertEqual((report.created, report.updated, report.ok), (1, 2, True))
        self.assertEqual(inv.get_product(self.conn, ADMIN, a)["name"], "Mouse v2")
        self.assertEqual(self.stock(a), 15)
        self.assertEqual(inv.get_product(self.conn, ADMIN, b)["price_cents"], 5500)
        imports = self.scalar("SELECT COUNT(*) FROM stock_movements WHERE reason = 'IMPORT'")
        self.assertEqual(imports, 2)             # Mouse +5, Cable +40 (Keyboard unchanged)
        self.assertEqual(stock_service.reconcile(self.conn, ADMIN), [])

    def test_bad_rows_abort_the_whole_import(self):
        add_product(self.conn, "Mouse", stock=10, barcode="111")
        path = self.write_csv(["Mouse,E,1,1,99,0,,111,,", "Bad,E,abc,1,1,0,,,,", "Dup,E,1,1,1,0,,222,,",
                               "Dup2,E,1,1,1,0,,222,,"])
        report = inv.import_csv(self.conn, ADMIN, path)
        self.assertFalse(report.ok)
        self.assertEqual(len(report.errors), 2)
        self.assertEqual(self.scalar("SELECT stock FROM products WHERE barcode = '111'"), 10)   # nothing applied
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM products"), 1)

    def test_dry_run_changes_nothing(self):
        path = self.write_csv(["New,E,1,2,3,0,,555,,"])
        report = inv.import_csv(self.conn, ADMIN, path, dry_run=True)
        self.assertEqual((report.created, report.ok), (1, True))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM products"), 0)

    def test_missing_name_column_and_permissions(self):
        path = self.write_csv(["1,2"], header="price,stock")
        self.assertFalse(inv.import_csv(self.conn, ADMIN, path).ok)
        with self.assertRaises(PermissionDenied):
            inv.import_csv(self.conn, CASHIER, path)


if __name__ == "__main__":
    unittest.main()

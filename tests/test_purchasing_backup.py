import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pos_erp.errors import NotFoundError, PermissionDenied, ValidationError
from pos_erp.services import backup_service as backup
from pos_erp.services import purchase_service as po
from pos_erp.services import stock_service
from pos_erp.services.purchase_service import POLine
from tests.helpers import ADMIN, CASHIER, DbTestCase, add_product


class SupplierTests(DbTestCase):
    def test_supplier_lifecycle_and_permissions(self):
        sid = po.add_supplier(self.conn, ADMIN, " Acme ", "555", "a@x.com", "Dhaka")
        self.assertEqual([r["name"] for r in po.list_suppliers(self.conn, ADMIN)], ["Acme"])
        po.deactivate_supplier(self.conn, ADMIN, sid)
        self.assertEqual(po.list_suppliers(self.conn, ADMIN), [])
        self.assertEqual(len(po.list_suppliers(self.conn, ADMIN, include_inactive=True)), 1)
        with self.assertRaises(ValidationError):
            po.add_supplier(self.conn, ADMIN, "  ")
        with self.assertRaises(PermissionDenied):
            po.add_supplier(self.conn, CASHIER, "X")


class PurchaseOrderTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.supplier = po.add_supplier(self.conn, ADMIN, "Acme")
        self.p1 = add_product(self.conn, "Mouse", cost="8.00", stock=5)
        self.p2 = add_product(self.conn, "Cable", cost="2.50", stock=0)

    def make_po(self):
        return po.create_purchase_order(self.conn, ADMIN, self.supplier,
                                        [POLine(self.p1, 10, "7.50"), POLine(self.p2, 20, "2.00")], "urgent")

    def items(self, po_id):
        return {r["product_id"]: r for r in po.get_po_items(self.conn, ADMIN, po_id)}

    def test_create_po(self):
        po_id = self.make_po()
        row = po.list_purchase_orders(self.conn, ADMIN)[0]
        self.assertEqual((row["status"], row["total_cents"], row["ordered"], row["received"]), ("Ordered", 11500, 30, 0))
        self.assertRegex(row["po_number"], r"^PO-\d{8}-0001$")
        self.assertEqual(self.stock(self.p1), 5)          # ordering alone does not change stock
        self.assertTrue(self.make_po() != po_id)

    def test_partial_then_full_receive_updates_stock_ledger_and_cost(self):
        po_id = self.make_po()
        items = self.items(po_id)
        self.assertEqual(po.receive_purchase_order(self.conn, ADMIN, po_id, {items[self.p1]["id"]: 4}),
                         "Partially Received")
        self.assertEqual(self.stock(self.p1), 9)
        self.assertEqual(self.scalar("SELECT cost_cents FROM products WHERE id = ?", self.p1), 750)   # last cost
        status = po.receive_purchase_order(self.conn, ADMIN, po_id,
                                           {items[self.p1]["id"]: 6, items[self.p2]["id"]: 20})
        self.assertEqual(status, "Received")
        self.assertEqual((self.stock(self.p1), self.stock(self.p2)), (15, 20))
        self.assertIsNotNone(self.scalar("SELECT closed_at FROM purchase_orders WHERE id = ?", po_id))
        self.assertEqual(stock_service.reconcile(self.conn, ADMIN), [])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM stock_movements WHERE reason = 'PURCHASE'"), 3)

    def test_cannot_over_receive_and_failure_is_atomic(self):
        po_id = self.make_po()
        items = self.items(po_id)
        with self.assertRaises(ValidationError):
            po.receive_purchase_order(self.conn, ADMIN, po_id, {items[self.p2]["id"]: 5, items[self.p1]["id"]: 11})
        self.assertEqual((self.stock(self.p1), self.stock(self.p2)), (5, 0))      # p2 line NOT applied either
        with self.assertRaises(ValidationError):
            po.receive_purchase_order(self.conn, ADMIN, po_id, {})
        with self.assertRaises(NotFoundError):
            po.receive_purchase_order(self.conn, ADMIN, po_id, {999999: 1})

    def test_closed_orders_cannot_receive(self):
        po_id = self.make_po()
        po.cancel_purchase_order(self.conn, ADMIN, po_id)
        with self.assertRaises(ValidationError):
            po.receive_purchase_order(self.conn, ADMIN, po_id, {self.items(po_id)[self.p1]["id"]: 1})

    def test_cancel_rules(self):
        po_id = self.make_po()
        po.receive_purchase_order(self.conn, ADMIN, po_id, {self.items(po_id)[self.p1]["id"]: 1})
        with self.assertRaises(ValidationError):
            po.cancel_purchase_order(self.conn, ADMIN, po_id)       # already partly received
        other = self.make_po()
        po.cancel_purchase_order(self.conn, ADMIN, other)
        self.assertEqual(po.list_purchase_orders(self.conn, ADMIN, "Cancelled")[0]["id"], other)

    def test_validation_and_permissions(self):
        bad_inputs = ([], [POLine(self.p1, 0, "1")], [POLine(self.p1, 1, "abc")], [POLine(self.p1, 1, "-1")],
                      [POLine(self.p1, 1, "1"), POLine(self.p1, 2, "1")])
        for lines in bad_inputs:
            with self.assertRaises(ValidationError):
                po.create_purchase_order(self.conn, ADMIN, self.supplier, lines)
        with self.assertRaises(NotFoundError):
            po.create_purchase_order(self.conn, ADMIN, 999, [POLine(self.p1, 1, "1")])
        with self.assertRaises(NotFoundError):
            po.create_purchase_order(self.conn, ADMIN, self.supplier, [POLine(9999, 1, "1")])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM purchase_orders"), 0)     # failed creates left nothing
        with self.assertRaises(PermissionDenied):
            po.create_purchase_order(self.conn, CASHIER, self.supplier, [POLine(self.p1, 1, "1")])


class LedgerTests(DbTestCase):
    def test_ledger_rejects_negative_stock_and_outside_transactions(self):
        pid = add_product(self.conn, stock=1)
        with self.assertRaises(RuntimeError):
            stock_service.apply_stock_change(self.conn, pid, 1, "ADJUSTMENT", "x")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE products SET stock = -1 WHERE id = ?", (pid,))

    def test_reconcile_detects_tampering_and_filters(self):
        pid = add_product(self.conn, "Findable", stock=5)
        self.conn.execute("UPDATE products SET stock = 99 WHERE id = ?", (pid,))    # bypass the ledger
        bad = stock_service.reconcile(self.conn, ADMIN)
        self.assertEqual((bad[0]["recorded"], bad[0]["ledger"]), (99, 5))
        self.assertEqual(len(stock_service.list_movements(self.conn, ADMIN, "findable")), 1)
        self.assertEqual(stock_service.list_movements(self.conn, ADMIN, "zzz"), [])
        with self.assertRaises(PermissionDenied):
            stock_service.list_movements(self.conn, CASHIER)


class BackupTests(DbTestCase):
    def test_backup_is_a_complete_readable_database(self):
        add_product(self.conn, "Mouse")
        with tempfile.TemporaryDirectory() as folder:
            path = backup.create_backup(self.conn, ADMIN, Path(folder) / "sub" / "copy.db")
            copy = sqlite3.connect(str(path))
            self.assertEqual(copy.execute("SELECT name FROM products").fetchone()[0], "Mouse")
            copy.close()
            with self.assertRaises(PermissionDenied):
                backup.create_backup(self.conn, CASHIER, Path(folder) / "x.db")

    def test_auto_backup_prunes_old_files(self):
        with tempfile.TemporaryDirectory() as folder:
            for i in range(5):
                (Path(folder) / f"backup_20260101_00000{i}.db").write_bytes(b"old")
            newest = backup.auto_backup(self.conn, folder, keep=3)
            names = sorted(os.listdir(folder))
            self.assertEqual(len(names), 3)
            self.assertIn(newest.name, names)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from pos_erp import config
from pos_erp.db import connect
from pos_erp.services import auth_service, backup_service, dashboard_service, reporting_service
from scripts import seed_demo
from tests.helpers import ADMIN


class SeedDemoTests(unittest.TestCase):
    def setUp(self):
        saved = (config.CATEGORIES, config.WAREHOUSES, config.CURRENCY_SYMBOL)
        self.addCleanup(self._restore, saved)        
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "demo.db"

    @staticmethod
    def _restore(saved):
        config.CATEGORIES, config.WAREHOUSES, config.CURRENCY_SYMBOL = saved

    def test_builds_a_consistent_database(self):
        summary = seed_demo.seed(self.path)
        self.assertGreaterEqual(summary["products"], 15)
        self.assertGreaterEqual(summary["invoices"], 20)
        self.assertEqual((summary["voided"], summary["returns"], summary["purchase_orders"]), (1, 3, 3))

        conn = connect(self.path)
        self.addCleanup(conn.close)
        self.assertTrue(backup_service.health_check(conn, ADMIN).ok)    

        admin = auth_service.authenticate(conn, "admin", seed_demo.ADMIN_PASSWORD)
        cashier = auth_service.authenticate(conn, "cashier", seed_demo.CASHIER_PASSWORD)
        self.assertEqual((admin.role, admin.must_change_password), ("Admin", False))
        self.assertEqual((cashier.role, cashier.must_change_password), ("Cashier", False))

        today = date.today()
        report = reporting_service.sales_report(conn, ADMIN, (today - timedelta(days=40)).isoformat(),
                                                today.isoformat())
        self.assertGreater(report.orders, 0)
        self.assertGreater(report.net_cents, 0)
        self.assertEqual(len(dashboard_service.daily_revenue(conn, ADMIN, 30)), 30)
        self.assertGreaterEqual(len(reporting_service.low_stock_report(conn, ADMIN)), 2)
        expiry = reporting_service.expiry_report(conn, ADMIN)
        self.assertTrue(any(row[1] == "Yogurt Cup" and row[4] == "Expired" for row in expiry))

    def test_reset_rebuilds_and_existing_files_are_protected(self):
        first = seed_demo.seed(self.path)
        with self.assertRaises(seed_demo.SeedError):
            seed_demo.seed(self.path)                                
        again = seed_demo.seed(self.path, reset=True)
        self.assertEqual(again["products"], first["products"])

    def test_never_touches_the_real_database(self):
        self.path.write_text("precious")
        with self.assertRaises(seed_demo.SeedError):
            seed_demo.seed(self.path)
        with mock.patch.object(seed_demo, "REAL_DB", self.path):      
            with self.assertRaises(seed_demo.SeedError):
                seed_demo.seed(self.path, reset=True)
        self.assertEqual(self.path.read_text(), "precious")


if __name__ == "__main__":
    unittest.main()

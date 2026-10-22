import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pos_erp.db.upgrades import LATEST_VERSION
from pos_erp.errors import PermissionDenied, ValidationError
from pos_erp.permissions import Session
from pos_erp.services import credit_service as credit
from pos_erp.services import user_service
from tests.helpers import DbTestCase


class CreditLedgerTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.admin = user_service.create_first_admin(self.conn, "owner", "owner-pass-1")
        self.cashier = Session(2, "sara", "Cashier")
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Sam', '0170')")
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Ana', '0180')")
        self.sam = credit.customer_id_for(self.conn, "0170")
        self.ana = credit.customer_id_for(self.conn, "0180")
        credit.post(self.conn, self.sam, "CHARGE", 4000, "owner", note="INV-1")

    def test_balance_and_running_ledger(self):
        credit.post(self.conn, self.sam, "PAYMENT", -1500, "owner", method="Cash")
        lines = credit.ledger(self.conn, self.cashier, "0170")
        self.assertEqual([(l.entry_type, l.amount_cents, l.balance_cents) for l in lines],
                         [("CHARGE", 4000, 4000), ("PAYMENT", -1500, 2500)])
        account = credit.get_account(self.conn, self.cashier, "0170")
        self.assertEqual((account.name, account.balance_cents), ("Sam", 2500))
        self.assertEqual(credit.balance_of_phone(self.conn, "nobody"), 0)

    def test_receive_payment_partial_then_full(self):
        self.assertEqual(credit.receive_payment(self.conn, self.cashier, "0170", "10", "Cash", "first"), 3000)
        self.assertEqual(credit.receive_payment(self.conn, self.cashier, "0170", "30", "bKash / Mobile"), 0)
        with self.assertRaises(ValidationError):
            credit.receive_payment(self.conn, self.cashier, "0170", "1", "Cash")

    def test_payment_validation(self):
        for amount, method in (("0", "Cash"), ("-5", "Cash"), ("abc", "Cash"), ("", "Cash"), ("41", "Cash"),
                               ("5", "Credit (Due)"), ("5", "Barter")):
            with self.assertRaises(ValidationError, msg=f"{amount} {method}"):
                credit.receive_payment(self.conn, self.cashier, "0170", amount, method)
        self.assertEqual(credit.balance_of(self.conn, self.sam), 4000)

    def test_adjustments_and_limits_are_administrator_only(self):
        self.assertEqual(credit.adjust(self.conn, self.admin, "0170", "-5", "Goodwill write-off"), 3500)
        with self.assertRaises(ValidationError):
            credit.adjust(self.conn, self.admin, "0170", "-5", "x")
        with self.assertRaises(ValidationError):
            credit.adjust(self.conn, self.admin, "0170", "0", "Nothing")
        with self.assertRaises(PermissionDenied):
            credit.adjust(self.conn, self.cashier, "0170", "-5", "Not allowed")
        with self.assertRaises(PermissionDenied):
            credit.set_credit_limit(self.conn, self.cashier, "0170", "100")
        credit.set_credit_limit(self.conn, self.admin, "0170", "250.50")
        self.assertEqual(credit.get_account(self.conn, self.admin, "0170").credit_limit_cents, 25050)
        with self.assertRaises(ValidationError):
            credit.set_credit_limit(self.conn, self.admin, "0170", "-1")

    def test_debtors_are_sorted_and_totalled(self):
        credit.post(self.conn, self.ana, "CHARGE", 9000, "owner")
        credit.post(self.conn, self.ana, "PAYMENT", -9000, "owner", method="Cash")
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Bob', '0190')")
        credit.post(self.conn, credit.customer_id_for(self.conn, "0190"), "CHARGE", 7000, "owner")
        debtors = credit.list_debtors(self.conn, self.cashier)
        self.assertEqual([d["phone"] for d in debtors], ["0190", "0170"])
        self.assertEqual(credit.total_receivable(self.conn, self.cashier), 11000)

    def test_unknown_customer_and_permissions(self):
        with self.assertRaises(ValidationError):
            credit.get_account(self.conn, self.cashier, "999")
        with self.assertRaises(PermissionDenied):
            credit.get_account(self.conn, Session(9, "x", "Nobody"), "0170")

    def test_credit_method_is_offered_only_to_sellers(self):
        self.assertIn(credit.CREDIT_METHOD, credit.sale_methods(self.cashier))
        self.assertNotIn(credit.CREDIT_METHOD, credit.sale_methods(Session(9, "x", "Nobody")))

    def test_csv_export(self):
        credit.post(self.conn, self.sam, "PAYMENT", -1000, "owner", method="Cash")
        with tempfile.TemporaryDirectory() as folder:
            path = credit.export_ledger_csv(self.conn, self.admin, "0170", Path(folder) / "ledger.csv")
            rows = list(csv.reader(open(path, encoding="utf-8")))
        self.assertEqual(rows[0][:4], ["When", "Type", "Amount", "Balance"])
        self.assertEqual([r[1] for r in rows[1:]], ["CHARGE", "PAYMENT"])
        self.assertEqual(rows[-1][3], "30.00")


class CreditSchemaTests(DbTestCase):
    def test_upgrade_creates_the_ledger_with_constraints(self):
        self.assertGreaterEqual(LATEST_VERSION, 7)
        self.conn.execute("INSERT INTO customers (name, phone) VALUES ('Sam', '0170')")
        customer = credit.customer_id_for(self.conn, "0170")
        for entry_type, amount in (("CHARGE", 0), ("GIFT", 100)):
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute("INSERT INTO customer_ledger (customer_id, entry_type, amount_cents, username, "
                                  "created_at) VALUES (?, ?, ?, 'x', 'now')", (customer, entry_type, amount))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE customers SET credit_limit_cents = -1 WHERE id = ?", (customer,))


if __name__ == "__main__":
    unittest.main()

import sqlite3
import unittest
from datetime import date, datetime
from unittest import mock

from pos_erp.db import connect, ensure_schema
from pos_erp.db.upgrades import LATEST_VERSION, apply_upgrades
from pos_erp.errors import PermissionDenied, ValidationError
from pos_erp.permissions import Session
from pos_erp.services import customer_service, dashboard_service, purchase_service, returns_service, till_service
from pos_erp.services import inventory_service as inv
from pos_erp.services.purchase_service import POLine
from pos_erp.services.receipt import render_receipt
from tests.helpers import ADMIN, CASHIER, DbTestCase, add_product

NOBODY = Session(9, "x", "Nobody")


class UpgradeV4Tests(unittest.TestCase):
    def test_columns_and_defaults(self):
        conn = connect(":memory:")
        ensure_schema(conn)
        conn.execute("INSERT INTO invoices (invoice_no, subtotal_cents, total_cents, payment_method, cashier, created_at)"
                     " VALUES ('INV-X', 500, 500, 'Cash', 'a', '2026-01-01 10:00:00')")
        self.assertEqual(apply_upgrades(conn), [2, 3, 4])
        row = conn.execute("SELECT points_redeemed, points_value_cents FROM invoices").fetchone()
        self.assertEqual((row[0], row[1]), (0, 0))
        self.assertEqual(LATEST_VERSION, 4)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE invoices SET points_redeemed = -1")


class CustomerLookupTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "Phone", price="150.00", cost="100.00", stock=50)
        self.first = till_service.checkout_with_payment(
            self.conn, ADMIN, [(self.a, 2)], customer_name="Sam", customer_phone="0170")     

    def test_lookup_and_history(self):
        info = customer_service.find_by_phone(self.conn, CASHIER, " 0170 ")
        self.assertEqual((info.name, info.points, info.spent_cents, info.visits), ("Sam", 3, 30000, 1))
        self.assertEqual(info.last_visit, self.first.created_at)
        second = till_service.checkout_with_payment(self.conn, ADMIN, [(self.a, 1)], customer_phone="0170")
        rows = customer_service.history(self.conn, CASHIER, "0170")
        self.assertEqual([r["invoice_no"] for r in rows], [second.invoice_no, self.first.invoice_no])
        self.assertEqual(customer_service.find_by_phone(self.conn, CASHIER, "0170").visits, 2)
        till_service.void_invoice(self.conn, ADMIN, second.invoice_no, "Mistake")
        self.assertEqual(customer_service.find_by_phone(self.conn, CASHIER, "0170").visits, 1)
        self.assertEqual([r["status"] for r in customer_service.history(self.conn, CASHIER, "0170")],
                         ["Voided", "Completed"])

    def test_unknown_blank_and_permissions(self):
        self.assertIsNone(customer_service.find_by_phone(self.conn, CASHIER, "0999"))
        for phone in ("", "  ", "N/A"):
            with self.assertRaises(ValidationError):
                customer_service.find_by_phone(self.conn, CASHIER, phone)
        with self.assertRaises(PermissionDenied):
            customer_service.find_by_phone(self.conn, NOBODY, "0170")
        with self.assertRaises(PermissionDenied):
            customer_service.history(self.conn, NOBODY, "0170")
        self.assertEqual(customer_service.points_value(3), 300)


class RedeemTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "Phone", price="150.00", cost="100.00", stock=50)
        self.cheap = add_product(self.conn, "Pen", price="1.00", cost="0.50", stock=50)
        till_service.checkout_with_payment(self.conn, ADMIN, [(self.a, 2)], customer_name="Sam",
                                           customer_phone="0170")                      

    def points(self):
        return self.scalar("SELECT loyalty_points FROM customers WHERE phone = '0170'")

    def spent(self):
        return self.scalar("SELECT total_spent_cents FROM customers WHERE phone = '0170'")

    def sale(self, items, **kw):
        return till_service.checkout_with_payment(self.conn, CASHIER, items, customer_phone="0170", **kw)

    def test_points_become_a_discount_and_are_spent(self):
        invoice = self.sale([(self.a, 1)], redeem_points=2)
        self.assertEqual((invoice.discount_cents, invoice.total_cents), (200, 14800))
        meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
        self.assertEqual((meta["points_redeemed"], meta["points_value_cents"]), (2, 200))
        self.assertEqual(self.points(), 2)         
        self.assertEqual(self.spent(), 44800)
        self.assertTrue(self.scalar("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'Redeemed 2 loyalty%'"))

    def test_points_add_to_a_typed_discount(self):
        invoice = self.sale([(self.a, 1)], discount="10.00", redeem_points=1)
        self.assertEqual((invoice.discount_cents, invoice.total_cents), (1100, 13900))

    def test_works_with_cash_tendering(self):
        invoice = self.sale([(self.a, 1)], redeem_points=1, tendered="150")          
        meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
        self.assertEqual((invoice.total_cents, meta["tendered_cents"], meta["change_cents"]), (14900, 15000, 100))

    def test_rejections_change_nothing(self):
        stock_before = self.stock(self.a)
        cases = (({"redeem_points": 4}, "only has 3"), ({"redeem_points": -1}, "whole number"),
                 ({"redeem_points": "x"}, "whole number"))
        for kwargs, fragment in cases:
            with self.assertRaises(ValidationError) as ctx:
                self.sale([(self.a, 1)], **kwargs)
            self.assertIn(fragment, str(ctx.exception))
        with self.assertRaises(ValidationError):
            self.sale([(self.cheap, 1)], redeem_points=2)                              
        with self.assertRaises(ValidationError):
            self.sale([(self.a, 1)], discount="149.00", redeem_points=2)              
        self.assertEqual((self.points(), self.stock(self.a), self.scalar("SELECT COUNT(*) FROM invoices")),
                         (3, stock_before, 1))

    def test_needs_a_phone_and_a_known_customer(self):
        with self.assertRaises(ValidationError):
            till_service.checkout_with_payment(self.conn, CASHIER, [(self.a, 1)], redeem_points=1)
        with self.assertRaises(ValidationError):
            till_service.checkout_with_payment(self.conn, CASHIER, [(self.a, 1)], customer_phone="0999",
                                               redeem_points=1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM invoices"), 1)

    def test_a_bill_can_be_paid_fully_with_points_and_void_gives_them_back(self):
        invoice = self.sale([(self.cheap, 1)], redeem_points=1)
        self.assertEqual(invoice.total_cents, 0)
        self.assertEqual(self.points(), 2)
        till_service.void_invoice(self.conn, ADMIN, invoice.invoice_no, "Mistake")
        self.assertEqual(self.points(), 3)

    def test_void_removes_earned_points_and_restores_spent_points(self):
        invoice = self.sale([(self.a, 1)], redeem_points=2)                            
        till_service.void_invoice(self.conn, ADMIN, invoice.invoice_no, "Mistake")
        self.assertEqual((self.points(), self.spent()), (3, 30000))

    def test_receipt_mentions_the_points(self):
        invoice = self.sale([(self.a, 1)], redeem_points=2)
        meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
        text = render_receipt(invoice, points_redeemed=meta["points_redeemed"],
                              points_value_cents=meta["points_value_cents"])
        self.assertIn("2 loyalty point(s)", text)
        self.assertNotIn("loyalty", render_receipt(invoice))

    def test_returns_still_work_on_a_redeemed_invoice(self):
        invoice = self.sale([(self.a, 1)], redeem_points=2)
        result = returns_service.process_return(self.conn, ADMIN, invoice.invoice_no, self.a, 1)
        self.assertEqual(result.refund_cents, 14800)                                   


class SortFilterTests(DbTestCase):
    def setUp(self):
        super().setUp()
        add_product(self.conn, "Beta", price="5.00", stock=1, min_alert=3, category="Grocery", barcode="2")
        add_product(self.conn, "alpha", price="9.00", stock=7, min_alert=2, category="Electronics", barcode="1")
        add_product(self.conn, "Gamma", price="1.00", stock=9, min_alert=2, category="Grocery", expiry="2000-01")
        self.rows = inv.list_products(self.conn, ADMIN)

    @staticmethod
    def names(rows):
        return [r["name"] for r in rows]

    def test_sorting(self):
        self.assertEqual(self.names(inv.sort_products(self.rows, "Name")), ["alpha", "Beta", "Gamma"])
        self.assertEqual(self.names(inv.sort_products(self.rows, "Name", True)), ["Gamma", "Beta", "alpha"])
        self.assertEqual(self.names(inv.sort_products(self.rows, "Stock")), ["Beta", "alpha", "Gamma"])
        self.assertEqual(self.names(inv.sort_products(self.rows, "Price")), ["Gamma", "Beta", "alpha"])
        self.assertEqual(self.names(inv.sort_products(self.rows, "Price", True)), ["alpha", "Beta", "Gamma"])

    def test_empty_values_always_come_last(self):
        self.assertEqual(self.names(inv.sort_products(self.rows, "Expiry")), ["Gamma", "Beta", "alpha"])
        self.assertEqual(self.names(inv.sort_products(self.rows, "Expiry", True)), ["Gamma", "Beta", "alpha"])
        self.assertEqual(self.names(inv.sort_products(self.rows, "Barcode")), ["alpha", "Beta", "Gamma"])

    def test_unknown_column_keeps_the_order(self):
        for column in ("Nope", None):
            self.assertEqual(self.names(inv.sort_products(self.rows, column)), ["Beta", "alpha", "Gamma"])

    def test_filters(self):
        self.assertEqual(self.names(inv.filter_products(self.rows, category="Grocery")), ["Beta", "Gamma"])
        self.assertEqual(self.names(inv.filter_products(self.rows, category="grocery")), ["Beta", "Gamma"])
        self.assertEqual(len(inv.filter_products(self.rows, category="All")), 3)
        self.assertEqual(len(inv.filter_products(self.rows)), 3)
        self.assertEqual(self.names(inv.filter_products(self.rows, low_stock_only=True)), ["Beta"])
        self.assertEqual(self.names(inv.filter_products(self.rows, expiry_only=True)), ["Gamma"])
        self.assertEqual(self.names(inv.filter_products(self.rows, category="Grocery", low_stock_only=True)), ["Beta"])


class ReorderTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.supplier = purchase_service.add_supplier(self.conn, ADMIN, "Acme")
        self.tea = add_product(self.conn, "Tea", cost="3.00", stock=4, min_alert=5)
        self.cable = add_product(self.conn, "Cable", cost="2.00", stock=0, min_alert=10)
        add_product(self.conn, "Fine", stock=20, min_alert=5)

    def suggestions(self):
        return {s.name: s for s in purchase_service.reorder_suggestions(self.conn, ADMIN)}

    def order(self, product, qty, cost="2.50"):
        return purchase_service.create_purchase_order(self.conn, ADMIN, self.supplier, [POLine(product, qty, cost)])

    def test_basic_rules(self):
        s = self.suggestions()
        self.assertEqual(set(s), {"Tea", "Cable"})
        self.assertEqual((s["Tea"].quantity, s["Cable"].quantity), (6, 20))       
        self.assertEqual((s["Tea"].unit_cost, s["Cable"].unit_cost), ("3.00", "2.00"))
        self.assertEqual((s["Tea"].supplier_id, s["Tea"].supplier_name), (None, ""))

    def test_open_orders_are_subtracted_and_supplier_and_cost_are_remembered(self):
        po_id = self.order(self.tea, 4)
        s = self.suggestions()
        self.assertEqual((s["Tea"].on_order, s["Tea"].quantity), (4, 2))
        self.assertEqual((s["Tea"].supplier_id, s["Tea"].supplier_name, s["Tea"].unit_cost),
                         (self.supplier, "Acme", "2.50"))
        purchase_service.cancel_purchase_order(self.conn, ADMIN, po_id)
        s = self.suggestions()
        self.assertEqual((s["Tea"].on_order, s["Tea"].quantity, s["Tea"].unit_cost), (0, 6, "3.00"))
        self.assertEqual(s["Tea"].supplier_name, "Acme")

    def test_covered_or_received_products_disappear(self):
        self.order(self.tea, 6)
        self.assertNotIn("Tea", self.suggestions())                               
        other = self.order(self.cable, 5)
        item = purchase_service.get_po_items(self.conn, ADMIN, other)[0]
        purchase_service.receive_purchase_order(self.conn, ADMIN, other, {item["id"]: 5})
        self.assertEqual(self.suggestions()["Cable"].quantity, 20 - 5 - 0)         

    def test_inactive_supplier_is_not_suggested(self):
        self.order(self.tea, 1)
        purchase_service.deactivate_supplier(self.conn, ADMIN, self.supplier)
        self.assertIsNone(self.suggestions()["Tea"].supplier_id)

    def test_suggestions_turn_into_a_purchase_order(self):
        lines = [POLine(s.product_id, s.quantity, s.unit_cost) for s in purchase_service.reorder_suggestions(self.conn, ADMIN)]
        po_id = purchase_service.create_purchase_order(self.conn, ADMIN, self.supplier, lines, "Suggested")
        items = purchase_service.get_po_items(self.conn, ADMIN, po_id)
        self.assertEqual(sorted((i["product_name"], i["quantity_ordered"], i["unit_cost_cents"]) for i in items),
                         [("Cable", 20, 200), ("Tea", 6, 300)])
        self.assertEqual(self.suggestions(), {})                                

    def test_permissions(self):
        with self.assertRaises(PermissionDenied):
            purchase_service.reorder_suggestions(self.conn, CASHIER)


class DashboardServiceTests(DbTestCase):
    def sale(self, when, items):
        with mock.patch("pos_erp.timeutil.now", return_value=when):
            return till_service.checkout_with_payment(self.conn, ADMIN, items)

    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "A", price="10.00", cost="6.00", stock=100)
        self.b = add_product(self.conn, "B", price="20.00", cost="5.00", stock=100)
        first = self.sale(datetime(2026, 10, 6, 10, 0), [(self.a, 2)])           
        self.sale(datetime(2026, 10, 7, 12, 0), [(self.b, 1)])                  
        self.sale(datetime(2026, 10, 8, 9, 0), [(self.a, 1)])                    
        voided = self.sale(datetime(2026, 10, 8, 15, 0), [(self.a, 1)])
        till_service.void_invoice(self.conn, ADMIN, voided.invoice_no, "Customer left")
        returns_service.process_return(self.conn, ADMIN, first.invoice_no, self.a, 1)  

    def test_daily_revenue_fills_gaps_and_nets_out_refunds_and_voids(self):
        series = dashboard_service.daily_revenue(self.conn, ADMIN, 5, today=date(2026, 10, 8))
        self.assertEqual(series, [("2026-10-04", 0), ("2026-10-05", 0), ("2026-10-06", 1000),
                                  ("2026-10-07", 2000), ("2026-10-08", 1000)])
        self.assertEqual(dashboard_service.daily_revenue(self.conn, ADMIN, 1, today=date(2026, 10, 8)),
                         [("2026-10-08", 1000)])

    def test_top_sellers(self):
        today = date(2026, 10, 8)
        self.assertEqual(dashboard_service.top_sellers(self.conn, ADMIN, 5, today=today),
                         [("A", 2, 2000), ("B", 1, 2000)])          
        self.assertEqual(dashboard_service.top_sellers(self.conn, ADMIN, 5, limit=1, today=today), [("A", 2, 2000)])
        self.assertEqual(dashboard_service.top_sellers(self.conn, ADMIN, 1, today=today), [("A", 1, 1000)])

    def test_validation_and_permissions(self):
        for days in (0, 400, "7"):
            with self.assertRaises(ValidationError):
                dashboard_service.daily_revenue(self.conn, ADMIN, days)
        with self.assertRaises(PermissionDenied):
            dashboard_service.daily_revenue(self.conn, CASHIER)
        with self.assertRaises(PermissionDenied):
            dashboard_service.top_sellers(self.conn, CASHIER)


if __name__ == "__main__":
    unittest.main()

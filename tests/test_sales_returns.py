import unittest

from pos_erp.errors import InsufficientStockError, NotFoundError, PermissionDenied, ValidationError
from pos_erp.services import inventory_service as inv
from pos_erp.services import reporting_service as rpt
from pos_erp.services import returns_service as ret
from pos_erp.services import sales_service as sales
from pos_erp.services import stock_service
from pos_erp.services.receipt import render_receipt
from tests.helpers import ADMIN, CASHIER, DbTestCase, add_product


class QuoteTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "A", price="10.00", cost="6.00", stock=10)
        self.b = add_product(self.conn, "B", price="5.50", cost="2.00", stock=10)

    def test_totals_discount_allocation_and_tax(self):
        q = sales.quote(self.conn, [(self.a, 2), (self.b, 1)], discount="5.00", tax_rate="10")
        self.assertEqual(q.subtotal_cents, 2550)
        self.assertEqual(sum(l.discount_cents for l in q.lines), 500)         # allocation sums exactly
        self.assertEqual(q.tax_cents, sum(l.tax_cents for l in q.lines))
        self.assertEqual(q.total_cents, sum(l.total_cents for l in q.lines))
        self.assertEqual(q.total_cents, 2550 - 500 + q.tax_cents)
        self.assertEqual(q.tax_cents, 205)       # 10% of 20.50 (per-line rounding: 1.63 + 0.42)

    def test_same_product_twice_is_merged(self):
        q = sales.quote(self.conn, [(self.a, 1), (self.a, 2)])
        self.assertEqual(len(q.lines), 1)
        self.assertEqual(q.lines[0].quantity, 3)

    def test_invalid_inputs(self):
        for kwargs in ({"discount": "-1"}, {"discount": "999"}, {"discount": "abc"}, {"tax_rate": "-5"},
                       {"tax_rate": "150"}):
            with self.assertRaises(ValidationError, msg=str(kwargs)):
                sales.quote(self.conn, [(self.a, 1)], **kwargs)
        for items in ([], [(self.a, 0)], [(self.a, "x")]):
            with self.assertRaises(ValidationError):
                sales.quote(self.conn, items)
        with self.assertRaises(NotFoundError):
            sales.quote(self.conn, [(9999, 1)])


class CheckoutTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "A", price="10.00", cost="6.00", stock=10)
        self.b = add_product(self.conn, "B", price="5.50", cost="2.00", stock=3)

    def test_checkout_is_consistent_everywhere(self):
        inv_ = sales.checkout(self.conn, CASHIER, [(self.a, 2), (self.b, 1)], discount="2.00", tax_rate="5",
                              payment_method="Card", customer_name="Sam", customer_phone="0170")
        self.assertEqual(self.stock(self.a), 8)
        self.assertEqual(self.stock(self.b), 2)
        self.assertEqual(sum(i.line_total_cents for i in inv_.items), inv_.total_cents)
        self.assertEqual(self.scalar("SELECT SUM(total_cents) FROM invoices"), inv_.total_cents)
        self.assertEqual(inv_.cashier, "cashier")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM stock_movements WHERE reason = 'SALE'"), 2)
        self.assertEqual(stock_service.reconcile(self.conn, ADMIN), [])
        self.assertEqual(self.scalar("SELECT total_spent_cents FROM customers WHERE phone = '0170'"), inv_.total_cents)

    def test_failure_rolls_back_everything(self):
        with self.assertRaises(InsufficientStockError):
            sales.checkout(self.conn, ADMIN, [(self.a, 2), (self.b, 99)], customer_phone="0171")
        self.assertEqual(self.stock(self.a), 10)                                  # first line NOT sold
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM invoices"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM customers"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM stock_movements WHERE reason = 'SALE'"), 0)

    def test_repeated_adds_cannot_oversell(self):
        with self.assertRaises(InsufficientStockError):
            sales.checkout(self.conn, ADMIN, [(self.b, 2), (self.b, 2)])         # 4 > 3 in stock

    def test_invoice_numbers_unique_and_sequential(self):
        nos = [sales.checkout(self.conn, ADMIN, [(self.a, 1)]).invoice_no for _ in range(3)]
        self.assertEqual(len(set(nos)), 3)
        self.assertTrue(nos[0].endswith("-0001") and nos[2].endswith("-0003"))

    def test_history_is_immutable_when_product_changes(self):
        invoice = sales.checkout(self.conn, ADMIN, [(self.a, 1)])
        inv.update_product(self.conn, ADMIN, self.a, inv.ProductForm(name="Renamed", price="99.00",
                           buying_price="50.00", stock="9", min_alert="2"), stock_note="n/a")
        inv.deactivate_product(self.conn, ADMIN, self.a)
        again = sales.get_invoice(self.conn, invoice.invoice_no)
        self.assertEqual((again.items[0].product_name, again.items[0].unit_price_cents,
                          again.items[0].unit_cost_cents), ("A", 1000, 600))
        self.assertEqual(again.total_cents, 1000)

    def test_existing_customer_name_is_not_overwritten(self):
        sales.checkout(self.conn, ADMIN, [(self.a, 1)], customer_name="Sam", customer_phone="0170")
        sales.checkout(self.conn, ADMIN, [(self.a, 1)], customer_name="Intruder", customer_phone="0170")
        self.assertEqual(self.scalar("SELECT name FROM customers WHERE phone = '0170'"), "Sam")

    def test_validation_and_permissions(self):
        with self.assertRaises(ValidationError):
            sales.checkout(self.conn, ADMIN, [(self.a, 1)], payment_method="Bitcoin")
        with self.assertRaises(PermissionDenied):
            sales.checkout(self.conn, type(ADMIN)(9, "x", "Nobody"), [(self.a, 1)])

    def test_receipt_contains_key_lines(self):
        invoice = sales.checkout(self.conn, ADMIN, [(self.a, 2)], discount="1.00", tax_rate="10")
        text = render_receipt(invoice)       # 20.00 - 1.00 discount + 1.90 tax = 20.90
        for needle in (invoice.invoice_no, "TOTAL PAID : $20.90", "Discount   : -$1.00", "A "):
            self.assertIn(needle, text)


class ReturnTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.a = add_product(self.conn, "A", price="10.00", cost="6.00", stock=10)
        self.invoice = sales.checkout(self.conn, ADMIN, [(self.a, 3)], discount="1.00", tax_rate="10",
                                      customer_name="Sam", customer_phone="0170")      # 30.00-1.00 = 29.00 + 10% tax

    def test_partial_refunds_add_up_to_exactly_the_line_total(self):
        line_total = self.invoice.items[0].line_total_cents
        refunds = [ret.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.a, 1).refund_cents
                   for _ in range(3)]
        self.assertEqual(sum(refunds), line_total)
        self.assertEqual(self.stock(self.a), 10)
        self.assertEqual(stock_service.reconcile(self.conn, ADMIN), [])
        self.assertEqual(self.scalar("SELECT total_spent_cents FROM customers WHERE phone = '0170'"), 0)

    def test_cannot_return_more_than_sold_or_repeat_refunds(self):
        ret.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.a, 2)
        with self.assertRaises(ValidationError) as ctx:
            ret.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.a, 2)
        self.assertIn("Only 1", str(ctx.exception))
        self.assertEqual(self.stock(self.a), 9)           # the rejected return changed nothing
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sales_returns"), 1)

    def test_not_restocked_leaves_stock_alone(self):
        result = ret.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.a, 1, "Damaged", restock=False)
        self.assertFalse(result.restocked)
        self.assertEqual(self.stock(self.a), 7)

    def test_unknown_invoice_or_product_and_permissions(self):
        with self.assertRaises(NotFoundError):
            ret.process_return(self.conn, ADMIN, "INV-NOPE", self.a, 1)
        with self.assertRaises(NotFoundError):
            ret.process_return(self.conn, ADMIN, self.invoice.invoice_no, 9999, 1)
        with self.assertRaises(ValidationError):
            ret.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.a, 0)
        with self.assertRaises(PermissionDenied):
            ret.process_return(self.conn, CASHIER, self.invoice.invoice_no, self.a, 1)

    def test_invoice_lines_helper(self):
        ret.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.a, 1)
        line = ret.invoice_lines(self.conn, ADMIN, self.invoice.invoice_no)[0]
        self.assertEqual((line["sold"], line["returned"]), (3, 1))
        with self.assertRaises(NotFoundError):
            ret.invoice_lines(self.conn, ADMIN, "INV-NOPE")

    def test_reports_net_out_refunds_and_use_cost_snapshot(self):
        before = rpt.sales_summary(self.conn, ADMIN)
        # net 29.00 - cost 18.00 = 11.00 profit (tax excluded)
        self.assertEqual(before.est_gross_profit_cents, 1100)
        ret.process_return(self.conn, ADMIN, self.invoice.invoice_no, self.a, 3)
        after = rpt.sales_summary(self.conn, ADMIN)
        self.assertEqual(after.net_revenue_cents, 0)
        self.assertEqual(after.est_gross_profit_cents, 0)
        self.assertEqual(rpt.sales_by_category(self.conn, ADMIN), [])
        self.assertEqual(rpt.dashboard_stats(self.conn).net_revenue_cents, 0)


class DashboardTests(DbTestCase):
    def test_cost_vs_retail_value_and_alerts(self):
        add_product(self.conn, "A", price="15.00", cost="8.00", stock=25, min_alert=5)
        add_product(self.conn, "Tea", price="5.50", cost="3.00", stock=4, min_alert=5, expiry="2000-01")
        stats = rpt.dashboard_stats(self.conn)
        self.assertEqual(stats.total_items, 2)
        self.assertEqual(stats.cost_value_cents, 25 * 800 + 4 * 300)
        self.assertEqual(stats.retail_value_cents, 25 * 1500 + 4 * 550)
        self.assertEqual((stats.low_stock, stats.expiry_alerts), (1, 1))
        self.assertEqual([r["name"] for r in inv.low_stock(self.conn)], ["Tea"])


if __name__ == "__main__":
    unittest.main()
import unittest

from pos_erp.errors import ValidationError
from pos_erp.services import (business_service, credit_service, print_engine, returns_service, shift_service,
                              till_service)
from tests.helpers import ADMIN, CASHIER, DbTestCase, add_product

CREDIT = credit_service.CREDIT_METHOD


class CreditSaleTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.pid = add_product(self.conn, "Phone", price="20.00", cost="12.00", stock=50, barcode="1")

    def sell(self, qty=2, tendered=None, phone="0170", **kw):
        return till_service.checkout_with_payment(
            self.conn, CASHIER, [(self.pid, qty)], customer_name="Sam", customer_phone=phone,
            payment_method=CREDIT, tendered=tendered, **kw)

    def ledger(self, phone="0170"):
        return [(l.entry_type, l.amount_cents, l.balance_cents) for l in credit_service.ledger(self.conn, CASHIER, phone)]

    def test_full_credit_sale(self):
        invoice = self.sell()
        self.assertEqual((invoice.payment_method, invoice.total_cents), (CREDIT, 4000))
        meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
        self.assertEqual((meta["tendered_cents"], meta["change_cents"]), (0, 0))
        self.assertEqual(self.ledger(), [("CHARGE", 4000, 4000)])
        self.assertEqual(self.stock(self.pid), 48)
        self.assertEqual(credit_service.get_account(self.conn, CASHIER, "0170").balance_cents, 4000)

    def test_partial_payment_at_the_sale(self):
        invoice = self.sell(tendered="15")
        self.assertEqual(self.ledger(), [("CHARGE", 4000, 4000), ("PAYMENT", -1500, 2500)])
        meta = till_service.invoice_meta(self.conn, invoice.invoice_no)
        self.assertEqual((meta["tendered_cents"], meta["change_cents"]), (1500, 0))

    def test_credit_needs_a_phone_number_and_leaves_nothing_behind(self):
        with self.assertRaises(ValidationError):
            self.sell(phone="")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM invoices"), 0)
        self.assertEqual(self.stock(self.pid), 50)

    def test_paying_more_than_the_bill_rolls_everything_back(self):
        with self.assertRaises(ValidationError):
            self.sell(qty=1, tendered="25")
        with self.assertRaises(ValidationError):
            self.sell(qty=1, tendered="abc")
        self.assertEqual((self.scalar("SELECT COUNT(*) FROM invoices"), self.scalar("SELECT COUNT(*) FROM customers"),
                          self.stock(self.pid)), (0, 0, 50))

    def test_credit_limit_blocks_the_sale_and_rolls_back(self):
        self.sell(qty=2)
        credit_service.set_credit_limit(self.conn, ADMIN, "0170", "70")
        self.sell(qty=1)
        with self.assertRaises(ValidationError):
            self.sell(qty=1)
        self.assertEqual(credit_service.balance_of_phone(self.conn, "0170"), 6000)
        self.assertEqual((self.stock(self.pid), self.scalar("SELECT COUNT(*) FROM invoices")), (47, 2))

    def test_cash_sales_do_not_touch_the_ledger(self):
        invoice = till_service.checkout_with_payment(self.conn, CASHIER, [(self.pid, 1)], customer_phone="0171",
                                                     tendered="50")
        self.assertEqual(invoice.payment_method, "Cash")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM customer_ledger"), 0)
        result = returns_service.process_return(self.conn, ADMIN, invoice.invoice_no, self.pid, 1, "x")
        self.assertFalse(result.to_account)

    def test_returning_a_credit_sale_reduces_the_debt(self):
        invoice = self.sell()
        result = returns_service.process_return(self.conn, ADMIN, invoice.invoice_no, self.pid, 1, "changed mind")
        self.assertEqual((result.refund_cents, result.to_account), (2000, True))
        self.assertEqual(self.ledger(), [("CHARGE", 4000, 4000), ("CREDIT_NOTE", -2000, 2000)])
        self.assertEqual(self.stock(self.pid), 49)

    def test_voiding_a_credit_sale(self):
        unpaid = self.sell(phone="0180")
        till_service.void_invoice(self.conn, ADMIN, unpaid.invoice_no, "wrong customer")
        self.assertEqual(credit_service.balance_of_phone(self.conn, "0180"), 0)
        part_paid = self.sell(tendered="15")
        till_service.void_invoice(self.conn, ADMIN, part_paid.invoice_no, "mistake")
        self.assertEqual(self.ledger(), [("CHARGE", 4000, 4000), ("PAYMENT", -1500, 2500), ("CREDIT_NOTE", -4000, -1500)])
        self.assertEqual(credit_service.list_debtors(self.conn, ADMIN), [])
        self.assertEqual(self.stock(self.pid), 50)

    def test_receipts_show_the_account_and_follow_the_paper_size(self):
        invoice = self.sell(tendered="15")
        invoice, meta = till_service.get_invoice_with_payment(self.conn, CASHIER, invoice.invoice_no)
        doc = print_engine.prepare_invoice(self.conn, invoice, meta)
        for expected in ("Paid now   : $15.00", "On credit  : $25.00", "Account due: $25.00"):
            self.assertIn(expected, doc.text)
        self.assertEqual(doc.paper.name, "80mm")
        business_service.update_profile(self.conn, ADMIN, {"receipt_paper": "58mm", "shop_address": "1 Market Road",
                                                           "shop_phone": "0170 000"})
        doc = print_engine.prepare_invoice(self.conn, invoice, meta)
        self.assertEqual(doc.paper.chars, 32)
        self.assertTrue(all(len(line) <= 32 for line in doc.text.splitlines()))
        self.assertIn("0170 000", doc.text)
        cash = till_service.checkout_with_payment(self.conn, CASHIER, [(self.pid, 1)], tendered="50")
        cash_invoice, cash_meta = till_service.get_invoice_with_payment(self.conn, CASHIER, cash.invoice_no)
        text = print_engine.prepare_invoice(self.conn, cash_invoice, cash_meta).text
        self.assertIn("Received   : $50.00", text)
        self.assertIn("Change     : $30.00", text)

    def test_the_shift_summary_counts_credit_sales_and_cash_paid_at_the_sale(self):
        shift_service.open_shift(self.conn, CASHIER, "100")
        first = self.sell(tendered="15")
        _shift, summary = shift_service.live_summary(self.conn, CASHIER)
        self.assertEqual((summary.credit_sales_cents, summary.due_cash_cents, summary.cash_sales_cents),
                         (4000, 1500, 0))
        self.assertEqual(summary.expected_cash_cents, 10000 + 1500)
        till_service.void_invoice(self.conn, ADMIN, first.invoice_no, "mistake")
        second = self.sell(phone="0190")
        returns_service.process_return(self.conn, ADMIN, second.invoice_no, self.pid, 1, "x")
        _shift, summary = shift_service.live_summary(self.conn, CASHIER)
        self.assertEqual((summary.credit_sales_cents, summary.cash_refunds_cents), (4000, 0))
        self.assertEqual(summary.due_cash_cents, 1500)


if __name__ == "__main__":
    unittest.main()

"""Plain-text receipt rendering (no GUI dependency, easy to test)."""
from __future__ import annotations

from ..money import fmt
from .sales_service import Invoice


def render_receipt(invoice: Invoice) -> str:
    rows = "".join(f"{i.product_name[:18]:<18} x{i.quantity:<3} {fmt(i.line_subtotal_cents)}\n" for i in invoice.items)
    rule = "-" * 41
    heavy = "=" * 41
    return f"""
{heavy}
          ENTERPRISE POS RECEIPT
{heavy}
Invoice No : {invoice.invoice_no}
Date       : {invoice.created_at}
Cashier    : {invoice.cashier}

Customer   : {invoice.customer_name}
Phone      : {invoice.customer_phone}
{rule}
ITEM                 QTY  PRICE
{rule}
{rows}{rule}
Subtotal   : {fmt(invoice.subtotal_cents)}
Discount   : -{fmt(invoice.discount_cents)}
Tax ({invoice.tax_rate:f}%): +{fmt(invoice.tax_cents)}
{rule}
TOTAL PAID : {fmt(invoice.total_cents)}
Payment    : {invoice.payment_method}
{heavy}
        Thank you for shopping with us!
{heavy}
"""
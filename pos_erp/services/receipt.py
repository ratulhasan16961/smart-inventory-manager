"""Plain-text receipt rendering (no GUI dependency, easy to test)."""
from __future__ import annotations

from ..money import fmt
from .sales_service import Invoice

DEFAULT_SHOP_NAME = "Enterprise POS"
DEFAULT_FOOTER = "Thank you for shopping with us!"
WIDTH = 41


def render_receipt(invoice: Invoice, shop_name: str = DEFAULT_SHOP_NAME, footer: str = DEFAULT_FOOTER) -> str:
    rows = "".join(f"{i.product_name[:18]:<18} x{i.quantity:<3} {fmt(i.line_subtotal_cents)}\n" for i in invoice.items)
    rule = "-" * WIDTH
    heavy = "=" * WIDTH
    title = f"{shop_name.strip().upper()} RECEIPT".center(WIDTH).rstrip()
    closing = footer.strip().center(WIDTH).rstrip()
    return f"""
{heavy}
{title}
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
{closing}
{heavy}
"""
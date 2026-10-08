"""Plain-text receipt rendering (no GUI dependency, easy to test)."""
from __future__ import annotations

from .. import config
from ..money import fmt
from .sales_service import Invoice

DEFAULT_SHOP_NAME = "Enterprise POS"
DEFAULT_FOOTER = "Thank you for shopping with us!"
WIDTH = 41


def render_receipt(invoice: Invoice, shop_name: str = DEFAULT_SHOP_NAME, footer: str = DEFAULT_FOOTER,
                   tendered_cents: int | None = None, change_cents: int | None = None,
                   voided: bool = False) -> str:
    rows = "".join(f"{i.product_name[:18]:<18} x{i.quantity:<3} {fmt(i.line_subtotal_cents)}\n" for i in invoice.items)
    rule = "-" * WIDTH
    heavy = "=" * WIDTH
    title = f"{shop_name.strip().upper()} RECEIPT".center(WIDTH).rstrip()
    closing = footer.strip().center(WIDTH).rstrip()
    banner = "*** VOIDED ***".center(WIDTH).rstrip() + "\n" if voided else ""
    payment = f"Payment    : {invoice.payment_method}\n"
    if tendered_cents is not None and invoice.payment_method == config.PAYMENT_METHODS[0]:
        payment += f"Received   : {fmt(tendered_cents)}\nChange     : {fmt(change_cents or 0)}\n"
    return f"""
{heavy}
{title}
{heavy}
{banner}Invoice No : {invoice.invoice_no}
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
{payment}{heavy}
{closing}
{heavy}
"""

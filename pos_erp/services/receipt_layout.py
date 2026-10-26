"""Width-aware receipt layout for 58 mm, 80 mm and A4. Takes plain values (no database, no GUI)."""
from __future__ import annotations

from dataclasses import dataclass

from ..money import fmt
from . import thermal


@dataclass(frozen=True)
class ReceiptData:
    shop_name: str
    invoice_no: str
    date: str
    cashier: str
    customer_name: str = "Walk-in Customer"
    customer_phone: str = "N/A"
    lines: tuple = ()
    subtotal_cents: int = 0
    discount_cents: int = 0
    points_redeemed: int = 0
    points_value_cents: int = 0
    tax_rate: str = "0"
    tax_cents: int = 0
    total_cents: int = 0
    payment_method: str = "Cash"
    payment_ref: str = ""
    cash_method: str = "Cash"
    tendered_cents: int | None = None
    change_cents: int | None = None
    paid_now_cents: int | None = None
    account_balance_cents: int | None = None
    voided: bool = False
    address: str = ""
    phone: str = ""
    footer: str = ""


def render(data: ReceiptData, width: int = 41) -> list[str]:
    w = width
    heavy, rule = "=" * w, "-" * w
    credit = data.paid_now_cents is not None

    def kv(label: str, value: str) -> str:
        return f"{label:<11}: {value}"

    lines = ["", heavy]
    lines += [thermal.center(part, w) for part in thermal.wrap(f"{data.shop_name.strip().upper()} RECEIPT", w)]
    lines += [thermal.center(part, w) for part in thermal.wrap(data.address, w)]
    if data.phone.strip():
        lines.append(thermal.center(f"Tel: {data.phone.strip()}", w))
    lines.append(heavy)
    if data.voided:
        lines.append(thermal.center("*** VOIDED ***", w))
    lines += [kv("Invoice No", data.invoice_no), kv("Date", data.date), kv("Cashier", data.cashier), "",
              kv("Customer", data.customer_name), kv("Phone", data.customer_phone), rule,
              thermal.two_columns("ITEM", "QTY  PRICE", w), rule]
    for name, quantity, cents in data.lines:
        lines.append(thermal.two_columns(name, f"x{quantity} {fmt(cents)}", w))
    lines += [rule, kv("Subtotal", fmt(data.subtotal_cents)), kv("Discount", f"-{fmt(data.discount_cents)}")]
    if data.points_redeemed:
        lines.append(f"  (incl. {data.points_redeemed} loyalty point(s): -{fmt(data.points_value_cents)})")
    if data.tax_cents:
        lines.append(f"Tax ({data.tax_rate}%): +{fmt(data.tax_cents)}")
    lines += [rule, kv("TOTAL" if credit else "TOTAL PAID", fmt(data.total_cents)), kv("Payment", data.payment_method)]
    if data.payment_ref:
        lines.append(kv("Ref", data.payment_ref))
    if data.payment_method == data.cash_method and data.tendered_cents is not None:
        lines += [kv("Received", fmt(data.tendered_cents)), kv("Change", fmt(data.change_cents or 0))]
    if credit:
        lines += [kv("Paid now", fmt(data.paid_now_cents)), kv("On credit", fmt(data.total_cents - data.paid_now_cents))]
        if data.account_balance_cents is not None:
            lines.append(kv("Account due", fmt(data.account_balance_cents)))
    lines.append(heavy)
    lines += [thermal.center(part, w) for part in thermal.wrap(data.footer, w)]
    lines.append(heavy)
    return [line[:w] for line in lines]


def render_text(data: ReceiptData, width: int = 41) -> str:
    return "\n".join(render(data, width)) + "\n"


def sample(shop_name: str, footer: str, address: str, phone: str, cash_method: str = "Cash") -> ReceiptData:
    """A made-up receipt for the 'print test page' button."""
    return ReceiptData(
        shop_name=shop_name, invoice_no="TEST-0001", date="2026-01-01 12:00:00", cashier="admin",
        customer_name="Test Customer", lines=(("Sample item", 2, 1000), ("Another sample item with a long name", 1, 1250)),
        subtotal_cents=2250, total_cents=2250, payment_method=cash_method, cash_method=cash_method,
        tendered_cents=5000, change_cents=2750, address=address, phone=phone, footer=footer)

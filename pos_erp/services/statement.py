"""A customer account statement as text (for the screen, or an A4 PDF)."""
from __future__ import annotations

from ..money import fmt
from . import thermal

_LABELS = {"CHARGE": "Sale on credit", "PAYMENT": "Payment", "CREDIT_NOTE": "Return / void credit",
           "ADJUSTMENT": "Adjustment"}


def render(shop_name: str, address: str, phone: str, customer_name: str, customer_phone: str, today: str,
           entries, balance_cents: int, width: int = 80) -> list[str]:
    """`entries` are credit_service.LedgerLine objects, oldest first."""
    w = width
    heavy, rule = "=" * w, "-" * w
    lines = [heavy]
    lines += [thermal.center(part, w) for part in thermal.wrap(f"{shop_name} - ACCOUNT STATEMENT".upper(), w)]
    lines += [thermal.center(part, w) for part in thermal.wrap(address, w)]
    if phone.strip():
        lines.append(thermal.center(f"Tel: {phone.strip()}", w))
    lines += [heavy, f"Customer: {customer_name} ({customer_phone})", f"Date    : {today}", rule,
              f"{'Date':<10}  {'Type':<20} {'Reference':<16} {'Amount':>11} {'Balance':>11}", rule]
    for line in entries:
        reference = line.invoice_no or line.note
        lines.append(f"{line.when[:10]:<10}  {_LABELS.get(line.entry_type, line.entry_type):<20} "
                     f"{reference[:16]:<16} {fmt(line.amount_cents):>11} {fmt(line.balance_cents):>11}")
    if not entries:
        lines.append("No entries.")
    lines += [rule, thermal.two_columns("BALANCE DUE" if balance_cents >= 0 else "CREDIT ON ACCOUNT",
                                        fmt(abs(balance_cents)), w), heavy]
    return [line[:w] for line in lines]

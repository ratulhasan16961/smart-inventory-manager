"""The shift summary as text for the screen, a thermal printer (58/80 mm) or A4."""
from __future__ import annotations

from ..money import fmt
from . import thermal
from .shift_service import ShiftSummary


def render(shop_name: str, shift, summary: ShiftSummary, width: int = 44) -> list[str]:
    w = width
    heavy, rule = "=" * w, "-" * w
    closed = shift["closed_at"] is not None

    def money(label: str, cents: int) -> str:
        return thermal.two_columns(label, fmt(cents), w)

    lines = [heavy]
    lines += [thermal.center(part, w) for part in thermal.wrap(f"{shop_name} - SHIFT SUMMARY".upper(), w)]
    lines += [heavy, f"Shift #{shift['id']} ({'closed' if closed else 'open'})",
              f"Opened: {shift['opened_at']} by {shift['opened_by']}"]
    if closed:
        lines.append(f"Closed: {shift['closed_at']} by {shift['closed_by']}")
    lines += [rule, money("Opening cash", summary.opening_cash_cents),
              money("+ Cash sales", summary.cash_sales_cents),
              money("+ Cash received on accounts", summary.due_cash_cents),
              money("- Cash refunds", summary.cash_refunds_cents),
              money("- Cash expenses", summary.expenses_cash_cents),
              rule, money("EXPECTED CASH", summary.expected_cash_cents)]
    if closed:
        lines += [money("Counted cash", shift["counted_cash_cents"]), money("Difference", shift["variance_cents"])]
    lines += [rule, "Card / digital sales"]
    lines += [money(f"  {method}", cents) for method, cents in sorted(summary.digital_sales.items())] or ["  none"]
    lines += [money("  Digital refunds", summary.digital_refunds_cents)] if summary.digital_refunds_cents else []
    lines += [money(f"  Received on accounts ({method})", cents) for method, cents in sorted(summary.due_digital.items())]
    lines += [rule, money("Sold on credit (not paid)", summary.credit_sales_cents),
              thermal.two_columns("Invoices", str(summary.invoices), w), heavy]
    return [line[:w] for line in lines]

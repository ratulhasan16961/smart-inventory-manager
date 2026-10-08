"""Money helpers. All amounts are stored as integer cents (no float rounding drift)."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import config


def to_cents(value) -> int:
    """Convert a decimal amount ('12.5', 12.5, Decimal) to integer cents (half-up)."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid amount: {value!r}")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"Invalid amount: {value!r}")
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_rate(value) -> Decimal:
    """Parse a percentage such as '7.5' (0-100)."""
    try:
        rate = Decimal(str(value).strip() or "0")
    except InvalidOperation:
        raise ValueError(f"Invalid rate: {value!r}") from None
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise ValueError(f"Invalid rate: {value!r}")
    return rate


def percent_of(cents: int, rate: Decimal) -> int:
    return int((Decimal(cents) * rate / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def allocate(total: int, weights: list[int]) -> list[int]:
    """Split ``total`` cents across ``weights`` proportionally; the parts always sum to total."""
    if total == 0 or not weights:
        return [0] * len(weights)
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("Cannot allocate across zero weights")
    shares = [total * w // weight_sum for w in weights]
    leftover = total - sum(shares)
    order = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % weight_sum), i))
    for i in order[:leftover]:
        shares[i] += 1
    return shares


def plain(cents: int) -> str:
    """12345 -> '123.45' (for CSV and form fields)."""
    return f"{Decimal(cents) / 100:.2f}"


def fmt(cents: int, symbol: str | None = None) -> str:
    """12345 -> '$123.45'."""
    symbol = config.CURRENCY_SYMBOL if symbol is None else symbol
    sign = "-" if cents < 0 else ""
    return f"{sign}{symbol}{Decimal(abs(cents)) / 100:,.2f}"

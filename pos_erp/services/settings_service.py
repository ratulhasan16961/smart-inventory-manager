"""Shop-wide settings stored in the database: shop name, currency, default tax, receipt footer."""
from __future__ import annotations

import sqlite3

from .. import config
from ..db.connection import transaction
from ..errors import ValidationError
from ..money import parse_rate
from ..permissions import Session
from . import audit

DEFAULTS = {
    "shop_name": "Enterprise POS",
    "currency_symbol": "$",
    "default_tax_rate": "0",
    "receipt_footer": "Thank you for shopping with us!",
}
LIMITS = {"shop_name": 30, "currency_symbol": 4, "receipt_footer": 41}  # receipts are 41 characters wide


def get_all(conn: sqlite3.Connection) -> dict[str, str]:
    values = dict(DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in DEFAULTS:
            values[row["key"]] = row["value"]
    return values


def get(conn: sqlite3.Connection, key: str) -> str:
    return get_all(conn)[key]


def validate(values: dict[str, str]) -> dict[str, str]:
    shop = values["shop_name"].strip()
    if not shop or len(shop) > LIMITS["shop_name"]:
        raise ValidationError(f"Shop name is required (max {LIMITS['shop_name']} characters)!")
    symbol = values["currency_symbol"].strip()
    if not symbol or len(symbol) > LIMITS["currency_symbol"]:
        raise ValidationError(f"Currency symbol is required (max {LIMITS['currency_symbol']} characters)!")
    try:
        rate = parse_rate(values["default_tax_rate"])
    except ValueError:
        raise ValidationError("Default tax must be a number between 0 and 100!") from None
    footer = values["receipt_footer"].strip()
    if len(footer) > LIMITS["receipt_footer"]:
        raise ValidationError(f"Receipt footer is too long (max {LIMITS['receipt_footer']} characters)!")
    return {"shop_name": shop, "currency_symbol": symbol,
            "default_tax_rate": format(rate.normalize(), "f"), "receipt_footer": footer}


def apply(conn: sqlite3.Connection) -> None:
    """Publish settings that other modules read at call time (currency symbol)."""
    config.CURRENCY_SYMBOL = get_all(conn)["currency_symbol"]


def update(conn: sqlite3.Connection, session: Session, values: dict[str, str]) -> dict[str, str]:
    """Save any subset of the settings; the rest keep their current value."""
    session.require("settings.manage")
    merged = {**get_all(conn), **{k: str(v) for k, v in values.items() if k in DEFAULTS}}
    clean = validate(merged)
    with transaction(conn):
        for key, value in clean.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        audit.log(conn, session.username, "Updated shop settings")
    apply(conn)
    return clean

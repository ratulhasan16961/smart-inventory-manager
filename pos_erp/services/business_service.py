"""Business profile beyond the basic shop settings: address, phone, receipt paper and thermal printer target."""
from __future__ import annotations

import re
import sqlite3

from ..db.connection import transaction
from ..errors import ValidationError
from ..permissions import Session
from . import audit, thermal

DEFAULTS = {"shop_address": "", "shop_phone": "", "receipt_paper": thermal.DEFAULT_PAPER, "printer_target": ""}
LIMITS = {"shop_address": 120, "shop_phone": 30, "printer_target": 200}
_TCP = re.compile(r"^tcp://[A-Za-z0-9._-]+(:\d{1,5})?$")


def get_profile(conn: sqlite3.Connection) -> dict[str, str]:
    values = dict(DEFAULTS)
    for row in conn.execute("SELECT key, value FROM settings WHERE key IN (?, ?, ?, ?)", tuple(DEFAULTS)):
        values[row["key"]] = row["value"]
    return values


def shop_basics(conn: sqlite3.Connection) -> dict[str, str]:
    """Name and footer written by the existing Shop Settings dialog."""
    values = {"shop_name": "Enterprise POS", "receipt_footer": "Thank you for shopping with us!"}
    for row in conn.execute("SELECT key, value FROM settings WHERE key IN ('shop_name', 'receipt_footer')"):
        values[row["key"]] = row["value"]
    return values


def validate(values: dict) -> dict[str, str]:
    clean = {key: str(values.get(key, DEFAULTS[key])).strip() for key in DEFAULTS}
    for key, limit in LIMITS.items():
        if len(clean[key]) > limit:
            raise ValidationError(f"{key.replace('shop_', '').replace('_', ' ').capitalize()} is too long "
                                  f"(max {limit} characters)!")
    if clean["receipt_paper"] not in thermal.PAPERS:
        raise ValidationError("Choose a paper size from the list.")
    target = clean["printer_target"]
    if target.startswith("tcp://") and not _TCP.match(target):
        raise ValidationError("Network printer must look like tcp://192.168.1.50:9100")
    if target.startswith("lp:") and not target[3:].strip():
        raise ValidationError("Write the printer queue name after lp: (for example lp:POS80).")
    if "\n" in target:
        raise ValidationError("The printer target must be one line.")
    return clean


def update_profile(conn: sqlite3.Connection, session: Session, values: dict) -> dict[str, str]:
    session.require("settings.manage")
    clean = validate(values)
    with transaction(conn):
        for key, value in clean.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        audit.log(conn, session.username, "Updated receipt and printer settings")
    return clean

"""Business profile beyond the basic shop settings: receipt layout, backup copy, reminders, daily summary."""
from __future__ import annotations

import re
import sqlite3

from ..db.connection import transaction
from ..errors import ValidationError
from ..permissions import Session
from . import audit, email_service, thermal

DEFAULT_REMINDER = ("Hello {name}, this is a friendly reminder from {shop}: your account balance due is {amount}. "
                    "Thank you!")
DEFAULTS = {
    "shop_address": "", "shop_phone": "", "receipt_paper": thermal.DEFAULT_PAPER, "printer_target": "",
    "country_code": "", "reminder_template": DEFAULT_REMINDER, "backup_mirror_dir": "", "backup_mirror_keep": "30",
    "digest_email": "", "digest_auto": "No",
}
LIMITS = {"shop_address": 120, "shop_phone": 30, "printer_target": 200, "reminder_template": 300,
          "backup_mirror_dir": 300}
_TCP = re.compile(r"^tcp://[A-Za-z0-9._-]+(:\d{1,5})?$")


def get_profile(conn: sqlite3.Connection) -> dict[str, str]:
    values = dict(DEFAULTS)
    marks = ", ".join("?" for _ in DEFAULTS)
    for row in conn.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", tuple(DEFAULTS)):
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
    if not re.fullmatch(r"\d{0,4}", clean["country_code"]):
        raise ValidationError("Country code must be digits only, like 880.")
    if not clean["reminder_template"]:
        clean["reminder_template"] = DEFAULT_REMINDER
    try:
        clean["reminder_template"].format(name="x", amount="1", shop="y")
    except (KeyError, IndexError, ValueError):
        raise ValidationError("The reminder message can only use {name}, {amount} and {shop}.") from None
    keep = clean["backup_mirror_keep"]
    if not keep.isdigit() or not 1 <= int(keep) <= 365:
        raise ValidationError("Keep between 1 and 365 backup copies.")
    if clean["digest_email"]:
        clean["digest_email"] = email_service.validate_email(clean["digest_email"])
    if clean["digest_auto"] not in ("Yes", "No"):
        raise ValidationError("Automatic summary must be Yes or No.")
    return clean


def update_profile(conn: sqlite3.Connection, session: Session, values: dict) -> dict[str, str]:
    """Only the keys you pass change; every other setting keeps its stored value."""
    session.require("settings.manage")
    clean = validate({**get_profile(conn), **values})
    with transaction(conn):
        for key, value in clean.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        audit.log(conn, session.username, "Updated business, backup and reminder settings")
    return clean

"""WhatsApp payment reminders (a wa.me link: no WhatsApp API and no account needed) and account statements."""
from __future__ import annotations

import re
import sqlite3
import urllib.parse

from .. import timeutil
from ..errors import ValidationError
from ..money import fmt
from ..permissions import Session
from . import business_service, credit_service, settings_service, statement

NEEDS_CODE = ("That phone number starts with 0, so WhatsApp needs the country code. "
              "Set it in Settings > Backup & Reminders (for example 880).")


def whatsapp_number(phone: str, country_code: str = "") -> str:
    """International digits for wa.me: 01712345678 + 880 -> 8801712345678."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    code = re.sub(r"\D", "", country_code or "")
    if raw.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        if not code:
            raise ValidationError(NEEDS_CODE)
        digits = code + digits[1:]
    elif code and not digits.startswith(code):
        digits = code + digits
    if not 8 <= len(digits) <= 15:
        raise ValidationError("That phone number cannot be used for WhatsApp. Check the number and the country code.")
    return digits


def reminder_text(conn: sqlite3.Connection, session: Session, phone: str) -> str:
    account = credit_service.get_account(conn, session, phone)
    if account.balance_cents <= 0:
        raise ValidationError("This customer does not owe anything.")
    template = business_service.get_profile(conn)["reminder_template"]
    shop = settings_service.get_all(conn)["shop_name"]
    return template.format(name=account.name, amount=fmt(account.balance_cents), shop=shop)


def whatsapp_url(conn: sqlite3.Connection, session: Session, phone: str) -> tuple[str, str]:
    """Returns (link, message)."""
    text = reminder_text(conn, session, phone)
    account = credit_service.get_account(conn, session, phone)
    number = whatsapp_number(account.phone, business_service.get_profile(conn)["country_code"])
    return f"https://wa.me/{number}?text={urllib.parse.quote(text)}", text


def statement_lines(conn: sqlite3.Connection, session: Session, phone: str, width: int = 80) -> list[str]:
    account = credit_service.get_account(conn, session, phone)
    entries = credit_service.ledger(conn, session, phone, limit=40)
    profile = business_service.get_profile(conn)
    shop = settings_service.get_all(conn)["shop_name"]
    return statement.render(shop, profile["shop_address"], profile["shop_phone"], account.name, account.phone,
                            timeutil.now().date().isoformat(), entries, account.balance_cents, width)

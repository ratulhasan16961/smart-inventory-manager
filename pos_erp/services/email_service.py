"""Verified account emails and password-reset codes sent by email.

Codes are 6 digits, stored only as a scrypt hash, valid for 10 minutes, usable once, limited to 5 wrong attempts,
and requested at most once a minute (5 per hour). A reset code is only ever sent to an address that a logged-in
user has verified with a code first, so a mistyped address can never receive somebody else's reset code.
Asking for a reset code never reveals whether an address is registered (a failed SMTP send is the one exception).
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from .. import mailer, timeutil
from ..db.connection import transaction
from ..errors import AuthenticationError, ConflictError, ValidationError
from ..mailer import MailError
from ..permissions import Session
from ..security import hash_password, verify_password
from . import audit, auth_service

CODE_DIGITS = 6
CODE_TTL_MINUTES = 10
MAX_CODE_ATTEMPTS = 5
RESEND_SECONDS = 60
MAX_SENDS_PER_HOUR = 5
INVALID_CODE = "That code is not valid or has expired. Request a new one."
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")

Sender = Callable[[str, str, str], None]
_dummy_hash: str | None = None


def validate_email(text: str) -> str:
    address = (text or "").strip().lower()
    if len(address) > 254 or not _EMAIL.match(address):
        raise ValidationError("Enter a valid email address (like name@gmail.com)!")
    return address


def _new_code(conn: sqlite3.Connection, user_id: int, purpose: str, email: str, now: datetime) -> tuple[int, str]:
    code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
    code_id = conn.execute(
        "INSERT INTO email_codes (user_id, purpose, email, code_hash, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        (user_id, purpose, email, hash_password(code), timeutil.stamp(now),
         timeutil.stamp(now + timedelta(minutes=CODE_TTL_MINUTES)))).lastrowid
    return code_id, code


def _too_soon(conn: sqlite3.Connection, user_id: int, purpose: str, now: datetime) -> bool:
    last = conn.execute("SELECT created_at FROM email_codes WHERE user_id = ? AND purpose = ? "
                        "ORDER BY id DESC LIMIT 1", (user_id, purpose)).fetchone()
    if last and now - timeutil.parse(last["created_at"]) < timedelta(seconds=RESEND_SECONDS):
        return True
    hour_ago = timeutil.stamp(now - timedelta(hours=1))
    sent = conn.execute("SELECT COUNT(*) FROM email_codes WHERE user_id = ? AND purpose = ? AND created_at > ?",
                        (user_id, purpose, hour_ago)).fetchone()[0]
    return sent >= MAX_SENDS_PER_HOUR


def _latest(conn: sqlite3.Connection, user_id: int, purpose: str):
    return conn.execute("SELECT * FROM email_codes WHERE user_id = ? AND purpose = ? AND used = 0 "
                        "ORDER BY id DESC LIMIT 1", (user_id, purpose)).fetchone()


def _check_code(conn: sqlite3.Connection, row, code: str, now: datetime) -> bool:
    """True when the code matches. Wrong guesses are counted and kill the code after MAX_CODE_ATTEMPTS."""
    if row is None or row["used"] or now >= timeutil.parse(row["expires_at"]) or row["attempts"] >= MAX_CODE_ATTEMPTS:
        return False
    if verify_password((code or "").strip(), row["code_hash"]):
        return True
    attempts = row["attempts"] + 1
    conn.execute("UPDATE email_codes SET attempts = ?, used = ? WHERE id = ?",
                 (attempts, 1 if attempts >= MAX_CODE_ATTEMPTS else 0, row["id"]))
    return False


def _deliver(conn: sqlite3.Connection, code_id: int, sender: Sender | None, to: str, subject: str, body: str) -> None:
    """Send AFTER the database transaction has committed (no write lock is held while talking to the mail server)."""
    try:
        (sender or mailer.send_mail)(to, subject, body)
    except MailError:
        conn.execute("UPDATE email_codes SET used = 1 WHERE id = ?", (code_id,))   # an unsent code must not work
        raise


def get_email(conn: sqlite3.Connection, session: Session) -> str | None:
    session.require("account.update")
    row = conn.execute("SELECT email FROM users WHERE id = ?", (session.user_id,)).fetchone()
    return row["email"] if row else None


def request_verification(conn: sqlite3.Connection, session: Session, email: str,
                         sender: Sender | None = None, now: datetime | None = None) -> None:
    """Send a code to a NEW address; it becomes the account email only after the code is entered."""
    session.require("account.update")
    address = validate_email(email)
    now = now or timeutil.now()
    with transaction(conn):
        if conn.execute("SELECT 1 FROM users WHERE email = ? COLLATE NOCASE AND id <> ?",
                        (address, session.user_id)).fetchone():
            raise ConflictError("That email address is already used by another account.")
        if _too_soon(conn, session.user_id, "verify", now):
            raise ValidationError("Please wait a minute before asking for another code.")
        code_id, code = _new_code(conn, session.user_id, "verify", address, now)
        audit.log(conn, session.username, f"Requested a verification code for {address}")
    body = (f"Hello {session.username},\n\nYour email verification code is: {code}\n\n"
            f"It works for {CODE_TTL_MINUTES} minutes and can be used once. If you did not ask for it, ignore this email.")
    _deliver(conn, code_id, sender, address, "Your verification code", body)


def confirm_verification(conn: sqlite3.Connection, session: Session, code: str, now: datetime | None = None) -> str:
    """Returns the verified address."""
    session.require("account.update")
    now = now or timeutil.now()
    address = None
    with transaction(conn):
        row = _latest(conn, session.user_id, "verify")
        if _check_code(conn, row, code, now):
            try:
                conn.execute("UPDATE users SET email = ? WHERE id = ?", (row["email"], session.user_id))
            except sqlite3.IntegrityError:
                raise ConflictError("That email address is already used by another account.") from None
            conn.execute("UPDATE email_codes SET used = 1 WHERE id = ?", (row["id"],))
            audit.log(conn, session.username, f"Verified email address {row['email']}")
            address = row["email"]
    if address is None:
        raise ValidationError(INVALID_CODE)
    return address


def remove_email(conn: sqlite3.Connection, session: Session) -> None:
    session.require("account.update")
    with transaction(conn):
        conn.execute("UPDATE users SET email = NULL WHERE id = ?", (session.user_id,))
        conn.execute("UPDATE email_codes SET used = 1 WHERE user_id = ?", (session.user_id,))
        audit.log(conn, session.username, "Removed the account email address")


def request_reset_code(conn: sqlite3.Connection, email: str, sender: Sender | None = None,
                       now: datetime | None = None) -> None:
    """Email a reset code to a verified address. Does nothing (and says nothing) for unknown or disabled addresses."""
    address = validate_email(email)
    now = now or timeutil.now()
    sent = None
    with transaction(conn):
        user = conn.execute("SELECT id, username, is_active FROM users WHERE email = ? COLLATE NOCASE",
                            (address,)).fetchone()
        if user is None or not user["is_active"]:
            audit.log(conn, address, "Password reset code requested for an unregistered or disabled address")
        elif _too_soon(conn, user["id"], "reset", now):
            audit.log(conn, user["username"], "Password reset code request ignored (too frequent)")
        else:
            sent = _new_code(conn, user["id"], "reset", address, now)
            audit.log(conn, user["username"], "Password reset code requested by email")
    if sent is None:
        return
    code_id, code = sent
    body = (f"Hello {user['username']},\n\nYour password reset code is: {code}\n\n"
            f"It works for {CODE_TTL_MINUTES} minutes and can be used once. If you did not ask for it, ignore this "
            "email: your password has not changed.")
    _deliver(conn, code_id, sender, address, "Your password reset code", body)


def reset_password_with_code(conn: sqlite3.Connection, email: str, code: str, new_password: str,
                             now: datetime | None = None) -> None:
    """Set a new password with a valid emailed code. Also clears a login lockout. Every failure looks the same."""
    global _dummy_hash
    address = (email or "").strip().lower()
    now = now or timeutil.now()
    done = False
    with transaction(conn):
        user = conn.execute("SELECT id, username, is_active FROM users WHERE email = ? COLLATE NOCASE",
                            (address,)).fetchone()
        if user is None or not user["is_active"]:
            if _dummy_hash is None:
                _dummy_hash = hash_password("dummy-code")
            verify_password((code or "").strip(), _dummy_hash)
            audit.log(conn, address or "?", "Failed password reset by email (unknown or disabled address)")
        elif _check_code(conn, _latest(conn, user["id"], "reset"), code, now):
            auth_service.validate_password(new_password, user["username"])
            conn.execute("UPDATE email_codes SET used = 1 WHERE user_id = ? AND purpose = 'reset'", (user["id"],))
            conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0, failed_attempts = 0, "
                         "locked_until = NULL WHERE id = ?", (hash_password(new_password), user["id"]))
            audit.log(conn, user["username"], "Password reset with an emailed code")
            done = True
        else:
            audit.log(conn, user["username"], "Failed password reset by email (wrong or expired code)")
    if not done:
        raise AuthenticationError(INVALID_CODE)

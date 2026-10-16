"""Password recovery: one-time recovery keys (stored hashed) and the command-line emergency reset."""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta

from .. import timeutil
from ..db.connection import transaction
from ..errors import AuthenticationError, NotFoundError, ValidationError
from ..permissions import Session
from ..security import hash_password, verify_password
from . import audit, auth_service

KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
KEY_GROUPS = 4
GROUP_LENGTH = 4
MAX_KEY_ATTEMPTS = 5
KEY_LOCKOUT_MINUTES = 15
INVALID = "Username or recovery key is not valid."

_dummy_hash: str | None = None


def generate_key() -> str:
    groups = ("".join(secrets.choice(KEY_ALPHABET) for _ in range(GROUP_LENGTH)) for _ in range(KEY_GROUPS))
    return "-".join(groups)


def normalize_key(text: str) -> str:
    """Case, dashes and spaces do not matter when typing a key."""
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def _issue(conn: sqlite3.Connection, user_id: int) -> str:
    key = generate_key()
    conn.execute("UPDATE users SET recovery_hash = ?, recovery_attempts = 0, recovery_locked_until = NULL "
                 "WHERE id = ?", (hash_password(normalize_key(key)), user_id))
    return key


def has_key(conn: sqlite3.Connection, session: Session) -> bool:
    session.require("account.update")
    row = conn.execute("SELECT recovery_hash FROM users WHERE id = ?", (session.user_id,)).fetchone()
    return bool(row and row["recovery_hash"])


def issue_key_for_session(conn: sqlite3.Connection, session: Session) -> str:
    """Create (or replace) the logged-in user's recovery key. The plain key is returned ONCE; only a hash is stored."""
    session.require("account.update")
    with transaction(conn):
        key = _issue(conn, session.user_id)
        audit.log(conn, session.username, "Generated a new recovery key")
    return key


def reset_password_with_key(conn: sqlite3.Connection, username: str, key: str, new_password: str,
                            now: datetime | None = None) -> str:
    """Forgot-password flow. Returns the NEW recovery key (the used one stops working). Also clears a login lockout.
    Unknown, disabled and key-less users all get the same message, so the screen does not reveal who exists."""
    global _dummy_hash
    now = now or timeutil.now()
    username = (username or "").strip()
    error: AuthenticationError | None = None
    new_key = ""

    with transaction(conn):
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not row["is_active"] or not row["recovery_hash"]:
            if _dummy_hash is None:
                _dummy_hash = hash_password("dummy-recovery")
            verify_password(normalize_key(key), _dummy_hash)
            audit.log(conn, username or "?", "Failed password recovery (unknown user, disabled, or no recovery key)")
            error = AuthenticationError(INVALID)
        elif row["recovery_locked_until"] and now < timeutil.parse(row["recovery_locked_until"]):
            remaining = timeutil.parse(row["recovery_locked_until"]) - now
            minutes = max(1, int(remaining.total_seconds() // 60) + 1)
            error = AuthenticationError(f"Too many wrong recovery keys. Try again in about {minutes} minute(s).")
        elif not verify_password(normalize_key(key), row["recovery_hash"]):
            attempts = row["recovery_attempts"] + 1
            locked_until = None
            message = INVALID
            if attempts >= MAX_KEY_ATTEMPTS:
                locked_until = timeutil.stamp(now + timedelta(minutes=KEY_LOCKOUT_MINUTES))
                attempts = 0
                message = f"Too many wrong recovery keys. Recovery is locked for {KEY_LOCKOUT_MINUTES} minutes."
            conn.execute("UPDATE users SET recovery_attempts = ?, recovery_locked_until = ? WHERE id = ?",
                         (attempts, locked_until, row["id"]))
            audit.log(conn, row["username"],
                      "Failed password recovery (wrong key)" + (" - recovery locked" if locked_until else ""))
            error = AuthenticationError(message)
        else:
            auth_service.validate_password(new_password, row["username"])
            new_key = _issue(conn, row["id"])
            conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0, failed_attempts = 0, "
                         "locked_until = NULL WHERE id = ?", (hash_password(new_password), row["id"]))
            audit.log(conn, row["username"], "Password reset with a recovery key (a new key was issued)")
    if error:
        raise error
    return new_key


def emergency_reset(conn: sqlite3.Connection, username: str, new_password: str) -> str:
    """For scripts/reset_password.py ONLY: set a new password with no proof of the old one.
    Whoever can run it already has full access to the database file. Returns a new recovery key."""
    username = (username or "").strip()
    with transaction(conn):
        row = conn.execute("SELECT id, username, is_active FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise NotFoundError(f"There is no user named '{username}'.")
        if not row["is_active"]:
            raise ValidationError("That account is disabled. Enable it first (Settings > Manage Users) "
                                  "or choose another user.")
        auth_service.validate_password(new_password, row["username"])
        key = _issue(conn, row["id"])
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0, failed_attempts = 0, "
                     "locked_until = NULL WHERE id = ?", (hash_password(new_password), row["id"]))
        audit.log(conn, row["username"], "Password reset from the command line (emergency reset)")
    return key

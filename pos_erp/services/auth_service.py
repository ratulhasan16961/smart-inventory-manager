"""Login, lockout, password policy and credential changes."""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta

from .. import config, timeutil
from ..db.connection import transaction
from ..errors import AuthenticationError, ConflictError, ValidationError
from ..permissions import Session
from ..security import hash_password, needs_rehash, verify_password
from . import audit

_dummy_hash: str | None = None


def ensure_default_users(conn: sqlite3.Connection) -> None:
    """First run only: create admin/cashier with a temporary password that MUST be changed."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    with transaction(conn):
        for username, role in (("admin", config.ROLE_ADMIN), ("cashier", config.ROLE_CASHIER)):
            conn.execute("INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?,?,?,1)",
                         (username, hash_password(config.DEFAULT_PASSWORD), role))


def validate_password(password: str, username: str = "") -> None:
    if len(password) < config.MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be at least {config.MIN_PASSWORD_LENGTH} characters long!")
    if username and password.lower() == username.strip().lower():
        raise ValidationError("Password must not be the same as the username!")


def authenticate(conn: sqlite3.Connection, username: str, password: str, now: datetime | None = None) -> Session:
    global _dummy_hash
    now = now or timeutil.now()
    username = (username or "").strip()
    error: AuthenticationError | None = None
    session: Session | None = None

    with transaction(conn):
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not row["is_active"]:
            if _dummy_hash is None:
                _dummy_hash = hash_password("dummy-password")
            verify_password(password, _dummy_hash)
            audit.log(conn, username or "?", "Failed login (unknown or disabled user)")
            error = AuthenticationError("Invalid username or password.")
        elif row["locked_until"] and now < timeutil.parse(row["locked_until"]):
            minutes = max(1, int((timeutil.parse(row["locked_until"]) - now).total_seconds() // 60) + 1)
            error = AuthenticationError(f"Account temporarily locked. Try again in about {minutes} minute(s).")
        elif not verify_password(password, row["password_hash"]):
            attempts = row["failed_attempts"] + 1
            locked_until = None
            message = "Invalid username or password."
            if attempts >= config.MAX_FAILED_LOGINS:
                locked_until = timeutil.stamp(now + timedelta(minutes=config.LOCKOUT_MINUTES))
                attempts = 0
                message = f"Too many failed attempts. Account locked for {config.LOCKOUT_MINUTES} minutes."
            conn.execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                         (attempts, locked_until, row["id"]))
            audit.log(conn, username, "Failed login" + (" (account locked)" if locked_until else ""))
            error = AuthenticationError(message)
        else:
            new_hash = hash_password(password) if needs_rehash(row["password_hash"]) else row["password_hash"]
            conn.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL, password_hash = ? WHERE id = ?",
                         (new_hash, row["id"]))
            audit.log(conn, row["username"], "User logged in")
            session = Session(row["id"], row["username"], row["role"], bool(row["must_change_password"]))
    if error:
        raise error
    assert session is not None
    return session


def change_password(conn: sqlite3.Connection, session: Session, new_password: str) -> Session:
    session.require("account.update")
    validate_password(new_password, session.username)
    with transaction(conn):
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                     (hash_password(new_password), session.user_id))
        audit.log(conn, session.username, "Changed password")
    return replace(session, must_change_password=False)


def update_credentials(conn: sqlite3.Connection, session: Session, new_username: str, new_password: str) -> Session:
    session.require("account.update")
    new_username = new_username.strip()
    if not new_username:
        raise ValidationError("Username cannot be empty!")
    validate_password(new_password, new_username)
    try:
        with transaction(conn):
            conn.execute("UPDATE users SET username = ?, password_hash = ?, must_change_password = 0 WHERE id = ?",
                         (new_username, hash_password(new_password), session.user_id))
            audit.log(conn, new_username, f"Updated login credentials (was '{session.username}')")
    except sqlite3.IntegrityError:
        raise ConflictError("That username is already taken!") from None
    return replace(session, username=new_username, must_change_password=False)

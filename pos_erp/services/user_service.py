"""User administration and first-run setup. Login itself lives in auth_service."""
from __future__ import annotations

import re
import sqlite3

from .. import config
from ..db.connection import transaction
from ..errors import ConflictError, NotFoundError, ValidationError
from ..permissions import Session
from ..security import hash_password
from . import audit, auth_service

ROLES = (config.ROLE_ADMIN, config.ROLE_CASHIER)
_USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
LAST_ADMIN = "At least one active administrator is required."


def _validate_username(username: str) -> None:
    if not _USERNAME.match(username):
        raise ValidationError("Username must be 3-32 characters: letters, numbers, dot, dash or underscore.")


def needs_setup(conn: sqlite3.Connection) -> bool:
    """True on a brand-new database that has no accounts yet."""
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def create_first_admin(conn: sqlite3.Connection, username: str, password: str) -> Session:
    username = username.strip()
    _validate_username(username)
    auth_service.validate_password(password, username)
    with transaction(conn):
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise ConflictError("Setup is already complete. Please log in.")
        user_id = conn.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?, ?, ?, 0)",
            (username, hash_password(password), config.ROLE_ADMIN)).lastrowid
        audit.log(conn, username, "First-run setup: administrator account created")
    return Session(user_id, username, config.ROLE_ADMIN, False)


def _get(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise NotFoundError("User not found!")
    return row


def _other_active_admins(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'Admin' AND is_active = 1 AND id <> ?",
                        (user_id,)).fetchone()[0]


def list_users(conn: sqlite3.Connection, session: Session) -> list[sqlite3.Row]:
    session.require("users.manage")
    return conn.execute("SELECT id, username, role, is_active, must_change_password, locked_until "
                        "FROM users ORDER BY username COLLATE NOCASE").fetchall()


def create_user(conn: sqlite3.Connection, session: Session, username: str, role: str, temp_password: str) -> int:
    """New accounts get a temporary password the user must replace at first login."""
    session.require("users.manage")
    username = username.strip()
    _validate_username(username)
    if role not in ROLES:
        raise ValidationError("Choose a valid role!")
    auth_service.validate_password(temp_password, username)
    try:
        with transaction(conn):
            user_id = conn.execute(
                "INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?, ?, ?, 1)",
                (username, hash_password(temp_password), role)).lastrowid
            audit.log(conn, session.username, f"Created user {username} ({role})")
    except sqlite3.IntegrityError:
        raise ConflictError("That username is already taken!") from None
    return user_id


def set_active(conn: sqlite3.Connection, session: Session, user_id: int, active: bool) -> None:
    session.require("users.manage")
    with transaction(conn):
        user = _get(conn, user_id)
        if not active:
            if user_id == session.user_id:
                raise ValidationError("You cannot disable your own account.")
            if user["role"] == config.ROLE_ADMIN and user["is_active"] and _other_active_admins(conn, user_id) == 0:
                raise ValidationError(LAST_ADMIN)
        conn.execute("UPDATE users SET is_active = ?, failed_attempts = 0, locked_until = NULL WHERE id = ?",
                     (1 if active else 0, user_id))
        audit.log(conn, session.username, f"{'Enabled' if active else 'Disabled'} user {user['username']}")


def set_role(conn: sqlite3.Connection, session: Session, user_id: int, role: str) -> None:
    session.require("users.manage")
    if role not in ROLES:
        raise ValidationError("Choose a valid role!")
    with transaction(conn):
        user = _get(conn, user_id)
        if user_id == session.user_id:
            raise ValidationError("You cannot change your own role.")
        if (role != config.ROLE_ADMIN and user["role"] == config.ROLE_ADMIN and user["is_active"]
                and _other_active_admins(conn, user_id) == 0):
            raise ValidationError(LAST_ADMIN)
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        audit.log(conn, session.username, f"Changed role of {user['username']} to {role}")


def reset_password(conn: sqlite3.Connection, session: Session, user_id: int, temp_password: str) -> None:
    """Admin sets a temporary password; it also unlocks the account. The user must change it at next login."""
    session.require("users.manage")
    with transaction(conn):
        user = _get(conn, user_id)
        auth_service.validate_password(temp_password, user["username"])
        conn.execute("UPDATE users SET password_hash = ?, must_change_password = 1, failed_attempts = 0, "
                     "locked_until = NULL WHERE id = ?", (hash_password(temp_password), user_id))
        audit.log(conn, session.username, f"Reset password of {user['username']}")

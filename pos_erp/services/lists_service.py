"""Managed lists (categories, warehouses): one place to fix typos; renames update the products too."""
from __future__ import annotations

import sqlite3

from .. import config
from ..db.connection import transaction
from ..errors import ConflictError, NotFoundError, ValidationError
from ..permissions import Session
from . import audit

KINDS = {"categories": "category", "warehouses": "warehouse"}  # list table -> products column
PROTECTED = {"categories": "General"}                         # the default category is used by the code itself
DEFAULTS = {"categories": ("General",), "warehouses": ("Central Warehouse",)}


def _column(kind: str) -> str:
    try:
        return KINDS[kind]
    except KeyError:
        raise ValueError(f"Unknown list: {kind}") from None


def names(conn: sqlite3.Connection, kind: str) -> list[str]:
    _column(kind)
    return [row["name"] for row in conn.execute(f"SELECT name FROM {kind} ORDER BY id")]


def sync_from_products(conn: sqlite3.Connection) -> None:
    """Register values that products already use (for example after a CSV import) and keep the defaults."""
    for kind, column in KINDS.items():
        conn.execute(f"INSERT OR IGNORE INTO {kind} (name) SELECT DISTINCT trim({column}) FROM products "
                     f"WHERE trim({column}) <> ''")
        if not names(conn, kind):
            for name in DEFAULTS[kind]:
                conn.execute(f"INSERT OR IGNORE INTO {kind} (name) VALUES (?)", (name,))
    conn.execute("INSERT OR IGNORE INTO categories (name) VALUES ('General')")


def apply(conn: sqlite3.Connection) -> None:
    """Publish the lists to config (the product form reads them)."""
    sync_from_products(conn)
    config.CATEGORIES = tuple(names(conn, "categories"))
    config.WAREHOUSES = tuple(names(conn, "warehouses"))


def _clean(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 40:
        raise ValidationError("Name is required (max 40 characters)!")
    return name


def _guard(kind: str, name: str) -> None:
    if PROTECTED.get(kind, "").lower() == name.lower():
        raise ValidationError(f"'{name}' is the default and cannot be renamed or removed.")


def add(conn: sqlite3.Connection, session: Session, kind: str, name: str) -> None:
    session.require("lists.manage")
    _column(kind)
    name = _clean(name)
    try:
        with transaction(conn):
            conn.execute(f"INSERT INTO {kind} (name) VALUES (?)", (name,))
            audit.log(conn, session.username, f"Added to {kind}: {name}")
    except sqlite3.IntegrityError:
        raise ConflictError("That name already exists!") from None
    apply(conn)


def rename(conn: sqlite3.Connection, session: Session, kind: str, old: str, new: str) -> int:
    """Rename an entry and every product that uses it. Returns how many products were updated."""
    session.require("lists.manage")
    column = _column(kind)
    new = _clean(new)
    with transaction(conn):
        row = conn.execute(f"SELECT id, name FROM {kind} WHERE name = ? COLLATE NOCASE", (old,)).fetchone()
        if row is None:
            raise NotFoundError("Entry not found!")
        _guard(kind, row["name"])
        try:
            conn.execute(f"UPDATE {kind} SET name = ? WHERE id = ?", (new, row["id"]))
        except sqlite3.IntegrityError:
            raise ConflictError("That name already exists!") from None
        moved = conn.execute(f"UPDATE products SET {column} = ? WHERE {column} = ? COLLATE NOCASE",
                             (new, row["name"])).rowcount
        audit.log(conn, session.username, f"Renamed {row['name']} to {new} in {kind} ({moved} products)")
    apply(conn)
    return moved


def remove(conn: sqlite3.Connection, session: Session, kind: str, name: str) -> None:
    session.require("lists.manage")
    column = _column(kind)
    with transaction(conn):
        row = conn.execute(f"SELECT id, name FROM {kind} WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        if row is None:
            raise NotFoundError("Entry not found!")
        _guard(kind, row["name"])
        used = conn.execute(f"SELECT COUNT(*) FROM products WHERE {column} = ? COLLATE NOCASE",
                            (row["name"],)).fetchone()[0]
        if used:
            raise ValidationError(f"{used} product(s) still use '{row['name']}'. Rename it or change those products first.")
        if conn.execute(f"SELECT COUNT(*) FROM {kind}").fetchone()[0] <= 1:
            raise ValidationError("At least one entry must remain in the list.")
        conn.execute(f"DELETE FROM {kind} WHERE id = ?", (row["id"],))
        audit.log(conn, session.username, f"Removed from {kind}: {row['name']}")
    apply(conn)
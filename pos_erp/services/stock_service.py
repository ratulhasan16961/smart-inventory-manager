"""Stock ledger. ``apply_stock_change`` is the ONLY code that modifies products.stock."""
from __future__ import annotations

import sqlite3

from .. import timeutil
from ..errors import InsufficientStockError, NotFoundError, ValidationError
from ..permissions import Session

REASONS = ("OPENING", "SALE", "RETURN", "PURCHASE", "ADJUSTMENT", "IMPORT")


def apply_stock_change(conn: sqlite3.Connection, product_id: int, delta: int, reason: str, username: str,
                       *, ref_type: str | None = None, ref_id: int | None = None, note: str = "") -> int:
    """Change stock by ``delta`` and record a ledger row. Must be called inside a transaction."""
    if not conn.in_transaction:
        raise RuntimeError("apply_stock_change must run inside a transaction")
    if reason not in REASONS:
        raise ValidationError(f"Unknown stock movement reason: {reason}")
    row = conn.execute("SELECT name, stock FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Product {product_id} not found!")
    new_stock = row["stock"] + delta
    if new_stock < 0:
        raise InsufficientStockError(
            f"Insufficient stock for {row['name']}! Available: {row['stock']}, requested: {-delta}")
    if delta == 0:
        return new_stock
    conn.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, product_id))
    conn.execute(
        """INSERT INTO stock_movements (product_id, qty_change, stock_after, reason, ref_type, ref_id, note,
                                        username, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (product_id, delta, new_stock, reason, ref_type, ref_id, note, username, timeutil.stamp()),
    )
    return new_stock


def list_movements(conn: sqlite3.Connection, session: Session, product_query: str = "",
                   limit: int = 500) -> list[sqlite3.Row]:
    session.require("stock.view")
    sql = """SELECT m.id, m.created_at, p.id AS product_id, p.name AS product_name, m.qty_change, m.stock_after,
                    m.reason, m.note, m.username
             FROM stock_movements m JOIN products p ON p.id = m.product_id"""
    params: list = []
    if product_query.strip():
        like = "%" + product_query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql += " WHERE p.name LIKE ? ESCAPE '\\' OR CAST(p.id AS TEXT) = ?"
        params += [like, product_query.strip()]
    sql += " ORDER BY m.id DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def reconcile(conn: sqlite3.Connection, session: Session) -> list[sqlite3.Row]:
    """Products whose recorded stock differs from the sum of their ledger rows (should be empty)."""
    session.require("stock.view")
    return conn.execute(
        """SELECT p.id, p.name, p.stock AS recorded, COALESCE(SUM(m.qty_change), 0) AS ledger
           FROM products p LEFT JOIN stock_movements m ON m.product_id = p.id
           GROUP BY p.id HAVING p.stock <> COALESCE(SUM(m.qty_change), 0)"""
    ).fetchall()

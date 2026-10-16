"""Suppliers, purchase orders and goods receiving (PO -> receive -> stock + ledger + cost)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import timeutil
from ..db.connection import transaction
from ..errors import NotFoundError, ValidationError
from ..money import fmt, to_cents
from ..permissions import Session
from . import audit, stock_service
from ..money import plain

OPEN_STATUSES = ("Ordered", "Partially Received")



def add_supplier(conn: sqlite3.Connection, session: Session, name: str, phone: str = "", email: str = "",
                 address: str = "") -> int:
    session.require("purchasing.manage")
    if not name.strip():
        raise ValidationError("Supplier Name Required!")
    with transaction(conn):
        supplier_id = conn.execute("INSERT INTO suppliers (name, phone, email, address) VALUES (?,?,?,?)",
                                   (name.strip(), phone.strip(), email.strip(), address.strip())).lastrowid
        audit.log(conn, session.username, f"Added supplier: {name.strip()}")
    return supplier_id


def list_suppliers(conn: sqlite3.Connection, session: Session, include_inactive: bool = False) -> list[sqlite3.Row]:
    session.require("purchasing.manage")
    sql = "SELECT * FROM suppliers" + ("" if include_inactive else " WHERE is_active = 1") + " ORDER BY name"
    return conn.execute(sql).fetchall()


def deactivate_supplier(conn: sqlite3.Connection, session: Session, supplier_id: int) -> None:
    session.require("purchasing.manage")
    with transaction(conn):
        row = conn.execute("SELECT name FROM suppliers WHERE id = ? AND is_active = 1", (supplier_id,)).fetchone()
        if row is None:
            raise NotFoundError("Supplier not found!")
        conn.execute("UPDATE suppliers SET is_active = 0 WHERE id = ?", (supplier_id,))
        audit.log(conn, session.username, f"Deactivated supplier: {row['name']}")



@dataclass(frozen=True)
class POLine:
    product_id: int
    quantity: int
    unit_cost: str


def _next_po_number(conn: sqlite3.Connection, moment) -> str:
    prefix = f"PO-{moment:%Y%m%d}-"
    last = conn.execute("SELECT po_number FROM purchase_orders WHERE po_number LIKE ? ORDER BY po_number DESC LIMIT 1",
                        (prefix + "%",)).fetchone()
    return f"{prefix}{(int(last['po_number'][len(prefix):]) + 1) if last else 1:04d}"


def create_purchase_order(conn: sqlite3.Connection, session: Session, supplier_id: int, lines: list[POLine],
                          notes: str = "") -> int:
    session.require("purchasing.manage")
    if not lines:
        raise ValidationError("Add at least one product to the purchase order!")
    if len({l.product_id for l in lines}) != len(lines):
        raise ValidationError("The same product appears twice in this order!")
    with transaction(conn):
        supplier = conn.execute("SELECT name FROM suppliers WHERE id = ? AND is_active = 1", (supplier_id,)).fetchone()
        if supplier is None:
            raise NotFoundError("Supplier not found!")
        moment = timeutil.now()
        po_number = _next_po_number(conn, moment)
        po_id = conn.execute(
            "INSERT INTO purchase_orders (po_number, supplier_id, notes, created_by, created_at) VALUES (?,?,?,?,?)",
            (po_number, supplier_id, notes.strip(), session.username, timeutil.stamp(moment))).lastrowid
        total = 0
        for line in lines:
            if not isinstance(line.quantity, int) or line.quantity <= 0:
                raise ValidationError("Quantity must be a positive whole number!")
            try:
                cost = to_cents(line.unit_cost)
            except ValueError:
                raise ValidationError("Invalid unit cost!") from None
            if cost < 0:
                raise ValidationError("Unit cost cannot be negative!")
            if conn.execute("SELECT 1 FROM products WHERE id = ? AND is_active = 1", (line.product_id,)).fetchone() is None:
                raise NotFoundError(f"Product {line.product_id} not found!")
            conn.execute("INSERT INTO purchase_order_items (po_id, product_id, quantity_ordered, unit_cost_cents) "
                         "VALUES (?,?,?,?)", (po_id, line.product_id, line.quantity, cost))
            total += cost * line.quantity
        audit.log(conn, session.username,
                  f"Created purchase order {po_number} for {supplier['name']} ({fmt(total)})")
    return po_id


def list_purchase_orders(conn: sqlite3.Connection, session: Session, status: str | None = None) -> list[sqlite3.Row]:
    session.require("purchasing.manage")
    sql = """SELECT po.id, po.po_number, s.name AS supplier, po.status, po.created_at,
                    COALESCE(SUM(i.quantity_ordered * i.unit_cost_cents), 0) AS total_cents,
                    COALESCE(SUM(i.quantity_ordered), 0) AS ordered, COALESCE(SUM(i.quantity_received), 0) AS received
             FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id
             LEFT JOIN purchase_order_items i ON i.po_id = po.id"""
    params: tuple = ()
    if status:
        sql += " WHERE po.status = ?"
        params = (status,)
    return conn.execute(sql + " GROUP BY po.id ORDER BY po.id DESC", params).fetchall()


def get_po_items(conn: sqlite3.Connection, session: Session, po_id: int) -> list[sqlite3.Row]:
    session.require("purchasing.manage")
    rows = conn.execute(
        """SELECT i.id, i.product_id, p.name AS product_name, i.quantity_ordered, i.quantity_received,
                  i.unit_cost_cents FROM purchase_order_items i JOIN products p ON p.id = i.product_id
           WHERE i.po_id = ? ORDER BY i.id""", (po_id,)).fetchall()
    if not rows:
        raise NotFoundError("Purchase order not found!")
    return rows


def receive_purchase_order(conn: sqlite3.Connection, session: Session, po_id: int,
                           receipts: dict[int, int]) -> str:
    """Receive goods. ``receipts`` maps purchase_order_items.id -> quantity received now (partial is fine).
    Increases stock through the ledger and sets the product's buying price to the PO unit cost."""
    session.require("purchasing.manage")
    receipts = {item_id: qty for item_id, qty in receipts.items() if qty}
    if not receipts:
        raise ValidationError("Enter a quantity to receive for at least one line.")
    with transaction(conn):
        po = conn.execute("SELECT po_number, status FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
        if po is None:
            raise NotFoundError("Purchase order not found!")
        if po["status"] not in OPEN_STATUSES:
            raise ValidationError(f"This order is {po['status']} and cannot receive more stock.")
        for item_id, qty in receipts.items():
            item = conn.execute("SELECT * FROM purchase_order_items WHERE id = ? AND po_id = ?",
                                (item_id, po_id)).fetchone()
            if item is None:
                raise NotFoundError("Order line not found!")
            if not isinstance(qty, int) or qty <= 0:
                raise ValidationError("Received quantity must be a positive whole number!")
            outstanding = item["quantity_ordered"] - item["quantity_received"]
            if qty > outstanding:
                raise ValidationError(f"Cannot receive {qty}: only {outstanding} still outstanding on this line.")
            conn.execute("UPDATE purchase_order_items SET quantity_received = quantity_received + ? WHERE id = ?",
                         (qty, item_id))
            stock_service.apply_stock_change(conn, item["product_id"], qty, "PURCHASE", session.username,
                                             ref_type="purchase_order", ref_id=po_id, note=po["po_number"])
            conn.execute("UPDATE products SET cost_cents = ? WHERE id = ?", (item["unit_cost_cents"], item["product_id"]))
        open_lines = conn.execute("SELECT COUNT(*) FROM purchase_order_items WHERE po_id = ? "
                                  "AND quantity_received < quantity_ordered", (po_id,)).fetchone()[0]
        status = "Partially Received" if open_lines else "Received"
        conn.execute("UPDATE purchase_orders SET status = ?, closed_at = ? WHERE id = ?",
                     (status, None if open_lines else timeutil.stamp(), po_id))
        audit.log(conn, session.username, f"Received stock for {po['po_number']} -> {status}")
    return status


def cancel_purchase_order(conn: sqlite3.Connection, session: Session, po_id: int) -> None:
    session.require("purchasing.manage")
    with transaction(conn):
        po = conn.execute("SELECT po_number, status FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()
        if po is None:
            raise NotFoundError("Purchase order not found!")
        received = conn.execute("SELECT COALESCE(SUM(quantity_received),0) FROM purchase_order_items WHERE po_id = ?",
                                (po_id,)).fetchone()[0]
        if po["status"] != "Ordered" or received:
            raise ValidationError("Only orders with nothing received yet can be cancelled.")
        conn.execute("UPDATE purchase_orders SET status = 'Cancelled', closed_at = ? WHERE id = ?",
                     (timeutil.stamp(), po_id))
        audit.log(conn, session.username, f"Cancelled purchase order {po['po_number']}")


@dataclass(frozen=True)
class ReorderSuggestion:
    product_id: int
    name: str
    stock: int
    min_alert: int
    on_order: int
    quantity: int
    unit_cost: str
    supplier_id: int | None
    supplier_name: str


_LAST_SUPPLIER = """(SELECT {column} FROM purchase_order_items i
        JOIN purchase_orders po ON po.id = i.po_id JOIN suppliers s ON s.id = po.supplier_id
        WHERE i.product_id = p.id AND s.is_active = 1 ORDER BY po.id DESC LIMIT 1)"""

_REORDER_SQL = f"""
    SELECT p.id, p.name, p.stock, p.min_alert, p.cost_cents,
           COALESCE((SELECT SUM(i.quantity_ordered - i.quantity_received)
                     FROM purchase_order_items i JOIN purchase_orders po ON po.id = i.po_id
                     WHERE i.product_id = p.id AND po.status IN ('Ordered', 'Partially Received')), 0) AS on_order,
           {_LAST_SUPPLIER.format(column="s.id")} AS supplier_id,
           {_LAST_SUPPLIER.format(column="s.name")} AS supplier_name,
           (SELECT i.unit_cost_cents FROM purchase_order_items i JOIN purchase_orders po ON po.id = i.po_id
            WHERE i.product_id = p.id AND po.status <> 'Cancelled' ORDER BY po.id DESC LIMIT 1) AS last_cost
    FROM products p WHERE p.is_active = 1 AND p.stock <= p.min_alert ORDER BY p.name"""


def reorder_suggestions(conn: sqlite3.Connection, session: Session, multiplier: int = 2) -> list[ReorderSuggestion]:
    """Products at or below their minimum alert, with a suggested order quantity:
    target stock = multiplier x minimum alert, minus current stock, minus what is already on open orders.
    The supplier and unit cost come from the product's most recent order (the product's cost otherwise)."""
    session.require("purchasing.manage")
    result = []
    for r in conn.execute(_REORDER_SQL).fetchall():
        target = max(r["min_alert"] * multiplier, r["min_alert"] + 1)
        quantity = target - r["stock"] - r["on_order"]
        if quantity <= 0:
            continue
        cost = r["last_cost"] if r["last_cost"] is not None else r["cost_cents"]
        result.append(ReorderSuggestion(r["id"], r["name"], r["stock"], r["min_alert"], r["on_order"], quantity,
                                        plain(cost), r["supplier_id"], r["supplier_name"] or ""))
    return result

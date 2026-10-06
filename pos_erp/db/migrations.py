"""Schema creation and the one-time upgrade of the original (pre-versioned) database.

* Empty database          -> create the current schema.
* Legacy database (v0)    -> file backup, then convert in ONE transaction:
                             REAL prices -> integer cents, sales lines -> invoices + items,
                             blank barcodes -> NULL, SHA-256 users -> flagged legacy hashes,
                             opening stock -> stock ledger. All IDs are preserved.
* Current database        -> nothing to do.
"""
from __future__ import annotations

import logging
import sqlite3
from decimal import Decimal

from .. import timeutil
from ..money import allocate, to_cents
from ..security import legacy_sha256
from .connection import transaction
from .schema import SCHEMA_STATEMENTS, SCHEMA_VERSION

log = logging.getLogger(__name__)

LEGACY_TABLES = ("inventory", "users", "suppliers", "purchase_orders",
                 "customers", "sales", "audit_logs", "sales_returns")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def ensure_schema(conn: sqlite3.Connection) -> bool:
    """Bring the database to SCHEMA_VERSION. Returns True if anything was created/migrated."""
    version = current_version(conn)
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"Database schema v{version} is newer than this application (v{SCHEMA_VERSION}).")
    if version == SCHEMA_VERSION:
        return False
    if version == 0 and _table_exists(conn, "inventory"):
        backup = _backup_file(conn)
        log.info("Upgrading legacy database (backup: %s)", backup)
        with transaction(conn):
            _upgrade_legacy(conn)
    else:
        with transaction(conn):
            _create_schema(conn)
    return True


def _create_schema(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _backup_file(conn: sqlite3.Connection) -> str | None:
    """Copy the database file next to itself before touching it."""
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row["file"] if row else ""
    if not path:
        return None
    dest = f"{path}.pre-migration-{timeutil.now():%Y%m%d%H%M%S}.bak"
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
    finally:
        target.close()
    return dest



def _col(row: sqlite3.Row, name: str, default=None):
    return row[name] if name in row.keys() and row[name] is not None else default


def _cents(value) -> int:
    try:
        return max(to_cents(value if value is not None else 0), 0)
    except ValueError:
        return 0


def _int(value) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _upgrade_legacy(conn: sqlite3.Connection) -> None:
    for name in LEGACY_TABLES:
        if _table_exists(conn, name):
            conn.execute(f"ALTER TABLE {name} RENAME TO legacy_{name}")
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)

    _copy_users(conn)
    _copy_products(conn)
    _copy_customers(conn)
    _copy_suppliers(conn)
    _copy_sales(conn)
    _copy_returns(conn)
    _copy_audit(conn)

    for name in LEGACY_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS legacy_{name}")
    conn.execute("INSERT INTO audit_logs (username, action, timestamp) VALUES ('system', ?, ?)",
                 (f"Database migrated to schema v{SCHEMA_VERSION}", timeutil.stamp()))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _copy_users(conn):
    if not _table_exists(conn, "legacy_users"):
        return
    default_hash = legacy_sha256("1234")
    for r in conn.execute("SELECT * FROM legacy_users ORDER BY id").fetchall():
        if not r["username"] or not r["password"]:
            continue
        stored = "sha256$" + r["password"]
        role = r["role"] if r["role"] in ("Admin", "Cashier") else "Cashier"
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, must_change_password) VALUES (?,?,?,?,?)",
            (r["id"], r["username"], stored, role, 1 if stored == default_hash else 0),
        )


def _copy_products(conn):
    now = timeutil.stamp()
    for r in conn.execute("SELECT * FROM legacy_inventory ORDER BY id").fetchall():
        stock = _int(_col(r, "stock", 0))
        conn.execute(
            """INSERT INTO products (id, name, category, cost_cents, price_cents, stock, min_alert,
                                     batch, barcode, warehouse, expiry)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (r["id"], (_col(r, "name", "") or "").strip() or "Unnamed product",
             (_col(r, "category", "") or "").strip() or "General",
             _cents(_col(r, "buying_price")), _cents(_col(r, "price")), 0, _int(_col(r, "min_alert")),
             _col(r, "batch", ""), (_col(r, "barcode", "") or "").strip() or None,
             _col(r, "warehouse", ""), _col(r, "expiry", "")),
        )
        if stock > 0:  
            conn.execute("UPDATE products SET stock = ? WHERE id = ?", (stock, r["id"]))
            conn.execute(
                """INSERT INTO stock_movements (product_id, qty_change, stock_after, reason, ref_type, ref_id,
                                                note, username, created_at)
                   VALUES (?,?,?,'OPENING','product',?,'Opening balance at migration','system',?)""",
                (r["id"], stock, stock, r["id"], now),
            )


def _copy_customers(conn):
    if not _table_exists(conn, "legacy_customers"):
        return
    for r in conn.execute("SELECT * FROM legacy_customers ORDER BY id").fetchall():
        if not r["phone"]:
            continue
        conn.execute(
            "INSERT INTO customers (id, name, phone, loyalty_points, total_spent_cents) VALUES (?,?,?,?,?)",
            (r["id"], _col(r, "name", "") or "Customer", r["phone"],
             _int(_col(r, "loyalty_points", 0)), _cents(_col(r, "total_spent", 0))),
        )


def _copy_suppliers(conn):
    if not _table_exists(conn, "legacy_suppliers"):
        return
    for r in conn.execute("SELECT * FROM legacy_suppliers ORDER BY id").fetchall():
        name = (_col(r, "name", "") or "").strip()
        if name:
            conn.execute("INSERT INTO suppliers (id, name, phone, email, address) VALUES (?,?,?,?,?)",
                         (r["id"], name, _col(r, "phone", ""), _col(r, "email", ""), _col(r, "address", "")))


def _ensure_product(conn, product_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT id, cost_cents FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:  
        conn.execute("INSERT INTO products (id, name, is_active) VALUES (?, ?, 0)",
                     (product_id, f"Deleted product #{product_id}"))
        row = conn.execute("SELECT id, cost_cents FROM products WHERE id = ?", (product_id,)).fetchone()
    return row


def _copy_sales(conn):
    """Old model: one row per cart line. The original app copied the WHOLE invoice
    discount/tax/total onto every line; a later revision stored per-line shares.
    The arithmetic identity below tells the two apart."""
    if not _table_exists(conn, "legacy_sales"):
        return
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute("SELECT * FROM legacy_sales ORDER BY id").fetchall():
        groups.setdefault(r["invoice_no"] or f"LEGACY-{r['id']}", []).append(r)

    for invoice_no, lines in groups.items():
        subs = [_cents(_col(l, "subtotal")) for l in lines]
        discs = [_cents(_col(l, "discount")) for l in lines]
        taxes = [_cents(_col(l, "tax")) for l in lines]
        totals = [_cents(_col(l, "total_price")) for l in lines]
        subtotal = sum(subs)

        repeated = (len(lines) > 1 and len(set(discs)) == 1 and len(set(taxes)) == 1 and len(set(totals)) == 1
                    and abs(totals[0] - (subtotal - discs[0] + taxes[0])) <= 1)
        if repeated:
            disc_total = min(discs[0], subtotal)
            line_disc = allocate(disc_total, subs) if subtotal else [0] * len(lines)
            nets = [s - d for s, d in zip(subs, line_disc)]
            line_tax = allocate(taxes[0], nets) if sum(nets) else [0] * len(lines)
        else:
            line_disc, line_tax = discs, taxes

        merged: dict[int, list[int]] = {} 
        for l, s, d, t in zip(lines, subs, line_disc, line_tax):
            acc = merged.setdefault(l["product_id"], [0, 0, 0, 0])
            acc[0] += max(int(_col(l, "quantity", 0) or 0), 0)
            acc[1] += s
            acc[2] += d
            acc[3] += t
        merged = {pid: v for pid, v in merged.items() if v[0] > 0}
        if not merged:
            continue

        inv_sub = sum(v[1] for v in merged.values())
        inv_disc = sum(v[2] for v in merged.values())
        inv_tax = sum(v[3] for v in merged.values())
        net = inv_sub - inv_disc
        rate = (Decimal(inv_tax) * 100 / Decimal(net)).quantize(Decimal("0.01")) if net > 0 else Decimal(0)
        first = lines[0]
        phone = _col(first, "customer_phone", "N/A")
        customer = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
        cur = conn.execute(
            """INSERT INTO invoices (invoice_no, customer_id, customer_name, customer_phone, subtotal_cents,
                                     discount_cents, tax_rate, tax_cents, total_cents, payment_method, cashier, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (invoice_no, customer["id"] if customer else None, _col(first, "customer_name", "Walk-in Customer"),
             phone, inv_sub, inv_disc, format(rate, "f"), inv_tax, inv_sub - inv_disc + inv_tax,
             _col(first, "payment_method", "Cash"), _col(first, "cashier_name", "unknown"),
             _col(first, "sale_date", "") or timeutil.stamp()),
        )
        for pid, (qty, sub, disc, tax) in merged.items():
            product = _ensure_product(conn, pid)
            name = conn.execute("SELECT name FROM products WHERE id = ?", (pid,)).fetchone()["name"]
            unit_price = int((Decimal(sub) / qty).quantize(Decimal(1)))
            conn.execute(
                """INSERT INTO invoice_items (invoice_id, product_id, product_name, quantity, unit_price_cents,
                                              unit_cost_cents, line_subtotal_cents, line_discount_cents,
                                              line_tax_cents, line_total_cents)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (cur.lastrowid, pid, name, qty, unit_price, product["cost_cents"], sub, disc, tax, sub - disc + tax),
            )


def _copy_returns(conn):
    if not _table_exists(conn, "legacy_sales_returns"):
        return
    for r in conn.execute("SELECT * FROM legacy_sales_returns ORDER BY id").fetchall():
        item = conn.execute(
            """SELECT ii.id, ii.quantity, ii.line_total_cents FROM invoice_items ii
               JOIN invoices i ON i.id = ii.invoice_id WHERE i.invoice_no = ? AND ii.product_id = ?""",
            (r["invoice_no"], r["product_id"]),
        ).fetchone()
        qty = _int(_col(r, "quantity", 0))
        if item is None or qty <= 0:
            log.warning("Skipping legacy return %s (no matching sale line)", r["id"])
            continue
        done = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM sales_returns WHERE invoice_item_id = ?",
                            (item["id"],)).fetchone()[0]
        if done + qty > item["quantity"]:
            log.warning("Skipping legacy return %s (exceeds sold quantity)", r["id"])
            continue
        refund = (_cents(r["refund_amount"]) if _col(r, "refund_amount") is not None
                  else item["line_total_cents"] * qty // item["quantity"])
        conn.execute(
            """INSERT INTO sales_returns (invoice_item_id, quantity, refund_cents, reason, restocked, processed_by, created_at)
               VALUES (?,?,?,?,1,'legacy',?)""",
            (item["id"], qty, refund, _col(r, "reason", ""), _col(r, "return_date", "") or timeutil.stamp()),
        )


def _copy_audit(conn):
    if _table_exists(conn, "legacy_audit_logs"):
        conn.execute(
            """INSERT INTO audit_logs (id, username, action, timestamp)
               SELECT id, COALESCE(username, 'system'), COALESCE(action, ''), COALESCE(timestamp, '')
               FROM legacy_audit_logs ORDER BY id"""
        )
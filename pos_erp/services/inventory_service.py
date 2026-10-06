"""Product catalogue: validation, CRUD, soft delete, stock adjustments, CSV import/export."""
from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .. import config, timeutil
from ..db.connection import transaction
from ..errors import ConflictError, NotFoundError, ValidationError
from ..money import plain, to_cents
from ..permissions import Session
from . import audit, stock_service

CSV_COLUMNS = ("id", "name", "category", "buying_price", "price", "stock", "min_alert",
               "batch", "barcode", "warehouse", "expiry")


# --------------------------------------------------------------------------- expiry / flags
def valid_expiry(text: str) -> bool:
    if not text:
        return True
    try:
        datetime.strptime(text, "%Y-%m")
        return True
    except ValueError:
        return False


def expiry_status(expiry: str | None, today: datetime | None = None) -> str | None:
    """'expired', 'expiring' (<= EXPIRY_WARNING_DAYS left) or None. Expiry is YYYY-MM (valid to month end)."""
    if not expiry:
        return None
    try:
        first = datetime.strptime(expiry.strip(), "%Y-%m")
    except ValueError:
        return None
    end_exclusive = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    today = today or timeutil.now()
    if today >= end_exclusive:
        return "expired"
    if (end_exclusive - today).days <= config.EXPIRY_WARNING_DAYS:
        return "expiring"
    return None


def product_flag(row: sqlite3.Row, today: datetime | None = None) -> str | None:
    """Row highlight: 'expired' > 'low_stock' > 'expiring' > None."""
    status = expiry_status(row["expiry"], today)
    if status == "expired":
        return "expired"
    if row["stock"] <= row["min_alert"]:
        return "low_stock"
    return status


# --------------------------------------------------------------------------- validation
@dataclass
class ProductForm:
    """Raw (string) values as typed by the user."""
    name: str = ""
    category: str = "General"
    buying_price: str = "0"
    price: str = "0"
    stock: str = "0"
    min_alert: str = "0"
    batch: str = ""
    barcode: str = ""
    warehouse: str = ""
    expiry: str = ""


def parse_form(form: ProductForm) -> dict:
    name = form.name.strip()
    if not name:
        raise ValidationError("Product Name is required!")
    try:
        cost = to_cents(form.buying_price or 0)
        price = to_cents(form.price or 0)
        stock = int(str(form.stock).strip() or 0)
        min_alert = int(str(form.min_alert).strip() or 0)
    except ValueError:
        raise ValidationError("Please enter valid numeric values for price and stock!") from None
    if min(cost, price, stock, min_alert) < 0:
        raise ValidationError("Prices, stock and minimum alert cannot be negative!")
    expiry = form.expiry.strip()
    if not valid_expiry(expiry):
        raise ValidationError("Expiry must be in YYYY-MM format (e.g. 2027-03)!")
    return {
        "name": name, "category": form.category.strip() or "General",
        "cost_cents": cost, "price_cents": price, "stock": stock, "min_alert": min_alert,
        "batch": form.batch.strip(), "barcode": form.barcode.strip() or None,  # blank -> NULL (UNIQUE-safe)
        "warehouse": form.warehouse.strip(), "expiry": expiry,
    }


def _integrity_error(exc: sqlite3.IntegrityError) -> ConflictError:
    if "barcode" in str(exc):
        return ConflictError("Duplicate barcode detected!")
    return ConflictError(f"Data conflict: {exc}")


def _get_active(conn: sqlite3.Connection, product_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
    if row is None:
        raise NotFoundError("Product not found!")
    return row


# --------------------------------------------------------------------------- queries
def get_product(conn: sqlite3.Connection, session: Session, product_id: int) -> sqlite3.Row:
    session.require("inventory.view")
    return _get_active(conn, product_id)


def list_products(conn: sqlite3.Connection, session: Session, query: str = "") -> list[sqlite3.Row]:
    session.require("inventory.view")
    sql = "SELECT * FROM products WHERE is_active = 1"
    params: list = []
    if query.strip():
        like = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql += " AND (name LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\' OR barcode LIKE ? ESCAPE '\\')"
        params += [like, like, like]
    return conn.execute(sql + " ORDER BY id", params).fetchall()


def low_stock(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT name, stock, min_alert FROM products WHERE is_active = 1 AND stock <= min_alert "
                        "ORDER BY name").fetchall()


def find_for_sale(conn: sqlite3.Connection, session: Session, key: str) -> sqlite3.Row:
    """Look up by barcode first, then by product ID."""
    session.require("sales.create")
    key = key.strip()
    row = conn.execute(
        """SELECT id, name, price_cents, stock FROM products
           WHERE is_active = 1 AND (barcode = ? OR CAST(id AS TEXT) = ?)
           ORDER BY (barcode = ?) DESC LIMIT 1""", (key, key, key)).fetchone()
    if row is None:
        raise NotFoundError("Product not found!")
    return row


# --------------------------------------------------------------------------- commands
def add_product(conn: sqlite3.Connection, session: Session, form: ProductForm) -> int:
    session.require("inventory.manage")
    data = parse_form(form)
    try:
        with transaction(conn):
            cur = conn.execute(
                """INSERT INTO products (name, category, cost_cents, price_cents, stock, min_alert, batch, barcode,
                                         warehouse, expiry) VALUES (?,?,?,?,0,?,?,?,?,?)""",
                (data["name"], data["category"], data["cost_cents"], data["price_cents"], data["min_alert"],
                 data["batch"], data["barcode"], data["warehouse"], data["expiry"]))
            product_id = cur.lastrowid
            if data["stock"]:
                stock_service.apply_stock_change(conn, product_id, data["stock"], "OPENING", session.username,
                                                 ref_type="product", ref_id=product_id, note="Initial stock")
            audit.log(conn, session.username, f"Added product: {data['name']}")
    except sqlite3.IntegrityError as exc:
        raise _integrity_error(exc) from exc
    return product_id


def _describe_changes(old: sqlite3.Row, new: dict) -> list[str]:
    changes = []
    for label, col in (("name", "name"), ("category", "category"), ("buying price", "cost_cents"),
                       ("price", "price_cents"), ("min alert", "min_alert"), ("batch", "batch"),
                       ("barcode", "barcode"), ("warehouse", "warehouse"), ("expiry", "expiry")):
        if old[col] != new[col]:
            a, b = old[col], new[col]
            if col.endswith("_cents"):
                a, b = plain(a), plain(b)
            changes.append(f"{label} {a or '-'} -> {b or '-'}")
    return changes


def update_product(conn: sqlite3.Connection, session: Session, product_id: int, form: ProductForm,
                   stock_note: str = "") -> None:
    """Edit a product. Changing stock requires a reason and is written to the stock ledger."""
    session.require("inventory.manage")
    data = parse_form(form)
    try:
        with transaction(conn):
            old = _get_active(conn, product_id)
            delta = data["stock"] - old["stock"]
            if delta and not stock_note.strip():
                raise ValidationError("A reason is required when changing stock.")
            conn.execute(
                """UPDATE products SET name=?, category=?, cost_cents=?, price_cents=?, min_alert=?, batch=?,
                          barcode=?, warehouse=?, expiry=? WHERE id=?""",
                (data["name"], data["category"], data["cost_cents"], data["price_cents"], data["min_alert"],
                 data["batch"], data["barcode"], data["warehouse"], data["expiry"], product_id))
            changes = _describe_changes(old, data)
            if delta:
                stock_service.apply_stock_change(conn, product_id, delta, "ADJUSTMENT", session.username,
                                                 ref_type="product", ref_id=product_id, note=stock_note.strip())
                changes.append(f"stock {old['stock']} -> {data['stock']} ({stock_note.strip()})")
            audit.log(conn, session.username,
                      f"Updated product ID {product_id}: " + ("; ".join(changes) or "no field changes"))
    except sqlite3.IntegrityError as exc:
        raise _integrity_error(exc) from exc


def adjust_stock(conn: sqlite3.Connection, session: Session, product_id: int, delta: int, note: str) -> int:
    """Stock-take / damage / correction. A reason is mandatory."""
    session.require("stock.adjust")
    if not isinstance(delta, int) or delta == 0:
        raise ValidationError("Adjustment must be a non-zero whole number!")
    if not note.strip():
        raise ValidationError("A reason is required for stock adjustments.")
    with transaction(conn):
        _get_active(conn, product_id)
        new_stock = stock_service.apply_stock_change(conn, product_id, delta, "ADJUSTMENT", session.username,
                                                     ref_type="product", ref_id=product_id, note=note.strip())
        audit.log(conn, session.username, f"Stock adjustment on product ID {product_id}: {delta:+d} ({note.strip()})")
    return new_stock


def deactivate_product(conn: sqlite3.Connection, session: Session, product_id: int) -> None:
    """Soft delete: history (invoices, ledger, POs) keeps pointing at the product. Barcode is released."""
    session.require("inventory.manage")
    with transaction(conn):
        product = _get_active(conn, product_id)
        conn.execute("UPDATE products SET is_active = 0, barcode = NULL WHERE id = ?", (product_id,))
        audit.log(conn, session.username, f"Deleted (deactivated) product ID {product_id}: {product['name']}")


# --------------------------------------------------------------------------- CSV
def export_csv(conn: sqlite3.Connection, session: Session, path: str | Path) -> int:
    session.require("inventory.export")
    rows = conn.execute("SELECT * FROM products WHERE is_active = 1 ORDER BY id").fetchall()
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow([r["id"], r["name"], r["category"], plain(r["cost_cents"]), plain(r["price_cents"]),
                             r["stock"], r["min_alert"], r["batch"], r["barcode"] or "", r["warehouse"], r["expiry"]])
    audit.log(conn, session.username, f"Exported {len(rows)} products to CSV")
    return len(rows)


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class _DryRun(Exception):
    pass


def import_csv(conn: sqlite3.Connection, session: Session, path: str | Path, dry_run: bool = False) -> ImportReport:
    """All-or-nothing upsert. Products are matched by barcode, else by name; IDs are never changed.
    Stock in the file is the new stock level (difference is written to the ledger as IMPORT)."""
    session.require("inventory.import")
    report = ImportReport()
    parsed: list[tuple[int, dict]] = []
    seen_barcodes: set[str] = set()

    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "name" not in [f.strip().lower() for f in reader.fieldnames]:
            report.errors.append("Missing required column: name")
            return report
        reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]
        for line_no, row in enumerate(reader, start=2):
            try:
                data = parse_form(ProductForm(
                    name=row.get("name") or "", category=row.get("category") or "General",
                    buying_price=row.get("buying_price") or "0", price=row.get("price") or "0",
                    stock=row.get("stock") or "0", min_alert=row.get("min_alert") or "0",
                    batch=row.get("batch") or "", barcode=row.get("barcode") or "",
                    warehouse=row.get("warehouse") or "", expiry=row.get("expiry") or ""))
            except ValidationError as exc:
                report.errors.append(f"Row {line_no}: {exc}")
                continue
            if data["barcode"]:
                if data["barcode"] in seen_barcodes:
                    report.errors.append(f"Row {line_no}: barcode {data['barcode']} appears twice in the file")
                    continue
                seen_barcodes.add(data["barcode"])
            parsed.append((line_no, data))
    if not parsed and not report.errors:
        report.errors.append("The file contains no data rows.")
    if report.errors:
        return report

    try:
        with transaction(conn):
            for line_no, data in parsed:
                existing = None
                if data["barcode"]:
                    existing = conn.execute("SELECT * FROM products WHERE barcode = ?", (data["barcode"],)).fetchone()
                else:
                    existing = conn.execute(
                        "SELECT * FROM products WHERE is_active = 1 AND lower(name) = lower(?)",
                        (data["name"],)).fetchone()
                    if existing:
                        data["barcode"] = existing["barcode"]
                try:
                    if existing:
                        conn.execute(
                            """UPDATE products SET name=?, category=?, cost_cents=?, price_cents=?, min_alert=?,
                                      batch=?, barcode=?, warehouse=?, expiry=? WHERE id=?""",
                            (data["name"], data["category"], data["cost_cents"], data["price_cents"],
                             data["min_alert"], data["batch"], data["barcode"], data["warehouse"], data["expiry"],
                             existing["id"]))
                        product_id, delta = existing["id"], data["stock"] - existing["stock"]
                        report.updated += 1
                    else:
                        cur = conn.execute(
                            """INSERT INTO products (name, category, cost_cents, price_cents, stock, min_alert, batch,
                                                     barcode, warehouse, expiry) VALUES (?,?,?,?,0,?,?,?,?,?)""",
                            (data["name"], data["category"], data["cost_cents"], data["price_cents"],
                             data["min_alert"], data["batch"], data["barcode"], data["warehouse"], data["expiry"]))
                        product_id, delta = cur.lastrowid, data["stock"]
                        report.created += 1
                except sqlite3.IntegrityError as exc:
                    report.errors.append(f"Row {line_no}: {_integrity_error(exc)}")
                    raise
                if delta:
                    stock_service.apply_stock_change(conn, product_id, delta, "IMPORT", session.username,
                                                     ref_type="import", note="CSV import")
            if not dry_run:
                audit.log(conn, session.username,
                          f"Imported products from CSV ({report.created} created, {report.updated} updated)")
            else:
                raise _DryRun
    except _DryRun:
        pass
    except sqlite3.IntegrityError:
        report.created = report.updated = 0
    return report
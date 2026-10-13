"""Build a demo database with realistic sample data. It never touches your real database.

    python3 -m scripts.seed_demo            # creates demo/demo.db
    python3 -m scripts.seed_demo --reset    # delete and rebuild it

Everything goes through the real services (so stock, ledger, invoices and reports stay consistent) with the clock
set back in time, which gives 30 days of history: users, products, suppliers, purchase orders, sales paid in cash
or by card, loyalty points, a few returns and one voided invoice. The output is repeatable (fixed random seed).
"""
from __future__ import annotations

import argparse
import random
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from unittest import mock

from pos_erp import config, timeutil
from pos_erp.bootstrap import open_database
from pos_erp.errors import ValidationError
from pos_erp.money import plain, to_cents
from pos_erp.services import (auth_service, inventory_service, lists_service, purchase_service, returns_service,
                              sales_service, settings_service, till_service, user_service)
from pos_erp.services.inventory_service import ProductForm
from pos_erp.services.purchase_service import POLine

REAL_DB = config.PROJECT_ROOT / "general_inventory.db"
DEFAULT_PATH = config.PROJECT_ROOT / "demo" / "demo.db"
ADMIN_PASSWORD = "demo-admin-pass"
CASHIER_PASSWORD = "demo-cashier-pass"
TAX_RATE = "5"
HISTORY_DAYS = 30

PRODUCTS = (
    ("Wireless Mouse", "Electronics", "8.00", "15.00", 60, 8, "Central Warehouse", None),
    ("Mechanical Keyboard", "Electronics", "35.00", "55.00", 40, 6, "Central Warehouse", None),
    ("USB-C Cable (2m)", "Electronics", "2.50", "6.00", 120, 20, "Central Warehouse", None),
    ("USB-C Charger 20W", "Electronics", "6.00", "14.00", 80, 10, "Central Warehouse", None),
    ("Bluetooth Speaker", "Electronics", "18.00", "35.00", 35, 5, "Central Warehouse", None),
    ("Laptop Stand", "Electronics", "12.00", "25.00", 30, 5, "Branch Warehouse", None),
    ("Webcam 1080p", "Electronics", "22.00", "45.00", 3, 5, "Branch Warehouse", None),      # low stock
    ("Organic Green Tea", "Grocery", "3.00", "5.50", 80, 10, "Branch Warehouse", 14),
    ("Dark Chocolate Bar", "Grocery", "1.20", "2.50", 150, 20, "Branch Warehouse", 9),
    ("Instant Noodles Pack", "Grocery", "0.40", "0.90", 300, 40, "Branch Warehouse", 11),
    ("Basmati Rice 5kg", "Grocery", "7.00", "11.00", 55, 8, "Central Warehouse", 18),
    ("Orange Juice 1L", "Grocery", "1.80", "3.20", 6, 10, "Branch Warehouse", 0),          # expires this month
    ("Yogurt Cup", "Grocery", "0.50", "1.00", 8, 10, "Branch Warehouse", -1),              # already expired
    ("Cotton T-Shirt", "Clothing", "5.00", "12.00", 70, 10, "Central Warehouse", None),
    ("Denim Jeans", "Clothing", "14.00", "32.00", 45, 6, "Central Warehouse", None),
    ("Wool Socks", "Clothing", "1.50", "4.00", 90, 12, "Central Warehouse", None),
    ("Dish Soap", "Household", "1.20", "2.80", 100, 15, "Central Warehouse", 20),
    ("Reusable Bag", "Household", "0.80", "2.00", 140, 20, "Central Warehouse", None),
)
PRICES = {p[0]: to_cents(p[3]) for p in PRODUCTS}

CUSTOMERS = (
    ("Sam Carter", "01700000001"), ("Priya Nair", "01700000002"), ("Karim Hossain", "01700000003"),
    ("Lena Fischer", "01700000004"), ("Tomas Silva", "01700000005"), ("Aisha Khan", "01700000006"),
    ("Daniel Wong", "01700000007"), ("Maria Rossi", "01700000008"),
)
SUPPLIERS = (
    ("Acme Electronics Supply", "555-0101", "sales@acme.example", "12 Tech Park"),
    ("Fresh Foods Co", "555-0102", "orders@freshfoods.example", "4 Market Road"),
    ("Style Textiles", "555-0103", "hello@styletextiles.example", "88 Mill Lane"),
)


class SeedError(Exception):
    """The demo database could not be created safely."""


def _month_label(today: date, offset: int) -> str:
    index = today.year * 12 + (today.month - 1) + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _add_products(conn, admin, today: date) -> dict[str, int]:
    ids: dict[str, int] = {}
    for index, (name, category, cost, price, stock, min_alert, warehouse, months) in enumerate(PRODUCTS, start=1):
        ids[name] = inventory_service.add_product(conn, admin, ProductForm(
            name=name, category=category, buying_price=cost, price=price, stock=str(stock),
            min_alert=str(min_alert), batch=f"B-{index:03d}", barcode=f"20000{index:02d}", warehouse=warehouse,
            expiry="" if months is None else _month_label(today, months)))
    return ids


def _plan_sales(rng: random.Random, today: date) -> list[dict]:
    """Decide every sale up front so the random sequence never depends on the time of day."""
    names = [p[0] for p in PRODUCTS]
    plans = []
    for days_ago in range(HISTORY_DAYS - 1, -1, -1):
        day = today - timedelta(days=days_ago)
        count = 0 if rng.random() < 0.12 else rng.randint(1, 4) + (1 if day.weekday() >= 4 else 0)
        for _ in range(count):
            when = datetime.combine(day, time(rng.randint(9, 20), rng.randint(0, 59), rng.randint(0, 59)))
            lines = [(name, rng.randint(1, 6) if PRICES[name] < 500 else rng.randint(1, 2))
                     for name in rng.sample(names, k=rng.randint(1, 4))]
            plans.append({
                "when": when, "lines": lines,
                "customer": rng.randrange(len(CUSTOMERS)) if rng.random() < 0.4 else None,
                "payment": rng.choices(config.PAYMENT_METHODS, weights=(55, 25, 12, 8))[0],
                "discount": rng.random() < 0.15, "redeem": rng.random() < 0.25,
            })
    return plans


class _Demo:
    def __init__(self, conn, admin, cashier, ids: dict[str, int]) -> None:
        self.conn, self.admin, self.cashier, self.ids = conn, admin, cashier, ids
        self.suppliers: dict[str, int] = {}
        self.orders: dict[str, int] = {}
        self.invoices: list[tuple[str, int]] = []      # (invoice number, one product id on it)

    def add_suppliers(self) -> None:
        for name, phone, email, address in SUPPLIERS:
            self.suppliers[name] = purchase_service.add_supplier(self.conn, self.admin, name, phone, email, address)

    def order(self, label: str, supplier: str, lines: list[tuple[str, int, str]]) -> None:
        po_lines = [POLine(self.ids[name], qty, cost) for name, qty, cost in lines]
        self.orders[label] = purchase_service.create_purchase_order(
            self.conn, self.admin, self.suppliers[supplier], po_lines, "Demo order")

    def receive(self, label: str, fraction: float) -> None:
        po_id = self.orders[label]
        items = purchase_service.get_po_items(self.conn, self.admin, po_id)
        receipts = {item["id"]: max(1, int(item["quantity_ordered"] * fraction)) for item in items}
        purchase_service.receive_purchase_order(self.conn, self.admin, po_id, receipts)

    def sale(self, plan: dict) -> None:
        items = []
        for product_name, qty in plan["lines"]:
            row = self.conn.execute("SELECT stock, min_alert FROM products WHERE id = ?",
                                    (self.ids[product_name],)).fetchone()
            room = row["stock"] - row["min_alert"] - 1       
            if room >= 1:
                items.append((self.ids[product_name], min(qty, room)))
        if not items:
            return
        name, phone = CUSTOMERS[plan["customer"]] if plan["customer"] is not None else ("", "")
        discount = "0"
        if plan["discount"] and sales_service.quote(self.conn, items, "0", "0").subtotal_cents >= 1000:
            discount = "1.00"

        invoice = None
        if plan["redeem"] and phone:
            row = self.conn.execute("SELECT loyalty_points FROM customers WHERE phone = ?", (phone,)).fetchone()
            if row is not None and row["loyalty_points"] >= 1:
                try:
                    invoice = till_service.checkout_with_payment(
                        self.conn, self.cashier, items, discount=discount, tax_rate=TAX_RATE,
                        payment_method="Card", customer_name=name, customer_phone=phone, redeem_points=1)
                except ValidationError:
                    invoice = None                          
        if invoice is None:
            method = plan["payment"]
            tendered = None
            if method == config.PAYMENT_METHODS[0]:          
                total = sales_service.quote(self.conn, items, discount, TAX_RATE).total_cents
                tendered = plain(((total + 499) // 500) * 500)
            invoice = till_service.checkout_with_payment(
                self.conn, self.cashier, items, discount=discount, tax_rate=TAX_RATE, payment_method=method,
                customer_name=name, customer_phone=phone, tendered=tendered)
        self.invoices.append((invoice.invoice_no, items[0][0]))

    def returns_and_void(self, rng: random.Random) -> None:
        if len(self.invoices) < 6:
            return
        picks = rng.sample(range(len(self.invoices)), k=4)
        for index in picks[:3]:
            invoice_no, product_id = self.invoices[index]
            returns_service.process_return(self.conn, self.admin, invoice_no, product_id, 1, "Demo return")
        till_service.void_invoice(self.conn, self.admin, self.invoices[picks[3]][0],
                                  "Demo: customer cancelled the order")


def _build(conn, rng: random.Random, real_now: datetime) -> None:
    today = real_now.date()

    def at(days_ago: int, hour: int) -> datetime:
        return datetime.combine(today - timedelta(days=days_ago), time(hour, 0))

    with mock.patch.object(timeutil, "now", return_value=datetime.combine(
            today - timedelta(days=HISTORY_DAYS), time(8, 30))):
        admin = user_service.create_first_admin(conn, "admin", ADMIN_PASSWORD)
        user_service.create_user(conn, admin, "cashier", config.ROLE_CASHIER, CASHIER_PASSWORD)
        conn.execute("UPDATE users SET must_change_password = 0")      
        settings_service.update(conn, admin, {
            "shop_name": "Demo Mart", "currency_symbol": "$", "default_tax_rate": TAX_RATE,
            "receipt_footer": "Thanks for visiting Demo Mart!"})
        lists_service.add(conn, admin, "categories", "Household")
        ids = _add_products(conn, admin, today)
    cashier = auth_service.authenticate(conn, "cashier", CASHIER_PASSWORD)
    demo = _Demo(conn, admin, cashier, ids)

    events: list[tuple[datetime, Callable[[], None]]] = [
        (at(HISTORY_DAYS, 9), demo.add_suppliers),
        (at(20, 10), partial(demo.order, "po1", "Acme Electronics Supply",
                             [("Wireless Mouse", 50, "7.50"), ("USB-C Cable (2m)", 100, "2.20")])),
        (at(18, 11), partial(demo.receive, "po1", 1.0)),
        (at(6, 10), partial(demo.order, "po2", "Fresh Foods Co",
                            [("Organic Green Tea", 40, "2.80"), ("Dark Chocolate Bar", 100, "1.10")])),
        (at(4, 11), partial(demo.receive, "po2", 0.5)),                
        (at(1, 15), partial(demo.order, "po3", "Style Textiles", [("Wool Socks", 40, "1.20")])),   
    ]
    for plan in _plan_sales(rng, today):
        if plan["when"] <= real_now:
            events.append((plan["when"], partial(demo.sale, plan)))
    events.sort(key=lambda event: event[0])
    for when, action in events:
        with mock.patch.object(timeutil, "now", return_value=when):
            action()
    with mock.patch.object(timeutil, "now", return_value=real_now):
        demo.returns_and_void(rng)


def _summary(conn, path: Path) -> dict:
    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "path": str(path),
        "products": one("SELECT COUNT(*) FROM products WHERE is_active = 1"),
        "customers": one("SELECT COUNT(*) FROM customers"),
        "invoices": one("SELECT COUNT(*) FROM invoices"),
        "voided": one("SELECT COUNT(*) FROM invoices WHERE status = 'Voided'"),
        "returns": one("SELECT COUNT(*) FROM sales_returns"),
        "purchase_orders": one("SELECT COUNT(*) FROM purchase_orders"),
    }


def seed(path: str | Path = DEFAULT_PATH, reset: bool = False) -> dict:
    """Create the demo database at ``path`` and return a summary. Refuses to touch the real database."""
    path = Path(path)
    if path.resolve() == REAL_DB.resolve():
        raise SeedError("That is your real database. Demo data is only ever written to a separate file.")
    if path.exists() and not reset:
        raise SeedError(f"{path} already exists. Use --reset to delete and rebuild it.")
    for leftover in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if leftover.exists():
            leftover.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(2026)
    real_now = timeutil.now().replace(microsecond=0)
    conn = open_database(path)
    try:
        _build(conn, rng, real_now)
        return _summary(conn, path)
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create a demo database with 30 days of sample data.")
    parser.add_argument("--path", default=str(DEFAULT_PATH), help="where to create it (default: demo/demo.db)")
    parser.add_argument("--reset", action="store_true", help="delete and rebuild an existing demo database")
    args = parser.parse_args(argv)
    try:
        summary = seed(Path(args.path), reset=args.reset)
    except SeedError as exc:
        print(f"Error: {exc}")
        return 1
    folder = Path(args.path).parent
    print(f"Demo database ready: {summary['path']}")
    print(f"  {summary['products']} products, {summary['customers']} customers, {summary['invoices']} invoices "
          f"({summary['voided']} voided), {summary['returns']} returns, {summary['purchase_orders']} purchase orders")
    print(f"Logins (demo only):  admin / {ADMIN_PASSWORD}    cashier / {CASHIER_PASSWORD}")
    print("Run the app on it (this keeps backups and receipts away from your real data):")
    print(f"  POS_DB_PATH={summary['path']} POS_BACKUP_DIR={folder / 'backups'} "
          f"POS_INVOICE_DIR={folder / 'invoices'} POS_LOG_DIR={folder / 'logs'} python3 inventory_app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

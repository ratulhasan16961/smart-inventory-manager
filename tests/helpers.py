"""Shared test fixtures (stdlib unittest, runnable with `python -m unittest` or pytest)."""
from __future__ import annotations

import sqlite3
import unittest

from pos_erp.db import connect, ensure_schema
from pos_erp.permissions import Session
from pos_erp.services import inventory_service as inv
from pos_erp.services.inventory_service import ProductForm

ADMIN = Session(1, "admin", "Admin")
CASHIER = Session(2, "cashier", "Cashier")


def make_conn() -> sqlite3.Connection:
    conn = connect(":memory:")
    ensure_schema(conn)
    return conn


def add_product(conn, name="Widget", price="10.00", cost="6.00", stock=10, min_alert=2, barcode="", **kw) -> int:
    return inv.add_product(conn, ADMIN, ProductForm(
        name=name, price=price, buying_price=cost, stock=str(stock), min_alert=str(min_alert), barcode=barcode, **kw))


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()

    def tearDown(self):
        self.conn.close()

    def stock(self, product_id) -> int:
        return self.conn.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()[0]

    def scalar(self, sql, *params):
        return self.conn.execute(sql, params).fetchone()[0]
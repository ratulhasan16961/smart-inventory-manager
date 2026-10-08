"""Current database schema (version 1). Money is INTEGER cents; relations are real FOREIGN KEYs."""
from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('Admin', 'Cashier')),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until TEXT
    )""",
    """CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL CHECK (length(trim(name)) > 0),
        category TEXT NOT NULL DEFAULT 'General',
        cost_cents INTEGER NOT NULL DEFAULT 0 CHECK (cost_cents >= 0),
        price_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
        stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
        min_alert INTEGER NOT NULL DEFAULT 0 CHECK (min_alert >= 0),
        batch TEXT NOT NULL DEFAULT '',
        barcode TEXT UNIQUE,
        warehouse TEXT NOT NULL DEFAULT '',
        expiry TEXT NOT NULL DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
    )""",
    "CREATE INDEX idx_products_name ON products (name COLLATE NOCASE)",
    "CREATE INDEX idx_products_active ON products (is_active)",
    """CREATE TABLE customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL UNIQUE,
        loyalty_points INTEGER NOT NULL DEFAULT 0 CHECK (loyalty_points >= 0),
        total_spent_cents INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT NOT NULL UNIQUE,
        customer_id INTEGER REFERENCES customers (id),
        customer_name TEXT NOT NULL DEFAULT 'Walk-in Customer',
        customer_phone TEXT NOT NULL DEFAULT 'N/A',
        subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents >= 0),
        discount_cents INTEGER NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),
        tax_rate TEXT NOT NULL DEFAULT '0',
        tax_cents INTEGER NOT NULL DEFAULT 0 CHECK (tax_cents >= 0),
        total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
        payment_method TEXT NOT NULL,
        cashier TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_invoices_created ON invoices (created_at)",
    """CREATE TABLE invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER NOT NULL REFERENCES invoices (id),
        product_id INTEGER NOT NULL REFERENCES products (id),
        product_name TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
        unit_cost_cents INTEGER NOT NULL CHECK (unit_cost_cents >= 0),
        line_subtotal_cents INTEGER NOT NULL,
        line_discount_cents INTEGER NOT NULL DEFAULT 0,
        line_tax_cents INTEGER NOT NULL DEFAULT 0,
        line_total_cents INTEGER NOT NULL,
        UNIQUE (invoice_id, product_id)
    )""",
    "CREATE INDEX idx_items_product ON invoice_items (product_id)",
    """CREATE TABLE sales_returns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_item_id INTEGER NOT NULL REFERENCES invoice_items (id),
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        refund_cents INTEGER NOT NULL CHECK (refund_cents >= 0),
        reason TEXT NOT NULL DEFAULT '',
        restocked INTEGER NOT NULL DEFAULT 1 CHECK (restocked IN (0, 1)),
        processed_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_returns_item ON sales_returns (invoice_item_id)",
    """CREATE TABLE stock_movements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products (id),
        qty_change INTEGER NOT NULL CHECK (qty_change <> 0),
        stock_after INTEGER NOT NULL CHECK (stock_after >= 0),
        reason TEXT NOT NULL CHECK (reason IN ('OPENING', 'SALE', 'RETURN', 'PURCHASE', 'ADJUSTMENT', 'IMPORT')),
        ref_type TEXT,
        ref_id INTEGER,
        note TEXT NOT NULL DEFAULT '',
        username TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_movements_product ON stock_movements (product_id, id)",
    """CREATE TABLE suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL CHECK (length(trim(name)) > 0),
        phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        address TEXT NOT NULL DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
    )""",
    """CREATE TABLE purchase_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_number TEXT NOT NULL UNIQUE,
        supplier_id INTEGER NOT NULL REFERENCES suppliers (id),
        status TEXT NOT NULL DEFAULT 'Ordered'
            CHECK (status IN ('Ordered', 'Partially Received', 'Received', 'Cancelled')),
        notes TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        closed_at TEXT
    )""",
    """CREATE TABLE purchase_order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        po_id INTEGER NOT NULL REFERENCES purchase_orders (id),
        product_id INTEGER NOT NULL REFERENCES products (id),
        quantity_ordered INTEGER NOT NULL CHECK (quantity_ordered > 0),
        quantity_received INTEGER NOT NULL DEFAULT 0
            CHECK (quantity_received >= 0 AND quantity_received <= quantity_ordered),
        unit_cost_cents INTEGER NOT NULL CHECK (unit_cost_cents >= 0),
        UNIQUE (po_id, product_id)
    )""",
    """CREATE TABLE audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        action TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )""",
)

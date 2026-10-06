"""Application-wide settings and constants (single source of truth)."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


DB_PATH = Path(os.environ.get("POS_DB_PATH", PROJECT_ROOT / "general_inventory.db"))
BACKUP_DIR = Path(os.environ.get("POS_BACKUP_DIR", PROJECT_ROOT / "backups"))
INVOICE_DIR = Path(os.environ.get("POS_INVOICE_DIR", PROJECT_ROOT / "invoices"))
LOG_DIR = Path(os.environ.get("POS_LOG_DIR", PROJECT_ROOT / "logs"))
BACKUPS_TO_KEEP = 20

CURRENCY_SYMBOL = "$"

ROLE_ADMIN = "Admin"
ROLE_CASHIER = "Cashier"


DEFAULT_PASSWORD = "1234"
MIN_PASSWORD_LENGTH = 8
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 5

EXPIRY_WARNING_DAYS = 30
LOYALTY_CENTS_PER_POINT = 10_000  

PAYMENT_METHODS = ("Cash", "Card", "bKash / Mobile", "AliPay / WeChat")
CATEGORIES = ("Electronics", "Grocery", "Clothing", "General")
WAREHOUSES = ("Central Warehouse", "Branch Warehouse")
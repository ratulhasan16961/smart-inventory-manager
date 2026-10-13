# Enterprise POS & ERP System

![CI](https://github.com/ratulhasan16961/smart-inventory-manager/actions/workflows/ci.yml/badge.svg)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

A desktop point-of-sale and inventory system for a small shop, built with Python, Tkinter and SQLite, with no
third-party runtime dependencies. It concentrates on what goes wrong in real shops: exact money maths, stock you can
trace, safe upgrades and backups, and permissions that are enforced in code, not just hidden buttons.

## Screenshots

| POS billing | Inventory |
|---|---|
| ![POS billing](docs/images/pos.png) | ![Inventory](docs/images/inventory.png) |

| Dashboard | Reports |
|---|---|
| ![Dashboard](docs/images/dashboard.png) | ![Reports](docs/images/reports.png) |

## Features

**Selling**
- Multi-item cart with barcode or ID lookup, discount, tax and several payment methods
- Cash tendering with live "change due"; a short payment blocks the sale and rolls everything back
- Loyalty points (earned automatically, redeemed as a discount) and customer lookup by phone with purchase history
- Returns and refunds with hard limits (never more than was sold or paid), void invoice with a reason
- Receipts: shown on screen, exported to a file or sent to the system printer

**Inventory**
- Products with category, buying/selling price, stock, minimum alert, batch, barcode, warehouse and expiry
- Low-stock and expiry highlighting, sorting by any column, category and low-stock/expiry filters
- Stock ledger: every change (sale, return, purchase, adjustment, import) is recorded with a reason and a balance;
  one click checks that stock equals the sum of its movements
- CSV import with a preview, validation report and all-or-nothing apply (IDs are never changed); CSV export
- Managed category and warehouse lists (renaming updates the products too)

**Purchasing**
- Suppliers, purchase orders (create, receive partly or fully, cancel); receiving raises stock through the ledger
- "From Low Stock" suggests order quantities (2 x minimum alert - stock - already on order) and remembers the
  last supplier and unit cost

**Reports**
- Dashboard: 30-day net revenue chart, top sellers, low stock
- Date-range sales report (daily, top products, payment methods, categories, refunds, discounts, tax, profit,
  voided invoices) plus low stock, expiry and stock valuation reports, all exportable to CSV

**Administration and safety**
- Users (create, disable, change role, reset password); first-run admin setup; temporary passwords must be changed;
  accounts lock after repeated failed logins; salted scrypt password hashing
- Audit log of sensitive actions; shop settings (name, currency, default tax, receipt footer)
- Automatic backup on exit (newest 20 kept), manual backup, validated restore with a safety copy, health check
- Versioned database upgrades with a file backup before each one
- Forgot your password? A one-time recovery key (shown at first-run setup, renewable in Settings) resets it from the
  login screen; an administrator can reset a cashier; the owner can run `python3 -m scripts.reset_password` on the
  computer that holds the database
  
## Try it in two minutes

```bash
git clone https://github.com/ratulhasan16961/smart-inventory-manager.git
cd smart-inventory-manager
python3 -m scripts.seed_demo
POS_DB_PATH=demo/demo.db POS_BACKUP_DIR=demo/backups POS_INVOICE_DIR=demo/invoices POS_LOG_DIR=demo/logs python3 inventory_app.py
```

The script builds a separate demo database with 30 days of sales, purchase orders, returns and a voided invoice.
Demo logins: `admin` / `demo-admin-pass` and `cashier` / `demo-cashier-pass`.

Requirements: Python 3.10+ with Tkinter (included with the python.org installers; on Debian/Ubuntu install
`python3-tk`; with Homebrew install `python-tk`). `matplotlib` is optional and only used for the pie chart in Analytics.

## Use it with your own data

```bash
python3 inventory_app.py
```

On a brand-new database the app asks you to create the administrator account. The database is created next to the
code as `general_inventory.db`. A database from the first version of this project is upgraded automatically
(a `*.pre-migration-*.bak` copy is made first).

## Roles

| | Admin | Cashier |
|---|:--:|:--:|
| Sell, look up customers and invoices, reprint receipts | yes | yes |
| Returns, void invoices | yes | no |
| Products, CSV import/export, stock adjustments, ledger | yes | no |
| Suppliers, purchase orders | yes | no |
| Reports, dashboard, analytics, audit log | yes | no |
| Users, settings, backup and restore | yes | no |

The rules live in `pos_erp/permissions.py` and are checked inside the services, so they hold even if a button is
reachable.

## How it is built

- **Layers:** `ui/` (Tkinter) calls `services/` (all business rules, no GUI code) which use `db/` (SQLite). See
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the diagram, data model and design decisions.
- **Money is integer cents**, never floats. Discounts and tax are allocated so lines always add up to the invoice.
- **Invoices keep snapshots** of product name, price and cost, so editing or deleting a product never rewrites history.
- **One door for stock:** a single function changes stock and always writes a ledger row; the database also rejects
  negative stock.
- **Atomic operations:** checkout, returns, voids, receiving and imports run in one transaction.
- **Soft delete** for products and suppliers keeps foreign keys valid.

## Project layout

```text
inventory_app.py          launcher (python3 inventory_app.py)
pos_erp/
  config.py  money.py  security.py  permissions.py  dayrange.py  bootstrap.py
  db/         connection, schema, migrations (legacy upgrade), upgrades (numbered)
  services/   auth, user, inventory, stock, sales, till, returns, purchase, customer,
              reporting, dashboard, settings, lists, backup, print, receipt, audit
  ui/         main window, POS tab, inventory tab, dashboard tab, login, dialogs/
scripts/seed_demo.py      demo data
tests/                    unittest suite (services, migrations, UI smoke tests)
docs/                     architecture notes and screenshots
```

## Configuration

Optional environment variables: `POS_DB_PATH`, `POS_BACKUP_DIR`, `POS_INVOICE_DIR`, `POS_LOG_DIR`. Shop name,
currency, default tax and receipt footer are changed inside the app (Settings > Shop Settings).

## Tests

```bash
python3 -m unittest discover -s tests -t . -v
```

170+ tests cover the services, the database upgrades and the UI. The UI tests open real (hidden) windows and skip
themselves on machines without a display. GitHub Actions runs the suite on Python 3.10, 3.12 and 3.14 and lints
with `ruff`.

## Known limitations

- One computer and one database file (SQLite); there is no multi-terminal mode.
- Batch, warehouse and expiry are attributes of a product, not separate stock records (no per-warehouse stock or FEFO).
- "Barcode Gen" shows a text preview, not a scannable barcode.
- Printing uses the system `lp` command (macOS/Linux) or the default printer (Windows).

## License

MIT. See [LICENSE](LICENSE).
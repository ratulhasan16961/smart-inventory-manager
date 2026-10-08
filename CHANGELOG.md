## 2.1.0

### Added
- User management (create users with a temporary password, disable/enable, change role, reset password).
  The last active administrator can never be disabled or demoted.
- First-run setup: a brand-new database asks for the administrator username and password
  (no default `admin/1234` account is created any more; existing databases keep their users).
- Shop settings (shop name, currency symbol, default tax, receipt footer), stored in the database.
- Backup restore with validation, an automatic safety copy and a database health check.
- Managed Category / Warehouse lists; renaming an entry also updates the products that use it.
- `db/upgrades.py`: numbered upgrades tracked in `schema_migrations`, with a file backup before each upgrade.

### Changed
- Expiry dates are stored as `YYYY-MM` (`2027-5` becomes `2027-05`); existing rows are fixed by the upgrade.
- Receipts and the Discount label use the configured shop name, footer and currency.
- Category and Warehouse dropdowns are read-only (add names under Settings > Categories & Warehouses).
- The Warehouse dropdown is wider.
# Changelog

## 2.2.0

### Added
- Cash handling at the till: "Received" field with live "Change due". Too little cash blocks the sale (and rolls it
  back). Leaving it blank means exact payment. Receipts show Received and Change.
- Void invoice (Admin, reason required): stock returns through the ledger (reason RETURN, note "VOID ..."),
  customer totals and loyalty points are reversed, reports ignore the invoice. Not possible once any item was returned.
- Invoices window (also on the POS tab): search, view / reprint receipts, void.
- Reports window (Admin): date-range sales report (daily, top products, payment methods, categories, refunds,
  discounts, tax, profit, voided invoices) plus Low Stock, Expiry and Stock Value reports, all exportable to CSV.
- Print button on receipts (macOS/Linux `lp`, Windows default printer).
- Database upgrade v3: payment and void columns on invoices.

### Changed
- Returns are refused on voided invoices.
- Cashiers can open the Invoices window; only Admins can void and open Reports.

## 2.0.0

### Added
- `pos_erp` package: services (business rules, no Tkinter), `db` layer with versioned migrations, `ui` modules.
- Purchase orders: create, receive (partial or full), cancel. Receiving raises stock through the ledger and
  sets the product's buying price to the order's unit cost.
- Stock ledger: every stock change (sale, return, purchase, adjustment, import, opening balance) is recorded.
  Manual adjustments require a reason. "Check ledger integrity" verifies stock == sum of movements.
- Invoice header + line items with price/cost snapshots; unique invoice numbers (`INV-YYYYMMDD-NNNN`).
- Role permission map (`permissions.py`) enforced inside services; UI buttons are greyed out per role.
- Forced password change for temporary passwords, lockout after 5 failed logins, failed logins audited.
- Cash-safe CSV import (validation report, dry-run preview, all-or-nothing, IDs preserved).
- Consistent backups using SQLite's backup API, with pruning (newest 20).
- Automated tests (unittest) and CI.

### Changed (behaviour you may notice)
- Money is stored as integer cents (was float). Totals, tax and discount allocation are exact.
- Passwords use salted scrypt. Old SHA-256 hashes still work once and are upgraded on login.
- Minimum password length is 8. Default `admin` / `cashier` accounts must change `1234` at first login.
- Deleting a product is a soft delete (history is kept; the barcode is freed).
- Refunds are restricted to Admin by default. To let cashiers refund, add `"returns.process"` to the
  Cashier set in `pos_erp/permissions.py`.
- Cashiers can no longer export CSV (it contains cost prices), back up the database or open the customer list.
- Editing stock on a product now requires a reason.
- Existing customers keep their stored name when another name is typed at the till.
- Receipts are exported to `invoices/` (was the current folder). The database path is absolute.
- One Tk window for login and app (was two).

### Fixed
- Revenue/profit were overcounted on multi-item invoices; profit included tax and used today's cost.
- Default users were re-created on every launch, undoing password changes.
- Two products with a blank barcode collided; CSV import replaced rows with new IDs, breaking sales history.
- Overselling via repeated cart adds; no stock re-check at checkout; no rollback on failure.
- Refunds: amount was never saved, quantity was unlimited, loyalty/customer totals were not reversed.

### Migration
- On first launch the old database is copied to `general_inventory.db.pre-migration-<timestamp>.bak`, then
  converted in one transaction. If anything fails nothing is changed.

### Known limitations
- "Barcode Gen" is still a text preview (placeholder, as before).
- Batch, warehouse and expiry are attributes of a product, not separate stock records.
- Single store, single terminal (SQLite file).
# Changelog

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
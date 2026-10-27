# Changelog
## 2.7.1

### Fixed
- The app now reports its real version number. 2.7.0 still said 2.5.0, which made the update check compare wrongly.

## 2.7.0

### Added
- Code 128 barcode labels: preview, A4 sheets (3 x 8), start at any label position.
- Payment reference (bKash / Nagad / card transaction ID) at the till, on receipts and in invoice search.
- Manager role (void, returns, reports, purchasing; no users, shop settings or restore).
- Backup copy to a second folder (cloud-synced folder, USB drive or network share).
- WhatsApp payment reminders and customer account statements (PDF).
- Daily summary email (send now, after closing a shift, or from a scheduler) and the net profit report.
- Owner's phone view: read-only, token-protected web page (python3 -m scripts.owner_view).
- Update check against the latest GitHub release.
- Database upgrades v9 (users table rebuilt for the Manager role) and v10 (payment reference).

## 2.6.0

### Added
- Receipt engine: 58 mm, 80 mm and A4 layouts, business address and phone, raw ESC/POS to thermal printers
  (network, raw print queue or device path), PDF export, test page (Settings > Receipt & Printer).
- Customer credit (baki): per-customer ledger, payments, credit limit, adjustments, CSV export, Customer Dues list.
- Daily expenses and register shifts: opening float, live cash summary (cash sales, card/digital, credit, refunds,
  expenses), closing count with the difference, shift history and printable summary.
- Standalone packaging: `pos_erp.spec`, per-user data folder, GitHub workflow that builds `.exe` and `.dmg`.
- Database upgrades v7 (credit ledger) and v8 (expenses, shifts).

## 2.5.0

### Added
- Forgot password > "Email me a code": a 6-digit code is emailed to the address registered on the account (valid
  10 minutes, one use, 5 wrong attempts, one request per minute). Unknown addresses get no email and no hint.
- Settings > Account Email: an address becomes the account email only after a code sent to it is entered.
- Mail settings come from environment variables or a git-ignored `mail.env` (Gmail App Password); the password is
  never stored in the database or backups. `POS_MAIL_MODE=console` prints the email in the terminal for testing.
- Database upgrade v6: `users.email` (unique) and hashed one-time `email_codes`.

## 2.4.0

### Added
- "Forgot password?" on the login screen: username + one-time recovery key + new password. The used key is
  replaced by a new one. Wrong keys are limited (5 attempts, then a 15 minute lock) and the screen never reveals
  whether a username exists.
- Recovery key shown once at first-run setup; Settings > Recovery Key creates or replaces it later.
- `python3 -m scripts.reset_password --user NAME`: emergency reset for a locked-out user, run on the computer
  that holds the database.
- Database upgrade v5: hashed recovery key and attempt counter per user.

### Changed
- First-run setup now shows the recovery key before the app opens.
- The login window resizes for each screen.

## 2.3.1

### Added
- MIT license and a rewritten README (screenshots, feature overview, quick start, roles, limitations).
- `scripts/seed_demo.py`: builds a separate demo database (30 days of sales, purchase orders, returns, a void)
  through the real services. It refuses to write to the real database.

### Changed
- Architecture document refreshed (permissions, data model, design decisions, QA checklist).

## 2.3.0

### Added
- Reorder from low stock: "From Low Stock" in Purchase Orders suggests quantities (2 x minimum alert - stock -
  what is already on order), remembers the last supplier and unit cost, and creates one purchase order.
- Customer lookup at the till (by phone): name, loyalty points and their value, total spent, visits, and a purchase
  history window with receipts. Cashiers can use it.
- Loyalty redemption: "Use points" lowers the bill (1 point = 1.00). It cannot exceed the customer's points or the
  bill, and voiding the invoice gives the points back.
- Inventory table: click a heading to sort, filter by category, "low stock only", "expiring / expired only".
- Dashboard tab (Admin): net revenue for the last 30 days as a bar chart, top sellers and a low-stock list.
- Database upgrade v4: `points_redeemed` and `points_value_cents` on invoices.

### Changed
- Redeemed points are part of the invoice discount, so reports show them under "Discounts given".
- Receipts show how many points were used.

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
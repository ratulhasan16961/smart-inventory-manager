# Enterprise POS & ERP System v7.0

A professional, enterprise-grade Python Desktop application designed to manage product inventory, track customer billing, generate receipts, and analyze sales data for small to medium-sized businesses. Built with SQLite3 and Tkinter.

---

## ✨ Key Features

* **📊 Live Dashboard Metrics:** Real-time summary cards showing Total Items, Low Stock Alerts, Total Stock Value ($), and Total Revenue ($).
* **🛒 Multi-Item POS & Billing Cart:** Scan barcodes or search product IDs, specify quantities, calculate subtotal, apply discounts ($) and taxes (%), and select payment methods (Cash, Card, Mobile Banking).
* **📄 Automated Receipts & Invoices:** Generates readable receipt pop-ups with options to export/save text-based receipts for customer transactions.
* **📦 Complete Inventory Management:** Add, delete, and search products with complete details including Buying Price, Selling Price, Stock, Min Alert Level, Batch Number, Barcode, Warehouse location, and Expiry date.
* **⚠️ Visual Low Stock Alerts:** Automated visual table highlighting in red for items at or below safety stock levels.
* **📈 Sales Analytics:** Visual bar charts using Matplotlib for real-time revenue and sales overview.
* **📝 Audit Logging:** Complete audit trail tracking user activities, timestamped actions, and transaction histories.
* **📁 Data Import & Export:** One-click CSV Export and Import capabilities for offline data reporting and database population.

---

## 🛠️ Tech Stack

* **Language:** Python 3.x
* **GUI Framework:** Tkinter / ttk
* **Database:** SQLite3
* **Data Visualization:** Matplotlib
* **File Handling:** JSON, CSV, Standard I/O

---

## 🚀 How to Run

### 1. Prerequisites

Ensure you have **Python 3** installed on your system:

```bash
python3 --version
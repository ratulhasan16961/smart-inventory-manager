# Ratul's Smart Inventory System v7.0

A professional, enterprise-grade Python application designed to manage inventory, track suppliers, handle multi-item billing and analyze sales data for small to medium businesses. Built with SQLite and dynamic GUI components.

## ✨ Key Features

- **📊 Live Dashboard Cards:** Real-time counters showing total products, total inventory stock value and low-stock alerts.
- **🧾 Multi-Item Cart Billing:** Add multiple products to a shopping cart, calculate dynamic grand totals and generate professional PDF customer receipts using ReportLab.
- **🔐 Role-Based Access Control (RBAC):** Differentiated UI views and action permissions for **Admin** (Full Access) and **Staff** (Billing & View Only).
- **🔄 Auto-Sequencing ID Logic:** Automatic primary key re-indexing upon product deletion to keep database records neat and contiguous.
- **🔍 Instant Live Search:** Real-time table filtering by product name or category as you type.
- **⚠️ Low Stock Alert System:** Automated pop-up warnings and visual table highlights for items at or below safety stock levels.
- **📈 Analysis Dashboard:** Visual Matplotlib bar charts for inventory volume comparison and quick decision-making.
- **🚚 Supplier Management:** Dedicated database interface to manage supplier directory and contact information.
- **📁 CSV Data Export:** One-click data backup and report export in `.csv` format for offline reporting.
- **🌓 Theme Toggle:** Modern UI with instant switching between Dark and Light visual themes.

## 🛠️ Tech Stack

- **Language:** Python 3
- **GUI Library:** Tkinter
- **Database:** SQLite3
- **Data Processing:** Pandas
- **Visualization:** Matplotlib
- **PDF Engine:** ReportLab

## 🚀 How to Run

Follow these steps to get the project up and running on your local machine:

### 1. Prerequisites
Make sure you have **Python 3** installed:
```bash
python3 --version
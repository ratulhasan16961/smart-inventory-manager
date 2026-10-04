import os
import sqlite3
import csv
import shutil
import hashlib
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def init_db():
    conn = sqlite3.connect("general_inventory.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            buying_price REAL,
            price REAL,
            stock INTEGER,
            min_alert INTEGER,
            batch TEXT,
            barcode TEXT UNIQUE,
            warehouse TEXT,
            expiry TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            po_number TEXT,
            supplier_name TEXT,
            product_name TEXT,
            quantity INTEGER,
            total_cost REAL,
            status TEXT DEFAULT 'Completed',
            date_created TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT UNIQUE,
            loyalty_points INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0.0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT,
            customer_name TEXT,
            customer_phone TEXT,
            product_id INTEGER,
            quantity INTEGER,
            subtotal REAL,
            discount REAL,
            tax REAL,
            total_price REAL,
            payment_method TEXT,
            cashier_name TEXT,
            sale_date TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT,
            timestamp TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT,
            product_id INTEGER,
            quantity INTEGER,
            refund_amount REAL,
            reason TEXT,
            return_date TEXT
        )
    """)

    cursor.execute("INSERT OR REPLACE INTO users (id, username, password, role) VALUES (1, 'admin', ?, 'Admin')", (hash_password('1234'),))
    cursor.execute("INSERT OR REPLACE INTO users (id, username, password, role) VALUES (2, 'cashier', ?, 'Cashier')", (hash_password('1234'),))

    conn.commit()
    conn.close()

init_db()

def log_action(username, action):
    conn = sqlite3.connect("general_inventory.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO audit_logs (username, action, timestamp) VALUES (?, ?, ?)",
                   (username, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def auto_backup_database():
    try:
        if not os.path.exists("backups"):
            os.makedirs("backups")
        backup_filename = f"backups/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copyfile("general_inventory.db", backup_filename)
    except Exception as e:
        print(f"Auto Backup Error: {e}")

class CanvasButton(tk.Canvas):
    def __init__(self, parent, text, command=None, bg_color="#0d6efd", fg_color="#ffffff", width=110, height=30, **kwargs):
        super().__init__(parent, width=width, height=height, bg=parent["bg"], highlightthickness=0, **kwargs)
        self.command = command
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.width = width
        self.height = height

        self.rect = self.create_rectangle(2, 2, width-2, height-2, fill=bg_color, outline=bg_color)
        self.text_id = self.create_text(width//2, height//2, text=text, fill=fg_color, font=("Helvetica", 9, "bold"))

        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_hover)
        self.bind("<Leave>", self._on_leave)

    def _on_click(self, event):
        if self.command:
            self.command()

    def _on_hover(self, event):
        self.config(cursor="hand2")

    def _on_leave(self, event):
        self.config(cursor="")

def create_styled_entry(parent, width=15, show=None):
    container = tk.Frame(parent, bg="#aaaaaa", bd=1, relief="solid")
    entry = tk.Entry(
        container,
        width=width,
        bg="#ffffff",
        fg="#000000",
        insertbackground="#000000",
        relief="flat",
        font=("Helvetica", 10),
        show=show
    )
    entry.pack(padx=3, pady=2, fill="both", expand=True)
    return container, entry

def main_app(current_user, user_role):
    root = tk.Tk()
    root.title(f"Enterprise POS & ERP System - [{current_user} ({user_role})]")
    root.geometry("1380x900")
    root.config(bg="#f4f6f9")

    def on_closing():
        auto_backup_database()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    cart_items = []

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview.Heading", font=("Helvetica", 10, "bold"), background="#212529", foreground="white")
    style.configure("Treeview", font=("Helvetica", 10), rowheight=26, background="#ffffff", fieldbackground="#ffffff", foreground="#000000")
    style.map("Treeview", background=[("selected", "#0d6efd")], foreground=[("selected", "white")])
    style.configure("TCombobox", fieldbackground="#ffffff", background="#ffffff", foreground="#000000")

    cards_frame = tk.Frame(root, bg="#f4f6f9")
    cards_frame.pack(fill="x", padx=15, pady=8)

    lbl_total_items = tk.Label(cards_frame, text="Total Items: 0", bg="#212529", fg="#ffffff", font=("Helvetica", 10, "bold"), width=22, height=2)
    lbl_total_items.grid(row=0, column=0, padx=6, pady=5)

    lbl_low_stock = tk.Label(cards_frame, text="Low Stock Alerts: 0", bg="#dc3545", fg="#ffffff", font=("Helvetica", 10, "bold"), width=22, height=2)
    lbl_low_stock.grid(row=0, column=1, padx=6, pady=5)

    lbl_total_val = tk.Label(cards_frame, text="Stock Value: $0.00", bg="#198754", fg="#ffffff", font=("Helvetica", 10, "bold"), width=24, height=2)
    lbl_total_val.grid(row=0, column=2, padx=6, pady=5)

    lbl_total_sales = tk.Label(cards_frame, text="Total Revenue: $0.00", bg="#0d6efd", fg="#ffffff", font=("Helvetica", 10, "bold"), width=24, height=2)
    lbl_total_sales.grid(row=0, column=3, padx=6, pady=5)

    def check_low_stock_alerts():
        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT name, stock, min_alert FROM inventory WHERE stock <= min_alert")
        alerts = cursor.fetchall()
        conn.close()
        
        if alerts:
            msg = "Warning! Below items are running low on stock:\n\n"
            for item in alerts:
                msg += f"• {item[0]} - Current Stock: {item[1]} (Min Limit: {item[2]})\n"
            messagebox.showwarning("Low Stock Notice", msg)

    def refresh_table():
        for row in tree.get_children():
            tree.delete(row)
            
        tree.tag_configure("low_stock", background="#ffd2d2", foreground="#842029")
        
        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, category, buying_price, price, stock, min_alert, batch FROM inventory")
        rows = cursor.fetchall()
        
        total_items = len(rows)
        low_stock_count = 0
        total_value = 0.0

        for r in rows:
            stock = r[5]
            min_alert = r[6]
            
            if stock is not None and min_alert is not None and stock <= min_alert:
                tree.insert("", "end", values=r, tags=("low_stock",))
                low_stock_count += 1
            else:
                tree.insert("", "end", values=r)

            if stock is not None and r[4] is not None:
                total_value += (stock * r[4])
                
        cursor.execute("SELECT SUM(total_price) FROM sales")
        total_rev = cursor.fetchone()[0] or 0.0
        conn.close()

        lbl_total_items.config(text=f"Total Items: {total_items}")
        lbl_low_stock.config(text=f"Low Stock Alerts: {low_stock_count}")
        lbl_total_val.config(text=f"Stock Value: ${total_value:.2f}")
        lbl_total_sales.config(text=f"Total Revenue: ${total_rev:.2f}")

    def clear_product_form():
        name_entry.delete(0, tk.END)
        category_combo.set('')
        buying_price_entry.delete(0, tk.END)
        price_entry.delete(0, tk.END)
        stock_entry.delete(0, tk.END)
        min_alert_entry.delete(0, tk.END)
        batch_entry.delete(0, tk.END)
        barcode_entry.delete(0, tk.END)
        warehouse_combo.set('')
        expiry_entry.delete(0, tk.END)

    def add_product():
        if user_role == "Cashier":
            messagebox.showerror("Access Denied", "Cashier cannot add products!")
            return

        n = name_entry.get().strip()
        cat = category_combo.get().strip()
        try:
            bp = float(buying_price_entry.get().strip() or 0)
            sp = float(price_entry.get().strip() or 0)
            st = int(stock_entry.get().strip() or 0)
            ma = int(min_alert_entry.get().strip() or 0)
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric values for price and stock!")
            return

        batch = batch_entry.get().strip()
        barcode = barcode_entry.get().strip()
        warehouse = warehouse_combo.get().strip()
        expiry = expiry_entry.get().strip()

        if not n:
            messagebox.showwarning("Warning", "Product Name is required!")
            return

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO inventory (name, category, buying_price, price, stock, min_alert, batch, barcode, warehouse, expiry)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (n, cat, bp, sp, st, ma, batch, barcode, warehouse, expiry))
            conn.commit()
            log_action(current_user, f"Added product: {n}")
            messagebox.showinfo("Success", "Product added successfully!")
            clear_product_form()
        except sqlite3.IntegrityError:
            messagebox.showerror("Error", "Duplicate barcode detected!")
        finally:
            conn.close()

        refresh_table()

    def open_edit_product_window(event=None):
        if user_role == "Cashier":
            return
        selected = tree.selection()
        if not selected:
            return
        
        item_vals = tree.item(selected[0], 'values')
        p_id = item_vals[0]

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT name, category, buying_price, price, stock, min_alert, batch, barcode, warehouse, expiry FROM inventory WHERE id = ?", (p_id,))
        p_data = cursor.fetchone()
        conn.close()

        if not p_data:
            return

        edit_win = tk.Toplevel(root)
        edit_win.title(f"Edit Product ID: {p_id}")
        edit_win.geometry("400x480")
        edit_win.config(bg="#f4f6f9")

        fields = ["Name", "Category", "Buying Price", "Selling Price", "Stock", "Min Alert", "Batch", "Barcode", "Warehouse", "Expiry"]
        entries = {}

        for idx, field in enumerate(fields):
            tk.Label(edit_win, text=f"{field}:", bg="#f4f6f9", font=("Helvetica", 9, "bold")).grid(row=idx, column=0, padx=10, pady=5, sticky="e")
            container, ent = create_styled_entry(edit_win, width=22)
            container.grid(row=idx, column=1, padx=10, pady=5)
            ent.insert(0, str(p_data[idx] if p_data[idx] is not None else ""))
            entries[field] = ent

        def save_updates():
            try:
                conn = sqlite3.connect("general_inventory.db")
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE inventory SET name=?, category=?, buying_price=?, price=?, stock=?, min_alert=?, batch=?, barcode=?, warehouse=?, expiry=?
                    WHERE id=?
                """, (
                    entries["Name"].get().strip(),
                    entries["Category"].get().strip(),
                    float(entries["Buying Price"].get().strip() or 0),
                    float(entries["Selling Price"].get().strip() or 0),
                    int(entries["Stock"].get().strip() or 0),
                    int(entries["Min Alert"].get().strip() or 0),
                    entries["Batch"].get().strip(),
                    entries["Barcode"].get().strip(),
                    entries["Warehouse"].get().strip(),
                    entries["Expiry"].get().strip(),
                    p_id
                ))
                conn.commit()
                conn.close()
                log_action(current_user, f"Updated Product ID: {p_id}")
                messagebox.showinfo("Updated", "Product successfully updated!")
                edit_win.destroy()
                refresh_table()
            except Exception as e:
                messagebox.showerror("Error", f"Could not update product: {str(e)}")

        btn_save = CanvasButton(edit_win, text="Save Changes", command=save_updates, bg_color="#198754", fg_color="#ffffff", width=130, height=32)
        btn_save.grid(row=len(fields), column=0, columnspan=2, pady=15)

    def delete_selected_product():
        if user_role == "Cashier":
            messagebox.showerror("Access Denied", "Cashier cannot delete products!")
            return

        selected = tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select a product to delete!")
            return
        
        item_vals = tree.item(selected[0], 'values')
        p_id = item_vals[0]

        if messagebox.askyesno("Confirm", f"Are you sure you want to delete Product ID {p_id}?"):
            conn = sqlite3.connect("general_inventory.db")
            cursor = conn.cursor()
            cursor.execute("DELETE FROM inventory WHERE id = ?", (p_id,))
            conn.commit()
            conn.close()
            log_action(current_user, f"Deleted Product ID: {p_id}")
            refresh_table()

    lbl_style = {"bg": "#ffffff", "fg": "#212529", "font": ("Helvetica", 9, "bold")}

    bill_frame = tk.LabelFrame(root, text=" Customer Billing & POS Multi-Item Cart ", bg="#ffffff", fg="#0d6efd", font=("Helvetica", 10, "bold"), bd=1, relief="solid")
    bill_frame.pack(fill="x", padx=15, pady=5)

    tk.Label(bill_frame, text="Barcode Scan / ID:", **lbl_style).grid(row=0, column=0, padx=4, pady=6, sticky="e")
    c_pid, prod_id_e = create_styled_entry(bill_frame, width=10)
    c_pid.grid(row=0, column=1, padx=4, pady=6)

    tk.Label(bill_frame, text="Qty:", **lbl_style).grid(row=0, column=2, padx=4, pady=6, sticky="e")
    c_pqty, prod_qty_e = create_styled_entry(bill_frame, width=5)
    c_pqty.grid(row=0, column=3, padx=4, pady=6)

    def add_to_cart():
        p_id_str = prod_id_e.get().strip()
        p_qty_str = prod_qty_e.get().strip() or "1"

        if not p_id_str:
            messagebox.showwarning("Warning", "Product Barcode or ID required!")
            return

        try:
            p_qty = int(p_qty_str)
        except ValueError:
            messagebox.showerror("Error", "Invalid Quantity!")
            return

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, stock FROM inventory WHERE barcode = ? OR id = ?", (p_id_str, p_id_str))
        item = cursor.fetchone()
        conn.close()

        if not item:
            messagebox.showerror("Error", "Product not found!")
            return

        p_id, p_name, unit_price, stock = item
        if stock < p_qty:
            messagebox.showerror("Stock Error", f"Insufficient stock! Available: {stock}")
            return

        item_total = unit_price * p_qty
        cart_items.append({
            "id": p_id,
            "name": p_name,
            "price": unit_price,
            "qty": p_qty,
            "total": item_total
        })

        lbl_cart_status.config(text=f"Cart Items: {len(cart_items)} | Last Added: {p_name}")
        prod_id_e.delete(0, tk.END)
        prod_qty_e.delete(0, tk.END)

    btn_add_cart = CanvasButton(bill_frame, text="🛒 Add Cart", command=add_to_cart, bg_color="#6f42c1", fg_color="#ffffff", width=90, height=28)
    btn_add_cart.grid(row=0, column=4, padx=6, pady=6)

    lbl_cart_status = tk.Label(bill_frame, text="Cart Items: 0", bg="#ffffff", fg="#198754", font=("Helvetica", 9, "bold"))
    lbl_cart_status.grid(row=0, column=5, padx=8)

    tk.Label(bill_frame, text="Discount ($):", **lbl_style).grid(row=0, column=6, padx=4, pady=6, sticky="e")
    c_disc, disc_e = create_styled_entry(bill_frame, width=6)
    c_disc.grid(row=0, column=7, padx=4, pady=6)
    disc_e.insert(0, "0")

    tk.Label(bill_frame, text="Tax (%):", **lbl_style).grid(row=0, column=8, padx=4, pady=6, sticky="e")
    c_tax, tax_e = create_styled_entry(bill_frame, width=5)
    c_tax.grid(row=0, column=9, padx=4, pady=6)
    tax_e.insert(0, "0")

    tk.Label(bill_frame, text="Payment:", **lbl_style).grid(row=0, column=10, padx=4, pady=6, sticky="e")
    pay_combo = ttk.Combobox(bill_frame, values=["Cash", "Card", "bKash / Mobile", "AliPay / WeChat"], width=12, font=("Helvetica", 9))
    pay_combo.grid(row=0, column=11, padx=4, pady=6)
    pay_combo.set("Cash")

    tk.Label(bill_frame, text="Customer Name:", **lbl_style).grid(row=1, column=0, padx=4, pady=6, sticky="e")
    c_cust_n, cust_name_e = create_styled_entry(bill_frame, width=12)
    c_cust_n.grid(row=1, column=1, padx=4, pady=6)

    tk.Label(bill_frame, text="Phone:", **lbl_style).grid(row=1, column=2, padx=4, pady=6, sticky="e")
    c_cust_p, cust_phone_e = create_styled_entry(bill_frame, width=11)
    c_cust_p.grid(row=1, column=3, padx=4, pady=6)

    def process_sale():
        if not cart_items:
            messagebox.showwarning("Warning", "Cart is empty! Add products to cart first.")
            return

        c_name = cust_name_e.get().strip() or "Walk-in Customer"
        c_phone = cust_phone_e.get().strip() or "N/A"

        try:
            disc = float(disc_e.get().strip() or 0)
            tax_rate = float(tax_e.get().strip() or 0)
        except ValueError:
            messagebox.showerror("Error", "Invalid Discount or Tax format!")
            return

        subtotal = sum(item["total"] for item in cart_items)
        tax_amt = (subtotal - disc) * (tax_rate / 100.0)
        total_price = (subtotal - disc) + tax_amt
        inv_no = "INV-" + datetime.now().strftime("%Y%m%d%H%M%S")
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pay_method = pay_combo.get()

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()

        if c_phone != "N/A":
            earned_points = int(total_price * 0.01) # 1% loyalty
            cursor.execute("""
                INSERT INTO customers (name, phone, loyalty_points, total_spent)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(phone) DO UPDATE SET
                name = excluded.name,
                loyalty_points = loyalty_points + excluded.loyalty_points,
                total_spent = total_spent + excluded.total_spent
            """, (c_name, c_phone, earned_points, total_price))

        for c_item in cart_items:
            cursor.execute("UPDATE inventory SET stock = stock - ? WHERE id = ?", (c_item["qty"], c_item["id"]))
            cursor.execute("""
                INSERT INTO sales (invoice_no, customer_name, customer_phone, product_id, quantity, subtotal, discount, tax, total_price, payment_method, cashier_name, sale_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (inv_no, c_name, c_phone, c_item["id"], c_item["qty"], c_item["total"], disc, tax_amt, total_price, pay_method, current_user, date_str))

        conn.commit()
        conn.close()

        log_action(current_user, f"Processed Sale Invoice: {inv_no} (${total_price:.2f})")
        refresh_table()

        rec_win = tk.Toplevel(root)
        rec_win.title("Sales Invoice & Receipt")
        rec_win.geometry("420x560")
        rec_win.config(bg="#ffffff")

        item_rows = ""
        for itm in cart_items:
            item_rows += f"{itm['name'][:18]:<18} x{itm['qty']:<2} ${itm['total']:.2f}\n"

        receipt_text = f"""
=========================================
          ENTERPRISE POS RECEIPT
=========================================
Invoice No : {inv_no}
Date       : {date_str}
Cashier    : {current_user}

Customer   : {c_name}
Phone      : {c_phone}
-----------------------------------------
ITEM                 QTY  PRICE
-----------------------------------------
{item_rows}-----------------------------------------
Subtotal   : ${subtotal:.2f}
Discount   : -${disc:.2f}
Tax ({tax_rate}%): +${tax_amt:.2f}
-----------------------------------------
TOTAL PAID : ${total_price:.2f}
Payment    : {pay_method}
=========================================
        Thank you for shopping with us!
=========================================
        """
        lbl_rec = tk.Label(rec_win, text=receipt_text, font=("Courier", 10), bg="#ffffff", fg="#000000", justify="left")
        lbl_rec.pack(padx=10, pady=10)

        def export_txt():
            filename = f"Invoice_{inv_no}.txt"
            with open(filename, "w") as f:
                f.write(receipt_text)
            messagebox.showinfo("Export Successful", f"Invoice saved as {filename}")

        btn_box = tk.Frame(rec_win, bg="#ffffff")
        btn_box.pack(pady=5)

        btn_pdf = CanvasButton(btn_box, text="📄 Export Invoice", command=export_txt, bg_color="#198754", fg_color="#ffffff", width=130, height=32)
        btn_pdf.pack()

        cart_items.clear()
        lbl_cart_status.config(text="Cart Items: 0")

    btn_sell = CanvasButton(bill_frame, text="💳 Process Bill", command=process_sale, bg_color="#198754", fg_color="#ffffff", width=110, height=30)
    btn_sell.grid(row=1, column=10, columnspan=2, padx=8, pady=6)

    input_frame = tk.LabelFrame(root, text=" Product Entry Form ", bg="#ffffff", fg="#212529", font=("Helvetica", 10, "bold"), bd=1, relief="solid")
    input_frame.pack(fill="x", padx=15, pady=5)

    form_grid = tk.Frame(input_frame, bg="#ffffff")
    form_grid.pack(side="left", fill="both", expand=True, padx=10, pady=10)

    btn_grid = tk.Frame(input_frame, bg="#ffffff")
    btn_grid.pack(side="right", fill="y", padx=15, pady=10)

    tk.Label(form_grid, text="Name:", **lbl_style).grid(row=0, column=0, sticky="e", padx=5, pady=5)
    c_name, name_entry = create_styled_entry(form_grid, width=15)
    c_name.grid(row=0, column=1, padx=5, pady=5)

    tk.Label(form_grid, text="Category:", **lbl_style).grid(row=0, column=2, sticky="e", padx=5, pady=5)
    category_combo = ttk.Combobox(form_grid, values=["Electronics", "Grocery", "Clothing", "General"], width=13, font=("Helvetica", 10))
    category_combo.grid(row=0, column=3, padx=5, pady=5)

    tk.Label(form_grid, text="Buying Price:", **lbl_style).grid(row=0, column=4, sticky="e", padx=5, pady=5)
    c_bp, buying_price_entry = create_styled_entry(form_grid, width=12)
    c_bp.grid(row=0, column=5, padx=5, pady=5)

    tk.Label(form_grid, text="Selling Price:", **lbl_style).grid(row=1, column=0, sticky="e", padx=5, pady=5)
    c_sp, price_entry = create_styled_entry(form_grid, width=15)
    c_sp.grid(row=1, column=1, padx=5, pady=5)

    tk.Label(form_grid, text="Stock:", **lbl_style).grid(row=1, column=2, sticky="e", padx=5, pady=5)
    c_st, stock_entry = create_styled_entry(form_grid, width=15)
    c_st.grid(row=1, column=3, padx=5, pady=5)

    tk.Label(form_grid, text="Min Alert:", **lbl_style).grid(row=1, column=4, sticky="e", padx=5, pady=5)
    c_ma, min_alert_entry = create_styled_entry(form_grid, width=12)
    c_ma.grid(row=1, column=5, padx=5, pady=5)

    tk.Label(form_grid, text="Batch No:", **lbl_style).grid(row=2, column=0, sticky="e", padx=5, pady=5)
    c_ba, batch_entry = create_styled_entry(form_grid, width=15)
    c_ba.grid(row=2, column=1, padx=5, pady=5)

    tk.Label(form_grid, text="Barcode:", **lbl_style).grid(row=2, column=2, sticky="e", padx=5, pady=5)
    c_bc, barcode_entry = create_styled_entry(form_grid, width=15)
    c_bc.grid(row=2, column=3, padx=5, pady=5)

    tk.Label(form_grid, text="Warehouse:", **lbl_style).grid(row=2, column=4, sticky="e", padx=5, pady=5)
    warehouse_combo = ttk.Combobox(form_grid, values=["Central Warehouse", "Branch Warehouse"], width=11, font=("Helvetica", 10))
    warehouse_combo.grid(row=2, column=5, padx=5, pady=5)

    tk.Label(form_grid, text="Expiry (YYYY-MM):", **lbl_style).grid(row=3, column=0, sticky="e", padx=5, pady=5)
    c_ex, expiry_entry = create_styled_entry(form_grid, width=15)
    c_ex.grid(row=3, column=1, padx=5, pady=5)

    def export_inventory_csv():
        filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
        if not filepath:
            return

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM inventory")
        rows = cursor.fetchall()
        headers = [description[0] for description in cursor.description]
        conn.close()

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)

        messagebox.showinfo("Export Complete", "Inventory exported to CSV successfully!")

    def import_inventory_csv():
        if user_role == "Cashier":
            messagebox.showerror("Access Denied", "Cashier cannot import CSV!")
            return

        filepath = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if not filepath:
            return

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        count = 0
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cursor.execute("""
                        INSERT OR REPLACE INTO inventory (name, category, buying_price, price, stock, min_alert, batch, barcode, warehouse, expiry)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (row['name'], row['category'], row['buying_price'], row['price'], row['stock'], row['min_alert'], row['batch'], row['barcode'], row['warehouse'], row['expiry']))
                    count += 1
            conn.commit()
            messagebox.showinfo("Import Complete", f"Successfully imported {count} items!")
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to import CSV: {str(e)}")
        finally:
            conn.close()
            refresh_table()

    def backup_database():
        backup_path = filedialog.asksaveasfilename(defaultextension=".db", filetypes=[("SQLite DB", "*.db")])
        if backup_path:
            shutil.copyfile("general_inventory.db", backup_path)
            messagebox.showinfo("Backup Success", f"Database backed up to {backup_path}")

    def open_analytics_dashboard():
        if user_role == "Cashier":
            messagebox.showerror("Access Denied", "Cashier cannot view analytics!")
            return

        dash_win = tk.Toplevel(root)
        dash_win.title("Sales & Profit Analytics Dashboard")
        dash_win.geometry("820x620")
        dash_win.config(bg="#f4f6f9")

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        
        cursor.execute("SELECT SUM(total_price), COUNT(id) FROM sales")
        s_res = cursor.fetchone()
        tot_rev = s_res[0] or 0.0
        tot_sales_count = s_res[1] or 0

        cursor.execute("""
            SELECT SUM((s.total_price - (i.buying_price * s.quantity)))
            FROM sales s JOIN inventory i ON s.product_id = i.id
        """)
        est_profit = cursor.fetchone()[0] or 0.0

        cursor.execute("""
            SELECT i.category, SUM(s.quantity) 
            FROM sales s JOIN inventory i ON s.product_id = i.id 
            GROUP BY i.category
        """)
        cat_data = cursor.fetchall()
        conn.close()

        tk.Label(dash_win, text="📊 Financial Analytics & Visual Reports", bg="#f4f6f9", fg="#212529", font=("Helvetica", 14, "bold")).pack(pady=10)
        
        f_top = tk.Frame(dash_win, bg="#ffffff", bd=1, relief="solid")
        f_top.pack(padx=15, pady=5, fill="x")

        tk.Label(f_top, text=f"Total Orders: {tot_sales_count}  |  Total Revenue: ${tot_rev:.2f}  |  Est. Net Profit: ${est_profit:.2f}", bg="#ffffff", font=("Helvetica", 11, "bold"), fg="#198754").pack(pady=10)

        if HAS_MATPLOTLIB and cat_data:
            categories = [c[0] if c[0] else 'General' for c in cat_data]
            quantities = [c[1] for c in cat_data]

            fig, ax = plt.subplots(figsize=(6, 3.8), dpi=100)
            ax.pie(quantities, labels=categories, autopct='%1.1f%%', startangle=140, colors=['#0d6efd', '#198754', '#fd7e14', '#6f42c1', '#dc3545'])
            ax.set_title("Sales Distribution by Category")

            canvas = FigureCanvasTkAgg(fig, master=dash_win)
            canvas.draw()
            canvas.get_tk_widget().pack(pady=10)
        else:
            f_text = tk.Frame(dash_win, bg="#ffffff", bd=1, relief="solid")
            f_text.pack(padx=15, pady=10, fill="both", expand=True)
            tk.Label(f_text, text="Category Wise Sales Breakdown:", bg="#ffffff", font=("Helvetica", 11, "bold")).pack(anchor="w", padx=10, pady=5)
            for cd in cat_data:
                tk.Label(f_text, text=f"• {cd[0] or 'General'}: {cd[1]} units sold", bg="#ffffff", font=("Helvetica", 10)).pack(anchor="w", padx=20, pady=2)

    def open_barcode_generator():
        bc_win = tk.Toplevel(root)
        bc_win.title("Barcode & Label Generator")
        bc_win.geometry("380x300")
        bc_win.config(bg="#f4f6f9")

        tk.Label(bc_win, text="Generate Item Barcode Label", bg="#f4f6f9", font=("Helvetica", 12, "bold")).pack(pady=10)
        tk.Label(bc_win, text="Product Code / Barcode:", bg="#f4f6f9", font=("Helvetica", 9, "bold")).pack()
        c_b, bc_e = create_styled_entry(bc_win, width=22)
        c_b.pack(pady=5)

        preview_frame = tk.Frame(bc_win, bg="#ffffff", width=250, height=80, bd=1, relief="solid")
        preview_frame.pack(pady=10)
        lbl_preview = tk.Label(preview_frame, text="Barcode Visual Canvas", bg="#ffffff")
        lbl_preview.pack(expand=True)

        def draw_barcode():
            val = bc_e.get().strip()
            if not val:
                messagebox.showwarning("Warning", "Enter barcode value!")
                return
            lbl_preview.config(text=f"||||||||||||||||||||||||||||\n{val}\n[ENTERPRISE POS LABEL]", font=("Courier", 11, "bold"))
            messagebox.showinfo("Success", "Barcode generated on Canvas Preview!")

        btn_gen = CanvasButton(bc_win, text="Generate Label", command=draw_barcode, bg_color="#0d6efd", fg_color="#ffffff", width=130, height=30)
        btn_gen.pack(pady=5)

    def open_supplier_module():
        if user_role == "Cashier":
            messagebox.showerror("Access Denied", "Cashier cannot access Supplier Management!")
            return

        sup_win = tk.Toplevel(root)
        sup_win.title("Supplier & Purchase Order (PO) Management")
        sup_win.geometry("620x420")
        sup_win.config(bg="#f4f6f9")

        tk.Label(sup_win, text="Supplier Management", bg="#f4f6f9", font=("Helvetica", 12, "bold")).pack(pady=8)
        
        f_in = tk.Frame(sup_win, bg="#f4f6f9")
        f_in.pack(pady=5)

        tk.Label(f_in, text="Name:", bg="#f4f6f9", font=("Helvetica", 9, "bold")).grid(row=0, column=0, padx=4)
        c1, s_name_e = create_styled_entry(f_in, width=12)
        c1.grid(row=0, column=1, padx=4)

        tk.Label(f_in, text="Phone:", bg="#f4f6f9", font=("Helvetica", 9, "bold")).grid(row=0, column=2, padx=4)
        c2, s_phone_e = create_styled_entry(f_in, width=12)
        c2.grid(row=0, column=3, padx=4)

        def add_supplier():
            sn = s_name_e.get().strip()
            sp = s_phone_e.get().strip()
            if not sn:
                messagebox.showwarning("Warning", "Supplier Name Required!")
                return
            conn = sqlite3.connect("general_inventory.db")
            cursor = conn.cursor()
            cursor.execute("INSERT INTO suppliers (name, phone) VALUES (?, ?)", (sn, sp))
            conn.commit()
            conn.close()
            messagebox.showinfo("Saved", "Supplier added successfully!")
            load_suppliers()

        btn_add_s = CanvasButton(f_in, text="Add Supplier", command=add_supplier, bg_color="#198754", fg_color="#ffffff", width=100, height=28)
        btn_add_s.grid(row=0, column=4, padx=6)

        cols = ("ID", "Supplier Name", "Phone")
        t_sup = ttk.Treeview(sup_win, columns=cols, show="headings", height=8)
        for c in cols:
            t_sup.heading(c, text=c)
            t_sup.column(c, width=150, anchor="center")
        t_sup.pack(fill="both", expand=True, padx=15, pady=10)

        def load_suppliers():
            for r in t_sup.get_children():
                t_sup.delete(r)
            conn = sqlite3.connect("general_inventory.db")
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, phone FROM suppliers")
            for row in cursor.fetchall():
                t_sup.insert("", "end", values=row)
            conn.close()

        load_suppliers()

    def open_return_refund_module():
        ref_win = tk.Toplevel(root)
        ref_win.title("Sales Return & Refund Terminal")
        ref_win.geometry("400x320")
        ref_win.config(bg="#f4f6f9")

        tk.Label(ref_win, text="Process Return / Refund", bg="#f4f6f9", font=("Helvetica", 12, "bold")).pack(pady=10)

        tk.Label(ref_win, text="Invoice No (e.g. INV-...):", bg="#f4f6f9", font=("Helvetica", 9, "bold")).pack()
        c1, inv_e = create_styled_entry(ref_win, width=22)
        c1.pack(pady=3)

        tk.Label(ref_win, text="Product ID:", bg="#f4f6f9", font=("Helvetica", 9, "bold")).pack()
        c2, pid_e = create_styled_entry(ref_win, width=22)
        c2.pack(pady=3)

        tk.Label(ref_win, text="Return Qty:", bg="#f4f6f9", font=("Helvetica", 9, "bold")).pack()
        c3, qty_e = create_styled_entry(ref_win, width=22)
        c3.pack(pady=3)

        def process_return():
            inv = inv_e.get().strip()
            p_id = pid_e.get().strip()
            try:
                rqty = int(qty_e.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Invalid Qty!")
                return

            conn = sqlite3.connect("general_inventory.db")
            cursor = conn.cursor()
            cursor.execute("SELECT id, quantity, total_price FROM sales WHERE invoice_no = ? AND product_id = ?", (inv, p_id))
            sale_rec = cursor.fetchone()

            if not sale_rec:
                messagebox.showerror("Error", "Matching sale record not found!")
                conn.close()
                return

            cursor.execute("UPDATE inventory SET stock = stock + ? WHERE id = ?", (rqty, p_id))
            cursor.execute("INSERT INTO sales_returns (invoice_no, product_id, quantity, return_date) VALUES (?, ?, ?, ?)",
                           (inv, p_id, rqty, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()

            log_action(current_user, f"Processed Refund for Invoice: {inv}, Product ID: {p_id}")
            messagebox.showinfo("Success", "Item refunded and stock updated successfully!")
            refresh_table()
            ref_win.destroy()

        btn_ref = CanvasButton(ref_win, text="Confirm Refund", command=process_return, bg_color="#dc3545", fg_color="#ffffff", width=130, height=30)
        btn_ref.pack(pady=12)

    def open_customer_database():
        cust_win = tk.Toplevel(root)
        cust_win.title("Customer Loyalty & History Database")
        cust_win.geometry("550x360")

        cols = ("ID", "Customer Name", "Phone", "Loyalty Points", "Total Spent ($)")
        t_cust = ttk.Treeview(cust_win, columns=cols, show="headings")
        for c in cols:
            t_cust.heading(c, text=c)
            t_cust.column(c, width=100, anchor="center")
        t_cust.pack(fill="both", expand=True, padx=10, pady=10)

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, phone, loyalty_points, total_spent FROM customers ORDER BY total_spent DESC")
        for r in cursor.fetchall():
            t_cust.insert("", "end", values=r)
        conn.close()

    def open_audit_logs():
        if user_role != "Admin":
            messagebox.showerror("Access Denied", "Only Admin can view Audit Logs!")
            return

        log_win = tk.Toplevel(root)
        log_win.title("System Audit Logs")
        log_win.geometry("550x350")

        cols = ("ID", "User", "Action", "Timestamp")
        t_log = ttk.Treeview(log_win, columns=cols, show="headings")
        for c in cols:
            t_log.heading(c, text=c)
            t_log.column(c, width=120, anchor="center")
        t_log.pack(fill="both", expand=True, padx=10, pady=10)

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC")
        for r in cursor.fetchall():
            t_log.insert("", "end", values=r)
        conn.close()

    def open_settings_window():
        settings_win = tk.Toplevel(root)
        settings_win.title("Settings & Maintenance")
        settings_win.geometry("380x380")
        settings_win.config(bg="#f4f6f9")

        tk.Label(settings_win, text="Update Security Settings", bg="#f4f6f9", fg="#212529", font=("Helvetica", 11, "bold")).pack(pady=8)

        tk.Label(settings_win, text="New Username:", bg="#f4f6f9", fg="#212529", font=("Helvetica", 9, "bold")).pack()
        c_u, new_user_entry = create_styled_entry(settings_win, width=20)
        c_u.pack(pady=3)

        tk.Label(settings_win, text="New Password:", bg="#f4f6f9", fg="#212529", font=("Helvetica", 9, "bold")).pack()
        c_p, new_pass_entry = create_styled_entry(settings_win, width=20, show="*")
        c_p.pack(pady=3)

        def save_credentials():
            u = new_user_entry.get().strip()
            p = new_pass_entry.get().strip()

            if not u or not p:
                messagebox.showwarning("Warning", "Fields cannot be empty!")
                return

            conn = sqlite3.connect("general_inventory.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET username = ?, password = ? WHERE username = ?", (u, hash_password(p), current_user))
            conn.commit()
            conn.close()

            log_action(current_user, "Updated login credentials")
            messagebox.showinfo("Success", "Username & Password updated with SHA256 Encryption!")
            settings_win.destroy()

        btn_save = CanvasButton(settings_win, text="Save Credentials", command=save_credentials, bg_color="#198754", fg_color="#ffffff", width=140, height=30)
        btn_save.pack(pady=8)

        tk.Frame(settings_win, height=1, bg="#cccccc").pack(fill="x", padx=15, pady=5)

        btn_bak = CanvasButton(settings_win, text="💾 Backup Database", command=backup_database, bg_color="#0d6efd", fg_color="#ffffff", width=140, height=30)
        btn_bak.pack(pady=4)

        btn_cust = CanvasButton(settings_win, text="👥 Customer Base", command=open_customer_database, bg_color="#6f42c1", fg_color="#ffffff", width=140, height=30)
        btn_cust.pack(pady=4)

    btn_add = CanvasButton(btn_grid, text="➕ Add Product", command=add_product, bg_color="#198754", fg_color="#ffffff", width=110, height=30)
    btn_add.grid(row=0, column=0, padx=4, pady=3)

    btn_dash = CanvasButton(btn_grid, text="📊 Analytics", command=open_analytics_dashboard, bg_color="#fd7e14", fg_color="#ffffff", width=110, height=30)
    btn_dash.grid(row=0, column=1, padx=4, pady=3)

    btn_delete = CanvasButton(btn_grid, text="🗑 Delete Product", command=delete_selected_product, bg_color="#dc3545", fg_color="#ffffff", width=110, height=30)
    btn_delete.grid(row=1, column=0, padx=4, pady=3)

    btn_exp = CanvasButton(btn_grid, text="📤 Export CSV", command=export_inventory_csv, bg_color="#0d6efd", fg_color="#ffffff", width=110, height=30)
    btn_exp.grid(row=1, column=1, padx=4, pady=3)

    btn_imp = CanvasButton(btn_grid, text="📥 Import CSV", command=import_inventory_csv, bg_color="#6c757d", fg_color="#ffffff", width=110, height=30)
    btn_imp.grid(row=2, column=0, padx=4, pady=3)

    btn_logs = CanvasButton(btn_grid, text="📜 Audit Logs", command=open_audit_logs, bg_color="#212529", fg_color="#ffffff", width=110, height=30)
    btn_logs.grid(row=2, column=1, padx=4, pady=3)

    btn_bc = CanvasButton(btn_grid, text="🏷 Barcode Gen", command=open_barcode_generator, bg_color="#0dcaf0", fg_color="#000000", width=110, height=30)
    btn_bc.grid(row=3, column=0, padx=4, pady=3)

    btn_sup = CanvasButton(btn_grid, text="🏭 Suppliers", command=open_supplier_module, bg_color="#20c997", fg_color="#ffffff", width=110, height=30)
    btn_sup.grid(row=3, column=1, padx=4, pady=3)

    btn_ref = CanvasButton(btn_grid, text="🔄 Return / Refund", command=open_return_refund_module, bg_color="#d63384", fg_color="#ffffff", width=110, height=30)
    btn_ref.grid(row=4, column=0, padx=4, pady=3)

    btn_set = CanvasButton(btn_grid, text="⚙ Settings", command=open_settings_window, bg_color="#495057", fg_color="#ffffff", width=110, height=30)
    btn_set.grid(row=4, column=1, padx=4, pady=3)

    search_frame = tk.Frame(root, bg="#f4f6f9")
    search_frame.pack(fill="x", padx=15, pady=5)

    tk.Label(search_frame, text="Search Inventory:", bg="#f4f6f9", fg="#212529", font=("Helvetica", 10, "bold")).pack(side="left", padx=5)
    c_s, search_entry = create_styled_entry(search_frame, width=25)
    c_s.pack(side="left", padx=5)

    def filter_inventory():
        q = search_entry.get().strip().lower()
        for row in tree.get_children():
            tree.delete(row)

        tree.tag_configure("low_stock", background="#ffd2d2", foreground="#842029")
            
        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, category, buying_price, price, stock, min_alert, batch FROM inventory WHERE LOWER(name) LIKE ? OR LOWER(category) LIKE ? OR barcode LIKE ?", 
                       (f"%{q}%", f"%{q}%", f"%{q}%"))
        for r in cursor.fetchall():
            stock = r[5]
            min_alert = r[6]
            if stock is not None and min_alert is not None and stock <= min_alert:
                tree.insert("", "end", values=r, tags=("low_stock",))
            else:
                tree.insert("", "end", values=r)
        conn.close()

    btn_search = CanvasButton(search_frame, text="🔍 Search", command=filter_inventory, bg_color="#0d6efd", fg_color="#ffffff", width=90, height=28)
    btn_search.pack(side="left", padx=5)

    btn_reset = CanvasButton(search_frame, text="🔄 Reset Table", command=refresh_table, bg_color="#6c757d", fg_color="#ffffff", width=100, height=28)
    btn_reset.pack(side="left", padx=5)

    table_frame = tk.Frame(root, bg="#ffffff")
    table_frame.pack(fill="both", expand=True, padx=15, pady=10)

    columns = ("ID", "Name", "Category", "Buying Price", "Price", "Stock", "Min Alert", "Batch")
    tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

    for col in columns:
        tree.heading(col, text=col)
        tree.column(col, anchor="center", width=120)

    tree.bind("<Double-1>", open_edit_product_window)

    scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    tree.pack(fill="both", expand=True)

    refresh_table()
    check_low_stock_alerts()
    root.mainloop()

def login_screen():
    login_win = tk.Tk()
    login_win.title("POS System Login")
    login_win.geometry("360x320")
    login_win.config(bg="#f4f6f9")
    login_win.eval('tk::PlaceWindow . center')

    tk.Label(login_win, text="Enterprise POS System", bg="#f4f6f9", fg="#212529", font=("Helvetica", 14, "bold")).pack(pady=20)

    tk.Label(login_win, text="Username:", bg="#f4f6f9", fg="#212529", font=("Helvetica", 9, "bold")).pack()
    c_u, user_entry = create_styled_entry(login_win, width=20)
    c_u.pack(pady=5)

    tk.Label(login_win, text="Password:", bg="#f4f6f9", fg="#212529", font=("Helvetica", 9, "bold")).pack()
    c_p, pass_entry = create_styled_entry(login_win, width=20, show="*")
    c_p.pack(pady=5)

    def authenticate():
        u = user_entry.get().strip()
        p = pass_entry.get().strip()
        hashed_p = hash_password(p)

        conn = sqlite3.connect("general_inventory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT username, role FROM users WHERE username = ? AND password = ?", (u, hashed_p))
        user = cursor.fetchone()
        conn.close()

        if user:
            log_action(user[0], "User logged in")
            login_win.destroy()
            main_app(user[0], user[1])
        else:
            messagebox.showerror("Login Failed", "Invalid Username or Password!")

    btn_login = CanvasButton(login_win, text="Login", command=authenticate, bg_color="#0d6efd", fg_color="#ffffff", width=120, height=32)
    btn_login.pack(pady=20)

    login_win.mainloop()

if __name__ == "__main__":
    login_screen()
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from reportlab.pdfgen import canvas
from datetime import datetime

def create_table():
    conn = sqlite3.connect('general_inventory.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS products
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     name TEXT, category TEXT, price REAL, stock_count INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS suppliers
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     s_name TEXT, s_contact TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS users
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     username TEXT UNIQUE, password TEXT, role TEXT)''')
    
    # Check and add 'role' column dynamically if missing
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'role' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'Admin'")

    # Default users setup
    cursor.execute("SELECT COUNT(*) FROM users WHERE username='admin'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("admin", "1234", "Admin"))
    else:
        cursor.execute("UPDATE users SET role='Admin' WHERE username='admin'")

    cursor.execute("SELECT COUNT(*) FROM users WHERE username='staff'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", ("staff", "1234", "Staff"))
        
    conn.commit()
    conn.close()

create_table()

def login():
    entered_user = user_entry.get().strip()
    entered_pass = pass_entry.get().strip()
    
    conn = sqlite3.connect('general_inventory.db')
    cursor = conn.cursor()
    cursor.execute("SELECT username, role FROM users WHERE username=? AND password=?", (entered_user, entered_pass))
    user = cursor.fetchone()
    conn.close()

    if user:
        current_username, role = user[0], user[1]
        login_window.destroy()
        main_app(current_username, role)
    else:
        messagebox.showerror("Error", "Invalid Credentials")

def main_app(current_username, user_role):
    global root, nav_bar, input_frame, tree, name_entry, category_entry, price_entry, stock_entry
    global add_btn, delete_btn, edit_btn, search_entry, card_p, card_v, card_l, search_frame, search_label
    
    root = tk.Tk()
    root.title(f"Ratul's Enterprise Inventory v7.0 [{user_role} View]")
    root.geometry("1150x950")
    root.config(bg="#2c3e50")

    def toggle_theme():
        current_bg = root.cget("bg")
        if current_bg == "#2c3e50":
            root.config(bg="#ecf0f1")
            nav_bar.config(bg="#bdc3c7")
            input_frame.config(bg="#ecf0f1", fg="black")
            search_frame.config(bg="#ecf0f1")
            search_label.config(bg="#ecf0f1", fg="black")
            if user_role == "Admin":
                add_btn.config(fg="black")
                edit_btn.config(fg="black")
                delete_btn.config(fg="black")
        else:
            root.config(bg="#2c3e50")
            nav_bar.config(bg="#34495e")
            input_frame.config(bg="#34495e", fg="white")
            search_frame.config(bg="#2c3e50")
            search_label.config(bg="#2c3e50", fg="white")
            if user_role == "Admin":
                add_btn.config(fg="black")
                edit_btn.config(fg="black")
                delete_btn.config(fg="black")

    def generate_bill_pdf():
        bill_win = tk.Toplevel(root)
        bill_win.title("Create Multi-Item Billing Receipt")
        bill_win.geometry("650x650")
        bill_win.config(bg="#34495e")

        cart = []  # Cart items: [{'id', 'name', 'price', 'qty', 'total'}]

        info_frame = tk.Frame(bill_win, bg="#34495e")
        info_frame.pack(pady=10)

        tk.Label(info_frame, text="Customer Name:", bg="#34495e", fg="white", font=("Helvetica", 10, "bold")).grid(row=0, column=0, padx=5)
        c_entry = tk.Entry(info_frame, width=25)
        c_entry.grid(row=0, column=1, padx=5)

        sel_frame = tk.LabelFrame(bill_win, text=" Add Items to Cart ", bg="#2c3e50", fg="white", padx=10, pady=10)
        sel_frame.pack(pady=5, fill="x", padx=15)

        tk.Label(sel_frame, text="Select Product:", bg="#2c3e50", fg="white").grid(row=0, column=0, padx=5)
        
        conn = sqlite3.connect('general_inventory.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, price, stock_count FROM products")
        db_products = cursor.fetchall()
        conn.close()

        prod_dict = {f"{p[1]} (Stock: {p[3]}, Price: ${p[2]})": p for p in db_products}
        prod_options = list(prod_dict.keys())

        prod_combo = ttk.Combobox(sel_frame, values=prod_options, state="readonly", width=35)
        prod_combo.grid(row=0, column=1, padx=5)

        tk.Label(sel_frame, text="Qty:", bg="#2c3e50", fg="white").grid(row=0, column=2, padx=5)
        qty_entry = tk.Entry(sel_frame, width=8)
        qty_entry.grid(row=0, column=3, padx=5)

        cart_columns = ('ID', 'Product Name', 'Price', 'Qty', 'Total')
        cart_tree = ttk.Treeview(bill_win, columns=cart_columns, show='headings', height=8)
        for col in cart_columns:
            cart_tree.heading(col, text=col)
            cart_tree.column(col, width=100, anchor="center")
        cart_tree.pack(pady=10, fill="both", expand=True, padx=15)

        total_label = tk.Label(bill_win, text="Grand Total: $0.00", bg="#34495e", fg="#2ecc71", font=("Helvetica", 12, "bold"))
        total_label.pack(pady=5)

        def update_cart_ui():
            for item in cart_tree.get_children():
                cart_tree.delete(item)
            grand_total = 0.0
            for item in cart:
                cart_tree.insert('', tk.END, values=(item['id'], item['name'], f"${item['price']:.2f}", item['qty'], f"${item['total']:.2f}"))
                grand_total += item['total']
            total_label.config(text=f"Grand Total: ${grand_total:.2f}")

        def add_to_cart():
            selected_str = prod_combo.get()
            if not selected_str:
                messagebox.showwarning("Warning", "Please select a product!", parent=bill_win)
                return
            
            try:
                qty = int(qty_entry.get().strip())
                if qty <= 0:
                    messagebox.showerror("Error", "Quantity must be greater than 0!", parent=bill_win)
                    return
            except ValueError:
                messagebox.showerror("Error", "Please enter a valid numeric quantity!", parent=bill_win)
                return

            p_data = prod_dict[selected_str]
            p_id, p_name, p_price, current_stock = p_data[0], p_data[1], float(p_data[2]), int(p_data[3])

            existing_qty_in_cart = sum(item['qty'] for item in cart if item['id'] == p_id)
            if qty + existing_qty_in_cart > current_stock:
                messagebox.showerror("Error", f"Insufficient stock! Available: {current_stock - existing_qty_in_cart}", parent=bill_win)
                return

            for item in cart:
                if item['id'] == p_id:
                    item['qty'] += qty
                    item['total'] = item['qty'] * item['price']
                    update_cart_ui()
                    qty_entry.delete(0, tk.END)
                    return

            cart.append({
                'id': p_id,
                'name': p_name,
                'price': p_price,
                'qty': qty,
                'total': p_price * qty
            })
            update_cart_ui()
            qty_entry.delete(0, tk.END)

        def remove_from_cart():
            selected = cart_tree.selection()
            if not selected:
                messagebox.showwarning("Warning", "Select an item from cart to remove!", parent=bill_win)
                return
            item_values = cart_tree.item(selected)['values']
            p_id = item_values[0]
            
            nonlocal cart
            cart = [i for i in cart if i['id'] != p_id]
            update_cart_ui()

        btn_add = tk.Button(sel_frame, text="➕ Add to Cart", command=add_to_cart, bg="#27ae60", fg="black")
        btn_add.grid(row=0, column=4, padx=5)

        tk.Button(bill_win, text="❌ Remove Selected Item", command=remove_from_cart, bg="#e74c3c", fg="black").pack(pady=2)

        def print_pdf_and_update():
            c_name = c_entry.get().strip()
            if not c_name:
                messagebox.showwarning("Warning", "Please enter Customer Name!", parent=bill_win)
                return
            if not cart:
                messagebox.showwarning("Warning", "Cart is empty! Add products first.", parent=bill_win)
                return

            try:
                conn = sqlite3.connect('general_inventory.db')
                cursor = conn.cursor()
                for item in cart:
                    cursor.execute("UPDATE products SET stock_count = stock_count - ? WHERE id = ?", (item['qty'], item['id']))
                conn.commit()
                conn.close()

                filename = f"Receipt_{c_name.replace(' ', '_')}.pdf"
                c = canvas.Canvas(filename)
                
                c.setFont("Helvetica-Bold", 18)
                c.drawString(180, 800, "RATUL'S SMART INVENTORY")
                c.setFont("Helvetica", 10)
                c.drawString(50, 770, f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                c.drawString(50, 755, f"Customer: {c_name}")
                c.line(50, 745, 550, 745)

                c.setFont("Helvetica-Bold", 10)
                c.drawString(50, 730, "Item Name")
                c.drawString(280, 730, "Price")
                c.drawString(380, 730, "Qty")
                c.drawString(480, 730, "Total")
                c.line(50, 720, 550, 720)

                y = 700
                c.setFont("Helvetica", 10)
                grand_total = 0.0
                for item in cart:
                    c.drawString(50, y, str(item['name']))
                    c.drawString(280, y, f"${item['price']:.2f}")
                    c.drawString(380, y, str(item['qty']))
                    c.drawString(480, y, f"${item['total']:.2f}")
                    grand_total += item['total']
                    y -= 20
                    if y < 100:
                        c.showPage()
                        y = 750

                c.line(50, y, 550, y)
                c.setFont("Helvetica-Bold", 12)
                c.drawString(380, y - 25, f"Grand Total: ${grand_total:.2f}")

                c.save()

                messagebox.showinfo("Success", f"Bill Generated Successfully!\nSaved as: {filename}", parent=bill_win)
                refresh_table()
                bill_win.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to generate bill: {str(e)}", parent=bill_win)

        tk.Button(bill_win, text="🧾 Generate & Save Bill PDF", command=print_pdf_and_update, bg="#e67e22", fg="black", font=("Helvetica", 11, "bold"), pady=5).pack(pady=15)

    def open_suppliers():
        s_win = tk.Toplevel(root)
        s_win.title("Suppliers Management")
        s_win.geometry("600x600")
        s_win.config(bg="#34495e")

        s_frame = tk.Frame(s_win, bg="#34495e")
        s_frame.pack(pady=10)

        tk.Label(s_frame, text="Supplier Name:", bg="#34495e", fg="white").grid(row=0, column=0, padx=5, pady=5)
        sn = tk.Entry(s_frame)
        sn.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(s_frame, text="Contact:", bg="#34495e", fg="white").grid(row=1, column=0, padx=5, pady=5)
        sc = tk.Entry(s_frame)
        sc.grid(row=1, column=1, padx=5, pady=5)

        btn_frame = tk.Frame(s_win, bg="#34495e")
        btn_frame.pack(pady=10)

        def refresh_suppliers():
            for i in s_tree.get_children():
                s_tree.delete(i)
            conn = sqlite3.connect('general_inventory.db')
            for row in conn.execute("SELECT * FROM suppliers"):
                s_tree.insert('', tk.END, values=row)
            conn.close()

        def add_s():
            if not sn.get().strip() or not sc.get().strip():
                messagebox.showwarning("Warning", "Please fill all fields!", parent=s_win)
                return
            conn = sqlite3.connect('general_inventory.db')
            conn.execute("INSERT INTO suppliers (s_name, s_contact) VALUES (?,?)", (sn.get().strip(), sc.get().strip()))
            conn.commit()
            conn.close()
            messagebox.showinfo("Success", "Supplier Added Successfully!", parent=s_win)
            sn.delete(0, tk.END)
            sc.delete(0, tk.END)
            refresh_suppliers()

        def delete_s():
            if user_role != "Admin":
                messagebox.showerror("Access Denied", "Only Admin can delete suppliers!", parent=s_win)
                return
            selected = s_tree.selection()
            if not selected:
                messagebox.showwarning("Warning", "Please select a supplier to delete!", parent=s_win)
                return
            item = s_tree.item(selected)['values']
            supplier_id = item[0]

            conn = sqlite3.connect('general_inventory.db')
            cursor = conn.cursor()
            cursor.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))

            cursor.execute("SELECT id, s_name, s_contact FROM suppliers ORDER BY id")
            rows = cursor.fetchall()
            cursor.execute("DELETE FROM suppliers")
            cursor.execute("DELETE FROM sqlite_sequence WHERE name='suppliers'")
            for row in rows:
                cursor.execute("INSERT INTO suppliers (s_name, s_contact) VALUES (?, ?)", (row[1], row[2]))

            conn.commit()
            conn.close()
            refresh_suppliers()
            messagebox.showinfo("Success", "Supplier deleted successfully!", parent=s_win)

        tk.Button(btn_frame, text="➕ Add Supplier", command=add_s, bg="#27ae60", fg="black", width=15).pack(side="left", padx=5)
        if user_role == "Admin":
            tk.Button(btn_frame, text="❌ Delete Selected", command=delete_s, bg="#e74c3c", fg="black", width=15).pack(side="left", padx=5)

        s_columns = ('ID', 'Supplier Name', 'Contact')
        s_tree = ttk.Treeview(s_win, columns=s_columns, show='headings', height=10)
        for col in s_columns:
            s_tree.heading(col, text=col)
            s_tree.column(col, width=150, anchor="center")
        s_tree.pack(pady=10, fill="both", expand=True, padx=15)

        refresh_suppliers()

    def show_analysis():
        conn = sqlite3.connect('general_inventory.db')
        df = pd.read_sql_query("SELECT name, stock_count FROM products", conn)
        conn.close()
        if not df.empty:
            graph_win = tk.Toplevel(root)
            graph_win.title("Stock Level Analysis")
            graph_win.geometry("700x500")
            
            fig, ax = plt.subplots(figsize=(6.5, 4.5))
            ax.bar(df['name'], df['stock_count'], color='#3498db')
            
            ax.set_xticks(range(len(df['name'])))
            ax.set_xticklabels(df['name'], rotation=45, ha='right', fontsize=9)
            ax.set_ylabel("Stock Quantity")
            ax.set_title("Product Stock Levels")
            
            fig.tight_layout()
            
            canvas_plot = FigureCanvasTkAgg(fig, master=graph_win)
            canvas_plot.draw()
            canvas_plot.get_tk_widget().pack(fill="both", expand=True)

    def export_to_csv():
        conn = sqlite3.connect('general_inventory.db')
        df = pd.read_sql_query("SELECT * FROM products", conn)
        conn.close()
        if df.empty:
            messagebox.showwarning("Warning", "No product data available to export!")
            return
        
        file_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if file_path:
            df.to_csv(file_path, index=False)
            messagebox.showinfo("Success", f"Inventory exported successfully to:\n{file_path}")

    def change_credentials_window():
        if user_role != "Admin":
            messagebox.showerror("Access Denied", "Only Admin can access settings!")
            return
        cred_win = tk.Toplevel(root)
        cred_win.title("Change Settings")
        cred_win.geometry("350x250")
        cred_win.config(bg="#34495e")

        tk.Label(cred_win, text="New Username:", bg="#34495e", fg="white").pack(pady=5)
        new_u_entry = tk.Entry(cred_win)
        new_u_entry.pack(pady=5)
        new_u_entry.insert(0, current_username)

        tk.Label(cred_win, text="New Password:", bg="#34495e", fg="white").pack(pady=5)
        new_p_entry = tk.Entry(cred_win, show="*")
        new_p_entry.pack(pady=5)

        def update_credentials():
            u_val = new_u_entry.get().strip()
            p_val = new_p_entry.get().strip()

            if not u_val or not p_val:
                messagebox.showwarning("Warning", "Username and Password cannot be empty!", parent=cred_win)
                return

            conn = sqlite3.connect('general_inventory.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET username=?, password=? WHERE username=?", (u_val, p_val, current_username))
            conn.commit()
            conn.close()

            messagebox.showinfo("Success", "Credentials updated successfully!", parent=cred_win)
            cred_win.destroy()

        tk.Button(cred_win, text="Save Changes", command=update_credentials, bg="#27ae60", fg="black").pack(pady=15)

    # Navigation Bar
    nav_bar = tk.Frame(root, bg="#34495e", height=50)
    nav_bar.pack(fill="x")

    tk.Button(nav_bar, text="📊 Analysis", command=show_analysis).pack(side="left", padx=10, pady=10)
    tk.Button(nav_bar, text="🚚 Suppliers", command=open_suppliers).pack(side="left", padx=10)
    tk.Button(nav_bar, text="🧾 Create Bill", command=generate_bill_pdf).pack(side="left", padx=10)
    tk.Button(nav_bar, text="📁 Export CSV", command=export_to_csv).pack(side="left", padx=10)
    
    if user_role == "Admin":
        tk.Button(nav_bar, text="⚙️ Settings", command=change_credentials_window).pack(side="left", padx=10)
        
    tk.Button(nav_bar, text="🌓 Theme", command=toggle_theme).pack(side="right", padx=10)

    # Summary Cards Frame
    cards_frame = tk.Frame(root, bg="#2c3e50")
    cards_frame.pack(pady=10)

    c1 = tk.Frame(cards_frame, bg="#2980b9", width=220, height=70)
    c1.pack_propagate(False)
    c1.grid(row=0, column=0, padx=15)
    card_p = tk.Label(c1, text="Total Products\n0", bg="#2980b9", fg="white", font=("Helvetica", 11, "bold"))
    card_p.pack(expand=True)

    c2 = tk.Frame(cards_frame, bg="#27ae60", width=220, height=70)
    c2.pack_propagate(False)
    c2.grid(row=0, column=1, padx=15)
    card_v = tk.Label(c2, text="Stock Value\n$0.00", bg="#27ae60", fg="white", font=("Helvetica", 11, "bold"))
    card_v.pack(expand=True)

    c3 = tk.Frame(cards_frame, bg="#c0392b", width=220, height=70)
    c3.pack_propagate(False)
    c3.grid(row=0, column=2, padx=15)
    card_l = tk.Label(c3, text="Low Stock Items\n0", bg="#c0392b", fg="white", font=("Helvetica", 11, "bold"))
    card_l.pack(expand=True)

    # Manage Inventory Frame
    input_frame = tk.LabelFrame(root, text=f" Manage Inventory ({user_role} View) ", bg="#34495e", fg="white", padx=20, pady=10)
    input_frame.pack(pady=10, fill="x", padx=20)

    tk.Label(input_frame, text="Name:", bg="#34495e", fg="white").grid(row=0, column=0)
    name_entry = tk.Entry(input_frame)
    name_entry.grid(row=0, column=1, padx=5)

    tk.Label(input_frame, text="Stock:", bg="#34495e", fg="white").grid(row=0, column=2)
    stock_entry = tk.Entry(input_frame)
    stock_entry.grid(row=0, column=3, padx=5)

    tk.Label(input_frame, text="Price:", bg="#34495e", fg="white").grid(row=1, column=0)
    price_entry = tk.Entry(input_frame)
    price_entry.grid(row=1, column=1, padx=5)

    tk.Label(input_frame, text="Category:", bg="#34495e", fg="white").grid(row=1, column=2)
    category_entry = tk.Entry(input_frame)
    category_entry.grid(row=1, column=3, padx=5)

    def update_summary_cards():
        conn = sqlite3.connect('general_inventory.db')
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), SUM(price * stock_count) FROM products")
        total_p, total_v = cursor.fetchone()
        
        cursor.execute("SELECT COUNT(*) FROM products WHERE stock_count <= 5")
        low_s = cursor.fetchone()[0]
        conn.close()

        total_p = total_p if total_p else 0
        total_v = total_v if total_v else 0.0

        card_p.config(text=f"Total Products\n{total_p}")
        card_v.config(text=f"Stock Value\n${total_v:.2f}")
        card_l.config(text=f"Low Stock Items\n{low_s}")

    def refresh_table(search_query=""):
        for i in tree.get_children():
            tree.delete(i)
        conn = sqlite3.connect('general_inventory.db')
        cursor = conn.cursor()
        
        if search_query:
            query = "SELECT * FROM products WHERE name LIKE ? OR category LIKE ?"
            cursor.execute(query, (f"%{search_query}%", f"%{search_query}%"))
        else:
            cursor.execute("SELECT * FROM products ORDER BY id")
            
        for row in cursor.fetchall():
            stock_count = int(row[4])
            if stock_count <= 5:
                tree.insert('', tk.END, values=row, tags=('low_stock',))
            else:
                tree.insert('', tk.END, values=row)
        conn.close()
        update_summary_cards()

    def on_search(event):
        refresh_table(search_entry.get().strip())

    def add_product():
        if user_role != "Admin":
            messagebox.showerror("Access Denied", "Only Admin can add products!")
            return
        try:
            conn = sqlite3.connect('general_inventory.db')
            conn.execute("INSERT INTO products (name, category, price, stock_count) VALUES (?,?,?,?)",
                         (name_entry.get(), category_entry.get(), float(price_entry.get()), int(stock_entry.get())))
            conn.commit()
            conn.close()
            
            name_entry.delete(0, tk.END)
            category_entry.delete(0, tk.END)
            price_entry.delete(0, tk.END)
            stock_entry.delete(0, tk.END)
            
            refresh_table()
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric values for Price and Stock!")

    def edit_product():
        if user_role != "Admin":
            messagebox.showerror("Access Denied", "Only Admin can edit products!")
            return
        selected = tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Please select a product from the table to edit!")
            return
        
        item = tree.item(selected)['values']
        p_id = item[0]

        edit_win = tk.Toplevel(root)
        edit_win.title("Edit Product")
        edit_win.geometry("300x250")
        edit_win.config(bg="#34495e")

        tk.Label(edit_win, text="Name:", bg="#34495e", fg="white").pack()
        e_name = tk.Entry(edit_win)
        e_name.pack()
        e_name.insert(0, item[1])

        tk.Label(edit_win, text="Category:", bg="#34495e", fg="white").pack()
        e_cat = tk.Entry(edit_win)
        e_cat.pack()
        e_cat.insert(0, item[2])

        tk.Label(edit_win, text="Price:", bg="#34495e", fg="white").pack()
        e_price = tk.Entry(edit_win)
        e_price.pack()
        e_price.insert(0, item[3])

        tk.Label(edit_win, text="Stock:", bg="#34495e", fg="white").pack()
        e_stock = tk.Entry(edit_win)
        e_stock.pack()
        e_stock.insert(0, item[4])

        def save_updates():
            try:
                conn = sqlite3.connect('general_inventory.db')
                cursor = conn.cursor()
                cursor.execute("UPDATE products SET name=?, category=?, price=?, stock_count=? WHERE id=?",
                               (e_name.get(), e_cat.get(), float(e_price.get()), int(e_stock.get()), p_id))
                conn.commit()
                conn.close()
                messagebox.showinfo("Success", "Product updated successfully!", parent=edit_win)
                refresh_table()
                edit_win.destroy()
            except ValueError:
                messagebox.showerror("Error", "Invalid Price or Stock input!", parent=edit_win)

        tk.Button(edit_win, text="Save Updates", command=save_updates, bg="#27ae60", fg="black").pack(pady=10)

    def delete_product():
        if user_role != "Admin":
            messagebox.showerror("Access Denied", "Only Admin can delete products!")
            return
        selected = tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Please select a product to delete!")
            return
        item = tree.item(selected)['values']
        product_id = item[0]
        
        conn = sqlite3.connect('general_inventory.db')
        cursor = conn.cursor()
        
        # Delete item
        cursor.execute("DELETE FROM products WHERE id=?", (product_id,))
        
        # Re-sequence IDs (1, 2, 3...)
        cursor.execute("SELECT id, name, category, price, stock_count FROM products ORDER BY id")
        rows = cursor.fetchall()
        
        cursor.execute("DELETE FROM products")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='products'")
        
        for row in rows:
            cursor.execute("INSERT INTO products (name, category, price, stock_count) VALUES (?, ?, ?, ?)",
                           (row[1], row[2], row[3], row[4]))
            
        conn.commit()
        conn.close()
        refresh_table()
        messagebox.showinfo("Success", "Product deleted and IDs re-sequenced successfully!")

    btn_bar = tk.Frame(root, bg="#2c3e50")
    btn_bar.pack(pady=5)

    if user_role == "Admin":
        add_btn = tk.Button(btn_bar, text="➕ Add Product", command=add_product, bg="#27ae60", fg="black", width=15)
        add_btn.pack(side="left", padx=5)

        edit_btn = tk.Button(btn_bar, text="✏️ Edit Product", command=edit_product, bg="#f39c12", fg="black", width=15)
        edit_btn.pack(side="left", padx=5)

        delete_btn = tk.Button(btn_bar, text="❌ Delete Selected", command=delete_product, bg="#e74c3c", fg="black", width=15)
        delete_btn.pack(side="left", padx=5)

    search_frame = tk.Frame(root, bg="#2c3e50")
    search_frame.pack(pady=10)

    search_label = tk.Label(search_frame, text="🔍 Search Product/Category:", bg="#2c3e50", fg="white", font=("Helvetica", 10, "bold"))
    search_label.pack(side="left", padx=5)

    search_entry = tk.Entry(search_frame, width=30)
    search_entry.pack(side="left", padx=5)
    search_entry.bind("<KeyRelease>", on_search)

    columns = ('ID', 'Name', 'Category', 'Price', 'Stock')
    tree = ttk.Treeview(root, columns=columns, show='headings')
    for col in columns:
        tree.heading(col, text=col)
        tree.column(col, width=100, anchor="center")
    tree.pack(pady=10, fill="both", expand=True, padx=20)

    tree.tag_configure('low_stock', background='#ffcccc', foreground='red')

    refresh_table()

    # Startup Low Stock Alert
    conn = sqlite3.connect('general_inventory.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name, stock_count FROM products WHERE stock_count <= 5")
    low_items = cursor.fetchall()
    conn.close()

    if low_items:
        items_str = "\n".join([f"• {item[0]} (Stock: {item[1]})" for item in low_items])
        messagebox.showwarning("Low Stock Warning", f"The following items need re-stocking urgently:\n\n{items_str}")

    root.mainloop()

# Login Window Setup
login_window = tk.Tk()
login_window.title("Login - Inventory System")
login_window.geometry("320x260")
login_window.config(bg="#2c3e50")

tk.Label(login_window, text="Username:", bg="#2c3e50", fg="white").pack(pady=5)
user_entry = tk.Entry(login_window)
user_entry.pack(pady=5)

tk.Label(login_window, text="Password:", bg="#2c3e50", fg="white").pack(pady=5)
pass_entry = tk.Entry(login_window, show="*")
pass_entry.pack(pady=5)

tk.Button(login_window, text="Login", command=login, bg="#3498db", fg="black", width=15).pack(pady=20)

login_window.mainloop()
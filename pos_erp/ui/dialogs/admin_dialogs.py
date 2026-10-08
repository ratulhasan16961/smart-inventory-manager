"""Analytics, audit log, customers, settings and the stock ledger."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox

from ... import config
from ...money import fmt
from ...services import audit, auth_service, backup_service, inventory_service, reporting_service, stock_service
from ..context import AppContext
from ..widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree,
                       handle_errors, make_tree)
from .system_dialogs import open_backup_restore, open_manage_lists, open_shop_settings, open_user_management

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


@handle_errors
def open_analytics(ctx: AppContext) -> None:
    summary = reporting_service.sales_summary(ctx.conn, ctx.session)
    categories = reporting_service.sales_by_category(ctx.conn, ctx.session)
    win = tk.Toplevel(ctx.root)
    win.title("Sales & Profit Analytics Dashboard")
    win.geometry("820x640")
    win.config(bg=WHITE)
    tk.Label(win, text="📊 Financial Analytics & Visual Reports", bg=WHITE, fg=DARK,
             font=(FONT, 14, "bold")).pack(pady=10)
    box = tk.Frame(win, bg=WHITE, bd=1, relief="solid")
    box.pack(padx=15, pady=5, fill="x")
    tk.Label(box, text=(f"Total Orders: {summary.orders}  |  Net Revenue: {fmt(summary.net_revenue_cents)}  |  "
                        f"Est. Gross Profit: {fmt(summary.est_gross_profit_cents)}"),
             bg=WHITE, fg="#198754", font=(FONT, 11, "bold")).pack(pady=10)

    if HAS_MATPLOTLIB and categories:
        figure = Figure(figsize=(6, 3.8), dpi=100)
        axes = figure.add_subplot(111)
        axes.pie([units for _, units in categories], labels=[name for name, _ in categories],
                 autopct="%1.1f%%", startangle=140,
                 colors=["#0d6efd", "#198754", "#fd7e14", "#6f42c1", "#dc3545"])
        axes.set_title("Units Sold by Category (net of returns)")
        canvas = FigureCanvasTkAgg(figure, master=win)
        canvas.draw()
        canvas.get_tk_widget().pack(pady=10)
    else:
        tk.Label(win, text="Category Wise Sales Breakdown:", bg=WHITE, font=(FONT, 11, "bold")).pack(
            anchor="w", padx=20, pady=5)
        for name, units in categories:
            tk.Label(win, text=f"• {name}: {units} units sold", bg=WHITE, font=(FONT, 10)).pack(anchor="w", padx=30)


@handle_errors
def open_audit_logs(ctx: AppContext) -> None:
    rows = audit.recent(ctx.conn, ctx.session)
    win = tk.Toplevel(ctx.root)
    win.title("System Audit Logs")
    win.geometry("900x420")
    win.config(bg=WHITE)
    box, tree = make_tree(win, ("ID", "User", "Action", "Timestamp"), (60, 110, 560, 150))
    box.pack(fill="both", expand=True, padx=10, pady=10)
    fill_tree(tree, [(r["id"], r["username"], r["action"], r["timestamp"]) for r in rows])


@handle_errors
def open_customers(ctx: AppContext) -> None:
    rows = reporting_service.customers(ctx.conn, ctx.session)
    win = tk.Toplevel(ctx.root)
    win.title("Customer Loyalty & History Database")
    win.geometry("640x380")
    win.config(bg=WHITE)
    box, tree = make_tree(win, ("ID", "Customer Name", "Phone", "Loyalty Points", "Total Spent"), (50, 190, 130, 120, 120))
    box.pack(fill="both", expand=True, padx=10, pady=10)
    fill_tree(tree, [(r["id"], r["name"], r["phone"], r["loyalty_points"], fmt(r["total_spent_cents"])) for r in rows])


def open_settings(ctx: AppContext) -> None:
    win = tk.Toplevel(ctx.root)
    win.title("Settings & Maintenance")
    win.geometry("380x680")
    win.config(bg=WHITE)
    tk.Label(win, text="Update Security Settings", bg=WHITE, fg=DARK, font=(FONT, 11, "bold")).pack(pady=8)
    tk.Label(win, text="Username:", **LABEL_STYLE).pack()
    container, user_entry = create_styled_entry(win, width=22)
    container.pack(pady=3)
    user_entry.insert(0, ctx.session.username)
    tk.Label(win, text=f"New Password (min {config.MIN_PASSWORD_LENGTH} characters):", **LABEL_STYLE).pack()
    container, pass_entry = create_styled_entry(win, width=22, show="*")
    container.pack(pady=3)

    @handle_errors
    def save_credentials() -> None:
        ctx.set_session(auth_service.update_credentials(ctx.conn, ctx.session, user_entry.get(), pass_entry.get()))
        messagebox.showinfo("Success", "Username & password updated.")
        win.destroy()

    @handle_errors
    def backup() -> None:
        path = filedialog.asksaveasfilename(defaultextension=".db", filetypes=[("SQLite DB", "*.db")])
        if path:
            backup_service.create_backup(ctx.conn, ctx.session, path)
            messagebox.showinfo("Backup Success", f"Database backed up to {path}")

    CanvasButton(win, "Save Credentials", save_credentials, "#198754", width=140, height=30).pack(pady=8)
    tk.Frame(win, height=1, bg="#cccccc").pack(fill="x", padx=15, pady=5)
    backup_button = CanvasButton(win, "💾 Backup Database", backup, "#0d6efd", width=190, height=30)
    backup_button.pack(pady=4)
    backup_button.set_enabled(ctx.can("system.backup"))
    customers_button = CanvasButton(win, "👥 Customer Base", lambda: open_customers(ctx), "#6f42c1", width=190, height=30)
    customers_button.pack(pady=4)
    customers_button.set_enabled(ctx.can("customers.view"))

    tk.Frame(win, height=1, bg="#cccccc").pack(fill="x", padx=15, pady=8)
    tk.Label(win, text="Administration", bg=WHITE, fg=DARK, font=(FONT, 11, "bold")).pack()
    for text, handler, color, permission in (
            ("🛠 Shop Settings", open_shop_settings, "#0d6efd", "settings.manage"),
            ("👤 Manage Users", open_user_management, "#6610f2", "users.manage"),
            ("💽 Backup & Restore", open_backup_restore, "#fd7e14", "system.restore"),
            ("🗂 Categories & Warehouses", open_manage_lists, "#20c997", "lists.manage")):
        button = CanvasButton(win, text, lambda h=handler: h(ctx), color, width=190, height=30)
        button.pack(pady=4)
        button.set_enabled(ctx.can(permission))


@handle_errors
def open_stock_ledger(ctx: AppContext) -> None:
    win = tk.Toplevel(ctx.root)
    win.title("Stock Ledger")
    win.geometry("1000x640")
    win.config(bg=WHITE)

    bar = tk.Frame(win, bg=WHITE)
    bar.pack(fill="x", padx=15, pady=10)
    tk.Label(bar, text="Filter (product name or ID):", **LABEL_STYLE).pack(side="left", padx=5)
    container, filter_entry = create_styled_entry(bar, width=22)
    container.pack(side="left", padx=5)
    box, tree = make_tree(win, ("When", "Product", "Change", "Balance", "Reason", "Note", "User"),
                          (150, 220, 80, 80, 110, 250, 90))

    @handle_errors
    def load() -> None:
        rows = stock_service.list_movements(ctx.conn, ctx.session, filter_entry.get())
        fill_tree(tree, [(r["created_at"], f"{r['product_name']} (#{r['product_id']})", f"{r['qty_change']:+d}",
                          r["stock_after"], r["reason"], r["note"], r["username"]) for r in rows])

    @handle_errors
    def check() -> None:
        problems = stock_service.reconcile(ctx.conn, ctx.session)
        if not problems:
            messagebox.showinfo("Ledger OK", "Every product's stock equals the sum of its ledger movements.")
            return
        lines = "\n".join(f"• {p['name']} (#{p['id']}): recorded {p['recorded']}, ledger {p['ledger']}"
                          for p in problems[:10])
        messagebox.showerror("Ledger mismatch", f"These products do not match their ledger:\n\n{lines}")

    filter_entry.bind("<Return>", lambda _e: load())
    CanvasButton(bar, "🔍 Search", load, "#0d6efd", width=90, height=28).pack(side="left", padx=5)
    CanvasButton(bar, "✔ Check ledger integrity", check, "#198754", width=180, height=28).pack(side="left", padx=5)
    box.pack(fill="both", expand=True, padx=15, pady=5)

    adjust = tk.LabelFrame(win, text=" Manual stock adjustment (stock-take, damage, correction) ", bg=WHITE, fg=DARK,
                           font=(FONT, 10, "bold"), bd=1, relief="solid")
    adjust.pack(fill="x", padx=15, pady=10)
    entries = {}
    for column, (label, width) in enumerate((("Product ID", 8), ("Change (+/-)", 8), ("Reason", 34))):
        tk.Label(adjust, text=f"{label}:", **LABEL_STYLE).grid(row=0, column=column * 2, padx=6, pady=8, sticky="e")
        container, entry = create_styled_entry(adjust, width=width)
        container.grid(row=0, column=column * 2 + 1, padx=4, pady=8)
        entries[label] = entry

    @handle_errors
    def apply_adjustment() -> None:
        try:
            product_id = int(entries["Product ID"].get().strip())
            delta = int(entries["Change (+/-)"].get().strip())
        except ValueError:
            messagebox.showerror("Error", "Product ID and Change must be whole numbers (e.g. 5 or -3)!")
            return
        new_stock = inventory_service.adjust_stock(ctx.conn, ctx.session, product_id, delta, entries["Reason"].get())
        messagebox.showinfo("Stock adjusted", f"Stock is now {new_stock}.")
        for entry in entries.values():
            entry.delete(0, tk.END)
        load()
        ctx.refresh()

    apply_button = CanvasButton(adjust, "Apply", apply_adjustment, "#fd7e14", width=90, height=28)
    apply_button.grid(row=0, column=6, padx=8)
    apply_button.set_enabled(ctx.can("stock.adjust"))
    load()
"""Shop settings, user management, backup & restore, and managed lists (categories / warehouses)."""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from ... import config
from ...services import backup_service, lists_service, settings_service, user_service
from ..context import AppContext
from ..widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree,
                       handle_errors, make_tree)


def _form_dialog(parent, title, fields, submit_text, on_submit, choices=None, initial=None):
    """Small pop-up form. ``fields``: (key, label, kind) with kind 'text', 'secret' or 'choice'.
    ``on_submit(values)`` runs inside the error handler; the window closes only if it succeeds."""
    initial = initial or {}
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry(f"430x{130 + 48 * len(fields)}")
    win.config(bg=WHITE)
    widgets = {}
    for row, (key, label, kind) in enumerate(fields):
        tk.Label(win, text=f"{label}:", **LABEL_STYLE).grid(row=row, column=0, padx=10, pady=8, sticky="e")
        if kind == "choice":
            options = list(choices[key])
            widget = ttk.Combobox(win, values=options, state="readonly", width=24)
            widget.grid(row=row, column=1, padx=10, pady=8)
            widget.set(initial.get(key, options[0]))
        else:
            container, widget = create_styled_entry(win, width=28, show="*" if kind == "secret" else None)
            container.grid(row=row, column=1, padx=10, pady=8)
            widget.insert(0, initial.get(key, ""))
        widgets[key] = widget

    @handle_errors
    def submit() -> None:
        on_submit({key: widget.get() for key, widget in widgets.items()})
        win.destroy()

    CanvasButton(win, submit_text, submit, "#198754", width=160, height=32).grid(
        row=len(fields), column=0, columnspan=2, pady=14)
    return win


# --------------------------------------------------------------------------- shop settings
@handle_errors
def open_shop_settings(ctx: AppContext) -> None:
    current = settings_service.get_all(ctx.conn)
    fields = [("shop_name", "Shop name", "text"), ("currency_symbol", "Currency symbol", "text"),
              ("default_tax_rate", "Default tax (%)", "text"), ("receipt_footer", "Receipt footer", "text")]

    def save(values: dict) -> None:
        settings_service.update(ctx.conn, ctx.session, values)
        messagebox.showinfo("Saved", "Settings saved.\nThe default tax appears on the POS screen "
                                     "the next time the app starts.")
        ctx.refresh()

    _form_dialog(ctx.root, "Shop Settings", fields, "Save settings", save, initial=current)


# --------------------------------------------------------------------------- users
@handle_errors
def open_user_management(ctx: AppContext) -> None:
    user_service.list_users(ctx.conn, ctx.session)  # fails early with a clear message if not allowed
    win = tk.Toplevel(ctx.root)
    win.title("User Management")
    win.geometry("860x520")
    win.config(bg=WHITE)
    tk.Label(win, text="New users get a temporary password and must choose their own at first login.",
             bg=WHITE, fg=DARK, font=(FONT, 10)).pack(pady=(10, 0))
    bar = tk.Frame(win, bg=WHITE)
    bar.pack(fill="x", padx=15, pady=10)
    box, tree = make_tree(win, ("ID", "Username", "Role", "Active", "Temp password", "Locked until"),
                          (50, 190, 100, 70, 120, 170), height=12)
    box.pack(fill="both", expand=True, padx=15, pady=(0, 15))

    def load() -> None:
        rows = user_service.list_users(ctx.conn, ctx.session)
        fill_tree(tree, [(r["id"], r["username"], r["role"], "yes" if r["is_active"] else "no",
                          "yes" if r["must_change_password"] else "", r["locked_until"] or "") for r in rows])

    def selected():
        picked = tree.selection()
        if not picked:
            messagebox.showwarning("Warning", "Select a user first!")
            return None
        return tree.item(picked[0], "values")

    def add_user() -> None:
        def create(values: dict) -> None:
            user_service.create_user(ctx.conn, ctx.session, values["username"], values["role"], values["password"])
            messagebox.showinfo("User created", "The user must choose a new password at first login.")
            load()

        _form_dialog(win, "Add User", [("username", "Username", "text"), ("role", "Role", "choice"),
                                       ("password", "Temporary password", "secret")],
                     "Create user", create, choices={"role": list(user_service.ROLES)},
                     initial={"role": config.ROLE_CASHIER})

    def reset_password() -> None:
        row = selected()
        if row is None:
            return
        user_id, name = int(row[0]), str(row[1])

        def reset(values: dict) -> None:
            user_service.reset_password(ctx.conn, ctx.session, user_id, values["password"])
            messagebox.showinfo("Password reset", f"{name} must choose a new password at the next login.")
            load()

        _form_dialog(win, f"Reset password: {name}", [("password", "Temporary password", "secret")],
                     "Reset password", reset)

    @handle_errors
    def toggle_active() -> None:
        row = selected()
        if row is None:
            return
        user_id, name, active = int(row[0]), str(row[1]), row[3] == "yes"
        action = "Disable" if active else "Enable"
        if messagebox.askyesno(f"{action} user", f"{action} {name}?"):
            user_service.set_active(ctx.conn, ctx.session, user_id, not active)
            load()

    def change_role() -> None:
        row = selected()
        if row is None:
            return
        user_id, name = int(row[0]), str(row[1])

        def apply_role(values: dict) -> None:
            user_service.set_role(ctx.conn, ctx.session, user_id, values["role"])
            load()

        _form_dialog(win, f"Change role: {name}", [("role", "Role", "choice")], "Save role", apply_role,
                     choices={"role": list(user_service.ROLES)}, initial={"role": str(row[2])})

    for text, handler, color in (("➕ Add User", add_user, "#198754"), ("🔑 Reset Password", reset_password, "#0d6efd"),
                                 ("⏯ Enable / Disable", toggle_active, "#fd7e14"),
                                 ("🎭 Change Role", change_role, "#6f42c1"), ("↻ Refresh", load, "#6c757d")):
        CanvasButton(bar, text, handler, color, width=140, height=30).pack(side="left", padx=4)
    load()


# --------------------------------------------------------------------------- backup & restore
@handle_errors
def open_backup_restore(ctx: AppContext) -> None:
    ctx.session.require("system.restore")
    win = tk.Toplevel(ctx.root)
    win.title("Backup & Restore")
    win.geometry("860x560")
    win.config(bg=WHITE)
    tk.Label(win, text="Restoring replaces ALL current data with the chosen backup.\n"
                       "A safety copy of the current database is saved first.",
             bg=WHITE, fg="#842029", font=(FONT, 10, "bold")).pack(pady=10)
    bar = tk.Frame(win, bg=WHITE)
    bar.pack(fill="x", padx=15, pady=5)
    box, tree = make_tree(win, ("File", "Size", "Modified"), (430, 100, 180), height=10)
    box.pack(fill="both", expand=True, padx=15, pady=10)
    paths: dict = {}

    def load() -> None:
        paths.clear()
        rows = []
        for path in backup_service.list_backups(config.BACKUP_DIR):
            info = path.stat()
            paths[path.name] = path
            rows.append((path.name, f"{info.st_size / 1024:.0f} KB",
                         datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")))
        fill_tree(tree, rows)

    @handle_errors
    def create_now() -> None:
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(
            initialdir=str(config.BACKUP_DIR), initialfile=f"backup_{datetime.now():%Y%m%d_%H%M%S}.db",
            defaultextension=".db", filetypes=[("SQLite DB", "*.db")])
        if path:
            backup_service.create_backup(ctx.conn, ctx.session, path)
            messagebox.showinfo("Backup Success", f"Database backed up to {path}")
            load()

    @handle_errors
    def restore(path) -> None:
        if not messagebox.askyesno(
                "Restore backup",
                f"Replace ALL current data with:\n{path.name}?\n\nA safety copy of the current database is saved "
                "first. The app closes afterwards; open it again to continue.", icon="warning"):
            return
        safety = backup_service.restore_backup(ctx.conn, ctx.session, path, config.BACKUP_DIR)
        messagebox.showinfo("Restore complete", f"Database restored.\nSafety copy: {safety.name}\n\n"
                                                "The app will now close. Please start it again.")
        ctx.conn.close()
        ctx.root.destroy()

    def restore_selected() -> None:
        picked = tree.selection()
        if not picked:
            messagebox.showwarning("Warning", "Select a backup first!")
            return
        restore(paths[str(tree.item(picked[0], "values")[0])])

    def restore_from_file() -> None:
        path = filedialog.askopenfilename(initialdir=str(config.BACKUP_DIR),
                                          filetypes=[("Database files", "*.db *.bak"), ("All files", "*.*")])
        if path:
            from pathlib import Path
            restore(Path(path))

    @handle_errors
    def health() -> None:
        report = backup_service.health_check(ctx.conn, ctx.session)
        if report.ok:
            messagebox.showinfo("Database health: OK", "Integrity check passed.\nNo broken links between tables.\n"
                                                       "Every product's stock matches its ledger.")
        else:
            messagebox.showwarning("Database health: problems found",
                                   f"Integrity: {report.integrity}\nBroken links: {report.foreign_key_problems}\n"
                                   f"Stock / ledger mismatches: {report.ledger_problems}\n\n"
                                   "Open Stock Ledger > Check ledger integrity for details.")

    for text, handler, color in (("💾 Create Backup", create_now, "#0d6efd"), ("♻ Restore Selected", restore_selected, "#dc3545"),
                                 ("📂 Restore From File...", restore_from_file, "#d63384"),
                                 ("🩺 Health Check", health, "#198754"), ("↻ Refresh", load, "#6c757d")):
        CanvasButton(bar, text, handler, color, width=160, height=30).pack(side="left", padx=4)
    load()


# --------------------------------------------------------------------------- managed lists
@handle_errors
def open_manage_lists(ctx: AppContext) -> None:
    ctx.session.require("lists.manage")
    win = tk.Toplevel(ctx.root)
    win.title("Categories & Warehouses")
    win.geometry("520x520")
    win.config(bg=WHITE)
    top = tk.Frame(win, bg=WHITE)
    top.pack(fill="x", padx=15, pady=10)
    tk.Label(top, text="List:", **LABEL_STYLE).pack(side="left", padx=5)
    chooser = ttk.Combobox(top, values=["Categories", "Warehouses"], state="readonly", width=16)
    chooser.pack(side="left", padx=5)
    chooser.set("Categories")
    box, tree = make_tree(win, ("Name",), (440,), height=14)
    box.pack(fill="both", expand=True, padx=15, pady=5)

    def kind() -> str:
        return "categories" if chooser.get() == "Categories" else "warehouses"

    def load(_event=None) -> None:
        fill_tree(tree, [(name,) for name in lists_service.names(ctx.conn, kind())])

    def changed() -> None:
        load()
        ctx.refresh()  # updates the dropdowns on the Inventory form

    def selected_name():
        picked = tree.selection()
        if not picked:
            messagebox.showwarning("Warning", "Select an entry first!")
            return None
        return str(tree.item(picked[0], "values")[0])

    def add_entry() -> None:
        def save(values: dict) -> None:
            lists_service.add(ctx.conn, ctx.session, kind(), values["name"])
            changed()

        _form_dialog(win, f"Add to {chooser.get()}", [("name", "Name", "text")], "Add", save)

    def rename_entry() -> None:
        old = selected_name()
        if old is None:
            return

        def save(values: dict) -> None:
            moved = lists_service.rename(ctx.conn, ctx.session, kind(), old, values["name"])
            messagebox.showinfo("Renamed", f"{moved} product(s) were updated.")
            changed()

        _form_dialog(win, f"Rename: {old}", [("name", "New name", "text")], "Rename", save, initial={"name": old})

    @handle_errors
    def remove_entry() -> None:
        name = selected_name()
        if name is not None and messagebox.askyesno("Confirm", f"Remove '{name}' from the list?"):
            lists_service.remove(ctx.conn, ctx.session, kind(), name)
            changed()

    chooser.bind("<<ComboboxSelected>>", load)
    bar = tk.Frame(win, bg=WHITE)
    bar.pack(pady=10)
    for text, handler, color in (("➕ Add", add_entry, "#198754"), ("✏ Rename", rename_entry, "#0d6efd"),
                                 ("🗑 Remove", remove_entry, "#dc3545")):
        CanvasButton(bar, text, handler, color, width=110, height=30).pack(side="left", padx=5)
    load()

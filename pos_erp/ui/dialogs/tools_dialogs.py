"""Settings > Backup & Reminders: backup copy folder, WhatsApp reminder message, daily summary email."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ...services import business_service, digest_service, mirror_service
from ..context import AppContext
from ..widgets import DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, handle_errors


class ToolsDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        profile = business_service.get_profile(ctx.conn)
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Backup & Reminders")
        self.win.geometry("680x820")
        self.win.config(bg=WHITE)
        self.entries: dict[str, tk.Entry] = {}

        self._heading("Backup copy (off-site)")
        self._note("Every automatic backup (when the app closes) is also copied here. Choose a Google Drive, OneDrive "
                   "or Dropbox sync folder, a USB drive or a network share: then a dead computer does not mean "
                   "lost data.")
        self._entry("backup_mirror_dir", "Copy folder:", 50, profile)
        CanvasButton(self.win, "Browse...", self.browse, "#6c757d", width=100, height=26).pack()
        self._entry("backup_mirror_keep", "Copies to keep:", 8, profile)
        self.status = tk.Label(self.win, text=mirror_service.status(ctx.conn), bg=WHITE, fg="#6c757d", font=(FONT, 9),
                               wraplength=620)
        self.status.pack(pady=4)
        row = tk.Frame(self.win, bg=WHITE)
        row.pack()
        CanvasButton(row, "Test folder", self.test_folder, "#0d6efd", width=120, height=28).pack(side="left", padx=4)
        CanvasButton(row, "Back up now", self.backup_now, "#198754", width=120, height=28).pack(side="left", padx=4)

        self._heading("WhatsApp reminders")
        self._entry("country_code", "Country code (digits, like 880):", 8, profile)
        self._entry("reminder_template", "Message ({name}, {amount}, {shop}):", 60, profile)

        self._heading("Daily summary email")
        self._note("Sends sales, profit, expenses, money owed, low stock and expiry to this address. Needs the email "
                   "setup (mail.env). Run python3 -m scripts.send_digest from a scheduler to send it every night.")
        self._entry("digest_email", "Send to:", 40, profile)
        tk.Label(self.win, text="Send automatically when a shift is closed:", **LABEL_STYLE).pack(pady=(8, 0))
        self.auto_combo = ttk.Combobox(self.win, values=["No", "Yes"], state="readonly", width=8)
        self.auto_combo.set(profile["digest_auto"])
        self.auto_combo.pack(pady=3)

        buttons = tk.Frame(self.win, bg=WHITE)
        buttons.pack(pady=14)
        CanvasButton(buttons, "Save", self.save, "#198754", width=120, height=32).pack(side="left", padx=5)
        CanvasButton(buttons, "📧 Send summary now", self.send_summary, "#6f42c1", width=190, height=32).pack(
            side="left", padx=5)

    def _heading(self, text: str) -> None:
        tk.Label(self.win, text=text, bg=WHITE, fg=DARK, font=(FONT, 12, "bold")).pack(pady=(16, 2))

    def _note(self, text: str) -> None:
        tk.Label(self.win, text=text, bg=WHITE, fg="#6c757d", font=(FONT, 9), wraplength=620, justify="left").pack()

    def _entry(self, key: str, label: str, width: int, profile: dict) -> None:
        tk.Label(self.win, text=label, **LABEL_STYLE).pack(pady=(8, 0))
        container, entry = create_styled_entry(self.win, width=width)
        container.pack(pady=3)
        entry.insert(0, profile[key])
        self.entries[key] = entry

    def values(self) -> dict[str, str]:
        values = {key: entry.get() for key, entry in self.entries.items()}
        values["digest_auto"] = self.auto_combo.get()
        return values

    @handle_errors
    def save(self, notify: bool = True) -> None:
        business_service.update_profile(self.ctx.conn, self.ctx.session, self.values())
        if notify:
            messagebox.showinfo("Saved", "Settings were saved.", parent=self.win)

    def browse(self) -> None:
        folder = filedialog.askdirectory(parent=self.win)
        if folder:
            entry = self.entries["backup_mirror_dir"]
            entry.delete(0, tk.END)
            entry.insert(0, folder)

    @handle_errors
    def test_folder(self) -> None:
        mirror_service.check_folder(self.entries["backup_mirror_dir"].get())
        messagebox.showinfo("Folder OK", "Files can be written to that folder.", parent=self.win)

    @handle_errors
    def backup_now(self) -> None:
        self.save(notify=False)
        path = mirror_service.backup_now(self.ctx.conn, self.ctx.session)
        self.status.config(text=mirror_service.status(self.ctx.conn))
        messagebox.showinfo("Backup copy", f"Backup written to {path}", parent=self.win)

    @handle_errors
    def send_summary(self) -> None:
        self.save(notify=False)
        address = digest_service.send(self.ctx.conn, self.ctx.session)
        messagebox.showinfo("Daily summary", f"The summary was sent to {address}.", parent=self.win)


def open_tools(ctx: AppContext) -> ToolsDialog:
    return ToolsDialog(ctx)

"""Settings > Receipt & Printer: address, phone, paper size, thermal printer, test page and test PDF."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ... import config
from ...services import business_service, print_output, receipt_layout, thermal
from ..context import AppContext
from ..widgets import DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, handle_errors

HINT = ("Thermal printer target (leave empty to use the normal system printer):\n"
        "tcp://192.168.1.50:9100   network printer\n"
        "lp:POS80   macOS/Linux raw print queue\n"
        "/dev/usb/lp0 or \\\\localhost\\POS80   device or shared printer")


class ReceiptSettingsDialog:
    FIELDS = (("shop_address", "Business address", 46), ("shop_phone", "Phone number", 24))

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        profile = business_service.get_profile(ctx.conn)
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Receipt & Printer")
        self.win.geometry("560x640")
        self.win.config(bg=WHITE)
        tk.Label(self.win, text="Receipt & Printer", bg=WHITE, fg=DARK, font=(FONT, 14, "bold")).pack(pady=(14, 2))
        tk.Label(self.win, text="Business name, currency, tax and the footer message are in Shop Settings.",
                 bg=WHITE, fg="#6c757d", font=(FONT, 9)).pack()
        self.entries = {}
        for key, label, width in self.FIELDS:
            self.entries[key] = self._entry(label, width, profile[key])
        tk.Label(self.win, text="Paper size:", **LABEL_STYLE).pack(pady=(10, 0))
        self.paper_combo = ttk.Combobox(self.win, values=list(thermal.PAPERS), state="readonly", width=12)
        self.paper_combo.set(profile["receipt_paper"])
        self.paper_combo.pack(pady=3)
        self.entries["printer_target"] = self._entry("Thermal printer (optional):", 46, profile["printer_target"])
        tk.Label(self.win, text=HINT, bg=WHITE, fg="#6c757d", font=(FONT, 8), justify="left").pack(pady=6)
        for text, command, color in (("Save", self.save, "#198754"), ("🖨 Print test page", self.print_test, "#0d6efd"),
                                     ("📄 Save test PDF", self.save_test_pdf, "#6c757d")):
            CanvasButton(self.win, text, command, color, width=190, height=32).pack(pady=5)

    def _entry(self, label: str, width: int, value: str):
        tk.Label(self.win, text=label if label.endswith(":") else f"{label}:", **LABEL_STYLE).pack(pady=(10, 0))
        container, entry = create_styled_entry(self.win, width=width)
        container.pack(pady=3)
        entry.insert(0, value)
        return entry

    def values(self) -> dict[str, str]:
        values = {key: entry.get() for key, entry in self.entries.items()}
        values["receipt_paper"] = self.paper_combo.get()
        return values

    @handle_errors
    def save(self, notify: bool = True) -> None:
        business_service.update_profile(self.ctx.conn, self.ctx.session, self.values())
        if notify:
            messagebox.showinfo("Saved", "Receipt and printer settings were saved.", parent=self.win)

    def _sample(self):
        profile = business_service.get_profile(self.ctx.conn)
        basics = business_service.shop_basics(self.ctx.conn)
        paper = thermal.paper_for(profile["receipt_paper"])
        data = receipt_layout.sample(basics["shop_name"], basics["receipt_footer"], profile["shop_address"],
                                     profile["shop_phone"], config.PAYMENT_METHODS[0])
        return receipt_layout.render_text(data, paper.chars), paper, profile["printer_target"]

    @handle_errors
    def print_test(self) -> None:
        self.save(notify=False)
        text, paper, target = self._sample()
        print_output.print_document(text, paper.name, target)
        messagebox.showinfo("Test page", "The test page was sent to the printer.", parent=self.win)

    @handle_errors
    def save_test_pdf(self) -> None:
        self.save(notify=False)
        text, paper, _target = self._sample()
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".pdf", initialfile="test_receipt.pdf",
                                            filetypes=[("PDF", "*.pdf")])
        if path:
            print_output.save_pdf(text, paper.name, path)
            messagebox.showinfo("Saved", f"Test receipt saved to {path}", parent=self.win)


def open_receipt_settings(ctx: AppContext) -> ReceiptSettingsDialog:
    return ReceiptSettingsDialog(ctx)

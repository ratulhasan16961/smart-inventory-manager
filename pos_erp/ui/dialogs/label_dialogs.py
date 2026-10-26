"""Barcode & Label Generator: a real Code 128 preview and an A4 sheet of labels as a PDF."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox

from ...errors import ValidationError
from ...money import fmt
from ...services import barcode, inventory_service, label_sheet
from ..context import AppContext
from ..widgets import FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, handle_errors

CANVAS_W, CANVAS_H = 460, 150


class LabelDialog:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.win = tk.Toplevel(ctx.root)
        self.win.title("Barcode & Label Generator")
        self.win.geometry("520x700")
        self.win.config(bg=WHITE)
        tk.Label(self.win, text="Barcode & Label Generator", bg=WHITE, fg="#212529", font=(FONT, 13, "bold")).pack(pady=10)
        self.code_e = self._entry("Barcode or product ID (what the scanner reads):", 28)
        CanvasButton(self.win, "Fill name and price from the product", self.lookup, "#6c757d", width=250,
                     height=26).pack(pady=2)
        self.name_e = self._entry("Label name:", 34)
        self.price_e = self._entry("Label price:", 12)
        self.copies_e = self._entry("Copies:", 6, "1")
        self.start_e = self._entry("Start at label # (to reuse a partly used sheet):", 6, "1")
        self.canvas = tk.Canvas(self.win, width=CANVAS_W, height=CANVAS_H, bg="white", highlightthickness=1,
                                highlightbackground="#adb5bd")
        self.canvas.pack(pady=14)
        row = tk.Frame(self.win, bg=WHITE)
        row.pack()
        CanvasButton(row, "Preview", self.preview, "#0d6efd", width=120, height=32).pack(side="left", padx=5)
        CanvasButton(row, "📑 Save A4 sheet (PDF)", self.save_pdf, "#198754", width=190, height=32).pack(
            side="left", padx=5)
        tk.Label(self.win, text="A4 sheet: 3 x 8 labels. Print the PDF at 100% (no scaling). Test one label with "
                                "your scanner before printing a whole sheet.", bg=WHITE, fg="#6c757d", font=(FONT, 8),
                 wraplength=460).pack(pady=8)

    def _entry(self, caption: str, width: int, initial: str = "") -> tk.Entry:
        tk.Label(self.win, text=caption, **LABEL_STYLE).pack(pady=(8, 0))
        container, entry = create_styled_entry(self.win, width=width)
        container.pack(pady=3)
        entry.insert(0, initial)
        return entry

    @staticmethod
    def _set(entry: tk.Entry, text: str) -> None:
        entry.delete(0, tk.END)
        entry.insert(0, text)

    @handle_errors
    def lookup(self) -> None:
        product = inventory_service.find_for_sale(self.ctx.conn, self.ctx.session, self.code_e.get().strip())
        stored = self.ctx.conn.execute("SELECT barcode FROM products WHERE id = ?", (product["id"],)).fetchone()
        self._set(self.code_e, (stored[0] if stored else None) or str(product["id"]))
        self._set(self.name_e, product["name"])
        self._set(self.price_e, fmt(product["price_cents"]))

    def label(self) -> label_sheet.Label:
        code = self.code_e.get().strip()
        if not code:
            raise ValidationError("Enter the barcode or product ID first.")
        barcode.encode_values(code)
        return label_sheet.Label(code, self.name_e.get().strip(), self.price_e.get().strip())

    @staticmethod
    def _whole(entry: tk.Entry, name: str, maximum: int) -> int:
        try:
            value = int(entry.get().strip() or "1")
        except ValueError:
            raise ValidationError(f"{name} must be a whole number.") from None
        if not 1 <= value <= maximum:
            raise ValidationError(f"{name} must be between 1 and {maximum}.")
        return value

    @handle_errors
    def preview(self) -> None:
        label = self.label()
        runs, total = barcode.bar_runs(label.code), barcode.total_modules(label.code)
        module = max(1, min(4, (CANVAS_W - 30) // (total + 20)))
        left = (CANVAS_W - total * module) // 2
        self.canvas.delete("all")
        for offset, width in runs:
            self.canvas.create_rectangle(left + offset * module, 34, left + (offset + width) * module, 104,
                                         fill="black", width=0)
        self.canvas.create_text(CANVAS_W // 2, 16, text=label.name, font=(FONT, 10, "bold"))
        self.canvas.create_text(CANVAS_W // 2, 118, text=label.code, font=("Courier", 10))
        self.canvas.create_text(CANVAS_W // 2, 136, text=label.price, font=(FONT, 12, "bold"))

    @handle_errors
    def save_pdf(self) -> None:
        label = self.label()
        copies = self._whole(self.copies_e, "Copies", 500)
        start = self._whole(self.start_e, "Start at label #", 24)
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".pdf", initialfile=f"labels_{label.code}.pdf",
                                            filetypes=[("PDF", "*.pdf")])
        if path:
            with open(path, "wb") as handle:
                handle.write(label_sheet.render_sheet([label] * copies, skip=start - 1))
            messagebox.showinfo("Saved", f"{copies} label(s) saved to {path}", parent=self.win)


def open_barcode_generator(ctx: AppContext) -> LabelDialog:
    return LabelDialog(ctx)

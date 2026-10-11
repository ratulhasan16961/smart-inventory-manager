"""Dashboard tab: 30-day revenue chart, top sellers and low-stock list (Admin only)."""
from __future__ import annotations

import tkinter as tk

from ..money import fmt
from ..services import dashboard_service, reporting_service
from .context import AppContext
from .widgets import BG, DARK, FONT, fill_tree, make_tree

DAYS = 30
BAR_COLOR = "#0d6efd"
MUTED = "#6c757d"


class DashboardTab:
    def __init__(self, parent: tk.Frame, ctx: AppContext) -> None:
        self.ctx = ctx
        self.data: list[tuple[str, int]] = []
        tk.Label(parent, text=f"Net revenue, last {DAYS} days", bg=BG, fg=DARK, font=(FONT, 12, "bold")).pack(
            anchor="w", padx=15, pady=(10, 0))
        self.lbl_summary = tk.Label(parent, text="", bg=BG, fg=DARK, font=(FONT, 10))
        self.lbl_summary.pack(anchor="w", padx=15)
        self.chart = tk.Canvas(parent, bg="#ffffff", height=300, highlightthickness=1, highlightbackground="#dee2e6")
        self.chart.pack(fill="x", padx=15, pady=10)
        self.chart.bind("<Configure>", lambda _e: self._draw())

        lower = tk.Frame(parent, bg=BG)
        lower.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        left = tk.Frame(lower, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = tk.Frame(lower, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        tk.Label(left, text=f"Top sellers ({DAYS} days)", bg=BG, fg=DARK, font=(FONT, 10, "bold")).pack(anchor="w")
        box, self.top_tree = make_tree(left, ("Product", "Units", "Revenue"), (240, 80, 120), height=7)
        box.pack(fill="both", expand=True)
        tk.Label(right, text="Low stock", bg=BG, fg=DARK, font=(FONT, 10, "bold")).pack(anchor="w")
        box, self.low_tree = make_tree(right, ("Product", "Stock", "Min Alert"), (240, 80, 100), height=7)
        box.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self) -> None:
        conn, session = self.ctx.conn, self.ctx.session
        self.data = dashboard_service.daily_revenue(conn, session, DAYS)
        total = sum(value for _day, value in self.data)
        best_day, best = max(self.data, key=lambda item: item[1])
        best_text = f"{best_day} ({fmt(best)})" if best > 0 else "no sales yet"
        self.lbl_summary.config(text=f"Total {fmt(total)}   |   Average per day {fmt(total // DAYS)}   |   "
                                     f"Best day {best_text}")
        fill_tree(self.top_tree, [(name, units, fmt(cents))
                                  for name, units, cents in dashboard_service.top_sellers(conn, session, DAYS)])
        low = reporting_service.low_stock_report(conn, session)
        fill_tree(self.low_tree, [(row[1], row[4], row[5]) for row in low[:50]])
        self._draw()

    def _draw(self) -> None:
        canvas = self.chart
        canvas.delete("all")
        data = self.data
        if not data:
            return
        width = max(canvas.winfo_width(), 700)
        height = max(canvas.winfo_height(), 260)
        left, right, top, bottom = 80, 20, 20, 40
        plot_w, plot_h = width - left - right, height - top - bottom
        peak = max(max(value for _day, value in data), 1)
        for fraction in (0, 0.5, 1):
            y = top + plot_h * (1 - fraction)
            canvas.create_line(left, y, width - right, y, fill="#e9ecef")
            canvas.create_text(left - 8, y, text=fmt(int(peak * fraction)), anchor="e", font=(FONT, 8), fill=MUTED)
        slot = plot_w / len(data)
        bar = max(slot * 0.7, 2)
        for index, (day, cents) in enumerate(data):
            x0 = left + index * slot + (slot - bar) / 2
            bar_height = plot_h * max(cents, 0) / peak
            canvas.create_rectangle(x0, top + plot_h - bar_height, x0 + bar, top + plot_h, fill=BAR_COLOR, outline="")
            if index % 5 == 0 or index == len(data) - 1:
                canvas.create_text(x0 + bar / 2, top + plot_h + 14, text=day[5:], font=(FONT, 8), fill=MUTED)

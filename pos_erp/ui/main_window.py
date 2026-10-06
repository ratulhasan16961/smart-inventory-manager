"""Main window: dashboard cards + POS and Inventory tabs."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..money import fmt
from ..services import reporting_service
from .context import AppContext
from .inventory_tab import InventoryTab
from .pos_tab import PosTab
from .widgets import BG, FONT

CARD_COLORS = {"items": "#212529", "low": "#dc3545", "expiry": "#fd7e14",
               "cost": "#198754", "retail": "#20c997", "revenue": "#0d6efd"}


class MainWindow:
    def __init__(self, root, conn, session) -> None:
        self.root = root
        self.ctx = AppContext(root, conn, session, refresh=self.refresh)
        self.ctx.set_session(session)

        strip = tk.Frame(root, bg=BG)
        strip.pack(fill="x", padx=15, pady=8)
        self.cards: dict[str, tk.Label] = {}
        for column, (key, color) in enumerate(CARD_COLORS.items()):
            card = tk.Label(strip, text="...", bg=color, fg="#ffffff", font=(FONT, 10, "bold"), width=21, height=2)
            card.grid(row=0, column=column, padx=5, pady=5)
            self.cards[key] = card

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=15, pady=5)
        pos_frame = tk.Frame(notebook, bg=BG)
        inventory_frame = tk.Frame(notebook, bg=BG)
        notebook.add(pos_frame, text="  🛒 POS Billing  ")
        notebook.add(inventory_frame, text="  📦 Inventory  ")

        self.pos = PosTab(pos_frame, self.ctx)
        self.inventory = InventoryTab(inventory_frame, self.ctx)
        self.refresh()
        self.inventory.check_low_stock_alerts()
        root.after(200, self.pos.focus_barcode)

    def refresh(self) -> None:
        s = reporting_service.dashboard_stats(self.ctx.conn)
        texts = {
            "items": f"Total Items: {s.total_items}",
            "low": f"Low Stock Alerts: {s.low_stock}",
            "expiry": f"Expiry Alerts: {s.expiry_alerts}",
            "cost": f"Stock Cost: {fmt(s.cost_value_cents)}",
            "retail": f"Retail Value: {fmt(s.retail_value_cents)}",
            "revenue": f"Net Revenue: {fmt(s.net_revenue_cents)}",
        }
        for key, text in texts.items():
            self.cards[key].config(text=text)
        self.inventory.refresh_table()
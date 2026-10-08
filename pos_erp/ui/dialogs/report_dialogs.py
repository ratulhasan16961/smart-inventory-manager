"""Reports window: date-range sales report, low stock, expiry and stock valuation."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ... import dayrange
from ...money import fmt
from ...services import reporting_service
from ..context import AppContext
from ..widgets import (DARK, FONT, LABEL_STYLE, WHITE, CanvasButton, create_styled_entry, fill_tree,
                       handle_errors, make_tree)


@handle_errors
def open_reports(ctx: AppContext) -> None:
    ctx.session.require("reports.view")
    win = tk.Toplevel(ctx.root)
    win.title("Reports")
    win.geometry("1100x760")
    win.config(bg=WHITE)
    notebook = ttk.Notebook(win)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    sales_tab = tk.Frame(notebook, bg=WHITE)
    notebook.add(sales_tab, text="  Sales  ")
    _build_sales_tab(ctx, sales_tab)
    for title, header, loader in (
            ("Low Stock", reporting_service.LOW_STOCK_HEADER, reporting_service.low_stock_report),
            ("Expiry", reporting_service.EXPIRY_HEADER, reporting_service.expiry_report),
            ("Stock Value", reporting_service.VALUATION_HEADER, reporting_service.stock_valuation)):
        tab = tk.Frame(notebook, bg=WHITE)
        notebook.add(tab, text=f"  {title}  ")
        _build_table_tab(ctx, tab, title, header, loader)


def _tree_in(parent, title, columns, widths):
    tk.Label(parent, text=title, **LABEL_STYLE).pack(anchor="w", padx=4)
    box, tree = make_tree(parent, columns, widths, height=7)
    box.pack(fill="both", expand=True, padx=4, pady=(0, 6))
    return tree


def _build_sales_tab(ctx: AppContext, parent) -> None:
    report = None
    top = tk.Frame(parent, bg=WHITE)
    top.pack(fill="x", padx=10, pady=10)
    tk.Label(top, text="Period:", **LABEL_STYLE).pack(side="left", padx=4)
    preset = ttk.Combobox(top, values=list(dayrange.PRESETS), state="readonly", width=14)
    preset.pack(side="left", padx=4)
    preset.set("This month")
    tk.Label(top, text="From (YYYY-MM-DD):", **LABEL_STYLE).pack(side="left", padx=4)
    container, from_e = create_styled_entry(top, width=12)
    container.pack(side="left", padx=4)
    tk.Label(top, text="To:", **LABEL_STYLE).pack(side="left", padx=4)
    container, to_e = create_styled_entry(top, width=12)
    container.pack(side="left", padx=4)

    summary = tk.Label(parent, text="", bg=WHITE, fg=DARK, font=(FONT, 10, "bold"), justify="left")
    summary.pack(anchor="w", padx=14, pady=4)
    tk.Label(parent, text="Revenue includes tax. Profit excludes tax and uses the cost at the time of sale. "
                          "Returns count against the date of the invoice.", bg=WHITE, fg="#6c757d",
             font=(FONT, 9)).pack(anchor="w", padx=14)

    upper = tk.Frame(parent, bg=WHITE)
    upper.pack(fill="both", expand=True, padx=10, pady=(8, 0))
    lower = tk.Frame(parent, bg=WHITE)
    lower.pack(fill="both", expand=True, padx=10)
    panes = []
    for row in (upper, upper, lower, lower):
        pane = tk.Frame(row, bg=WHITE)
        pane.pack(side="left", fill="both", expand=True)
        panes.append(pane)
    daily_tree = _tree_in(panes[0], "Daily sales", ("Date", "Orders", "Units", "Revenue"), (110, 70, 70, 110))
    top_tree = _tree_in(panes[1], "Top products (by revenue)", ("Product", "Units", "Revenue"), (230, 70, 110))
    pay_tree = _tree_in(panes[2], "Payment methods", ("Method", "Orders", "Revenue"), (170, 70, 110))
    cat_tree = _tree_in(panes[3], "Categories", ("Category", "Units", "Revenue"), (170, 70, 110))

    def on_preset(_event=None) -> None:
        name = preset.get()
        if name == "Custom":
            return
        start, end = dayrange.preset_range(name)
        for entry, value in ((from_e, start), (to_e, end)):
            entry.delete(0, tk.END)
            entry.insert(0, value.isoformat())

    @handle_errors
    def run(_event=None) -> None:
        nonlocal report
        report = reporting_service.sales_report(ctx.conn, ctx.session, from_e.get(), to_e.get())
        r = report
        summary.config(text=(
            f"Orders: {r.orders}    Units sold: {r.units}    Gross sales: {fmt(r.gross_cents)}    "
            f"Refunds: -{fmt(r.refunds_cents)}    NET REVENUE: {fmt(r.net_cents)}\n"
            f"Discounts given: {fmt(r.discount_cents)}    Tax collected: {fmt(r.tax_cents)}    "
            f"Est. gross profit: {fmt(r.profit_cents)}    Voided invoices: {r.voided_orders} ({fmt(r.voided_cents)})"))
        fill_tree(daily_tree, [(d.day, d.orders, d.units, fmt(d.revenue_cents)) for d in r.daily])
        fill_tree(top_tree, [(n, u, fmt(c)) for n, u, c in r.top_products])
        fill_tree(pay_tree, [(n, u, fmt(c)) for n, u, c in r.payments])
        fill_tree(cat_tree, [(n, u, fmt(c)) for n, u, c in r.categories])

    @handle_errors
    def export() -> None:
        if report is None:
            run()
        if report is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")],
                                            initialfile=f"sales_{report.start}_to_{report.end}.csv")
        if path:
            reporting_service.export_sales_report_csv(ctx.session, report, path)
            messagebox.showinfo("Export Complete", f"Report saved to {path}")

    preset.bind("<<ComboboxSelected>>", lambda _e: (on_preset(), run()))
    CanvasButton(top, "Run report", run, "#0d6efd", width=110, height=28).pack(side="left", padx=8)
    CanvasButton(top, "📤 Export CSV", export, "#198754", width=120, height=28).pack(side="left", padx=4)
    on_preset()
    run()


def _build_table_tab(ctx: AppContext, parent, title: str, header, loader) -> None:
    rows: list = []
    bar = tk.Frame(parent, bg=WHITE)
    bar.pack(fill="x", padx=10, pady=10)
    box, tree = make_tree(parent, header, [max(80, 960 // len(header))] * len(header))
    box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    @handle_errors
    def load() -> None:
        rows[:] = loader(ctx.conn, ctx.session)
        fill_tree(tree, rows)

    @handle_errors
    def export() -> None:
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")],
                                            initialfile=f"{title.lower().replace(' ', '_')}_report.csv")
        if path:
            reporting_service.export_table_csv(ctx.session, header, rows, path)
            messagebox.showinfo("Export Complete", f"Report saved to {path}")

    CanvasButton(bar, "↻ Refresh", load, "#6c757d", width=100, height=28).pack(side="left", padx=4)
    CanvasButton(bar, "📤 Export CSV", export, "#198754", width=120, height=28).pack(side="left", padx=4)
    load()

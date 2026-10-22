"""Prepares an invoice for printing: business profile from the settings, paper layout, credit-sale lines."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import config
from . import business_service, credit_service, print_output, receipt_layout, settings_service, thermal
from .sales_service import Invoice


@dataclass(frozen=True)
class PreparedDocument:
    text: str
    paper: thermal.Paper
    printer_target: str
    name: str

def prepare_invoice(conn: sqlite3.Connection, invoice: Invoice, meta) -> PreparedDocument:
    """`meta` is the row from till_service.invoice_meta (amounts received, status, points)."""
    shop = settings_service.get_all(conn)
    profile = business_service.get_profile(conn)
    paper = thermal.paper_for(profile["receipt_paper"])
    on_credit = invoice.payment_method == credit_service.CREDIT_METHOD
    data = receipt_layout.ReceiptData(
        shop_name=shop["shop_name"], invoice_no=invoice.invoice_no, date=invoice.created_at, cashier=invoice.cashier,
        customer_name=invoice.customer_name, customer_phone=invoice.customer_phone,
        lines=tuple((item.product_name, item.quantity, item.line_subtotal_cents) for item in invoice.items),
        subtotal_cents=invoice.subtotal_cents, discount_cents=invoice.discount_cents,
        points_redeemed=meta["points_redeemed"], points_value_cents=meta["points_value_cents"],
        tax_rate=format(invoice.tax_rate, "f"), tax_cents=invoice.tax_cents, total_cents=invoice.total_cents,
        payment_method=invoice.payment_method, cash_method=config.PAYMENT_METHODS[0],
        tendered_cents=meta["tendered_cents"], change_cents=meta["change_cents"],
        paid_now_cents=meta["tendered_cents"] if on_credit else None,
        account_balance_cents=credit_service.balance_of_phone(conn, invoice.customer_phone) if on_credit else None,
        voided=meta["status"] == "Voided", address=profile["shop_address"], phone=profile["shop_phone"],
        footer=shop["receipt_footer"])
    return PreparedDocument(receipt_layout.render_text(data, paper.chars), paper, profile["printer_target"],
                            f"Invoice_{invoice.invoice_no}")


def print_document(doc: PreparedDocument) -> None:
    print_output.print_document(doc.text, doc.paper.name, doc.printer_target)


def save_pdf(doc: PreparedDocument, path) -> Path:
    return print_output.save_pdf(doc.text, doc.paper.name, path)

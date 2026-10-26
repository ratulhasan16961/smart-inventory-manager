"""A4 sheets of product labels (name, Code 128 barcode, price) as a PDF. Pure Python, no libraries."""
from __future__ import annotations

from dataclasses import dataclass

from ..errors import ValidationError
from . import barcode, thermal

A4_W, A4_H = 595.28, 841.89


@dataclass(frozen=True)
class Label:
    code: str
    name: str = ""
    price: str = ""


def _esc(text: str) -> str:
    return thermal.ascii_safe(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _centered(cx: float, y: float, size: float, text: str, max_width: float) -> str:
    text = text[:max(1, int(max_width / (0.6 * size)))]
    x = cx - len(text) * 0.6 * size / 2
    return f"BT /F1 {size:.1f} Tf {x:.2f} {y:.2f} Td ({_esc(text)}) Tj ET"


def _label_ops(label: Label, x: float, y: float, w: float, h: float) -> list[str]:
    runs, total = barcode.bar_runs(label.code), barcode.total_modules(label.code)
    module = min(1.6, (w - 12) / (total + 20))
    left = x + (w - total * module) / 2
    bottom, height = y + h * 0.30, h * 0.40
    ops = ["0.85 G", "0.5 w", f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re", "S", "0 g"]
    ops += [f"{left + offset * module:.2f} {bottom:.2f} {width * module:.2f} {height:.2f} re" for offset, width in runs]
    ops.append("f")
    cx = x + w / 2
    if label.name:
        ops.append(_centered(cx, y + h - 12, 8, label.name, w - 8))
    ops.append(_centered(cx, bottom - 9, 7, label.code, w - 8))
    if label.price:
        ops.append(_centered(cx, y + 6, 10, label.price, w - 8))
    return ops


def _build_pdf(pages: list[str]) -> bytes:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    kids = []
    for index, content in enumerate(pages):
        page_no, content_no = 4 + 2 * index, 5 + 2 * index
        kids.append(f"{page_no} 0 R")
        stream = content.encode("latin-1", "replace")
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {A4_W} {A4_H}] /Contents {content_no} 0 R "
                        "/Resources << /Font << /F1 3 0 R >> >> >>").encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def render_sheet(labels, cols: int = 3, rows: int = 8, skip: int = 0) -> bytes:
    """Labels fill the sheet left to right, top to bottom. `skip` leaves the first cells empty (a used sheet)."""
    if skip < 0:
        raise ValidationError("The first label position cannot be negative.")
    cell_w, cell_h = (A4_W - 36.0) / cols, (A4_H - 56.0) / rows
    per_page = cols * rows
    slots = [None] * skip + list(labels)
    pages = []
    for start in range(0, max(len(slots), 1), per_page):
        ops: list[str] = []
        for index, label in enumerate(slots[start:start + per_page]):
            if label is not None:
                row, col = divmod(index, cols)
                ops += _label_ops(label, 18.0 + col * cell_w, A4_H - 28.0 - (row + 1) * cell_h, cell_w, cell_h)
        pages.append("\n".join(ops))
    return _build_pdf(pages)

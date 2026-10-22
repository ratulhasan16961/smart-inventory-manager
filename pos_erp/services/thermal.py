"""Paper sizes and output formats for receipts: layout helpers, ESC/POS bytes and a small PDF writer.

Standard library only and no database: every function is a plain transformation, so it is easy to test.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import textwrap
from dataclasses import dataclass


@dataclass(frozen=True)
class Paper:
    name: str
    chars: int
    width_mm: float
    thermal: bool


PAPERS = {
    "58mm": Paper("58mm", 32, 58.0, True),
    "80mm": Paper("80mm", 48, 80.0, True),
    "A4": Paper("A4", 80, 210.0, False),
}
DEFAULT_PAPER = "80mm"

_SYMBOLS = {"৳": "Tk ", "₹": "Rs ", "€": "EUR ", "–": "-", "—": "-", "•": "*", "…": "...",
            "“": '"', "”": '"', "‘": "'", "’": "'"}


def paper_for(name: str | None) -> Paper:
    return PAPERS.get(name or "", PAPERS[DEFAULT_PAPER])


def ascii_safe(text: str) -> str:
    """Thermal printers use the CP437 code page by default: swap the symbols it cannot print."""
    for symbol, replacement in _SYMBOLS.items():
        text = text.replace(symbol, replacement)
    return text.encode("cp437", "replace").decode("cp437")


def center(text: str, width: int) -> str:
    return text.strip().center(width).rstrip()


def wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap((text or "").strip(), width) or []


def two_columns(left: str, right: str, width: int) -> str:
    right = str(right)
    if len(right) >= width:
        return right[:width]
    room = width - len(right) - 1
    return f"{str(left)[:room]:<{room}} {right}"


ESC, GS = b"\x1b", b"\x1d"


def escpos_document(lines: list[str], *, cut: bool = True, feed: int = 4, open_drawer: bool = False) -> bytes:
    """ESC/POS bytes: initialise, CP437 text, feed, partial cut (and optionally the cash-drawer pulse)."""
    data = bytearray()
    if open_drawer:
        data += ESC + b"p\x00\x19\xfa"
    data += ESC + b"@" + ESC + b"t\x00"
    for line in lines:
        data += ascii_safe(line).encode("cp437", "replace") + b"\n"
    data += ESC + b"d" + bytes([max(0, min(feed, 255))])
    if cut:
        data += GS + b"V\x42\x00"
    return bytes(data)


def send_raw(data: bytes, target: str, timeout: float = 10.0) -> None:
    """Send bytes to `tcp://host[:port]` (network printer), `lp:QUEUE` (CUPS raw queue) or a device/share path."""
    target = (target or "").strip()
    if not target:
        raise ValueError("No printer target is set.")
    if target.startswith("tcp://"):
        host, _, port = target[len("tcp://"):].partition(":")
        if not host:
            raise ValueError("The network printer address is missing its host name.")
        with socket.create_connection((host, int(port or 9100)), timeout=timeout) as connection:
            connection.sendall(data)
    elif target.startswith("lp:"):
        command = shutil.which("lp") or shutil.which("lpr")
        if command is None:
            raise OSError("No print command (lp) was found on this computer.")
        queue = target[3:].strip()
        args = [command, "-d", queue, "-o", "raw"] if queue else [command, "-o", "raw"]
        subprocess.run(args, input=data, check=True, capture_output=True, timeout=timeout)
    else:
        with open(target, "wb") as handle:
            handle.write(data)


def _pdf_text(line: str) -> str:
    return ascii_safe(line).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_pdf(lines: list[str], paper: Paper) -> bytes:
    """A one-page PDF (Courier, built into every viewer) as wide as the paper; roll paper grows to fit the text."""
    page_w = paper.width_mm / 25.4 * 72
    margin = 36.0 if paper.name == "A4" else 8.0
    font_size = (page_w - 2 * margin) / (paper.chars * 0.6)
    leading = font_size * 1.25
    page_h = margin * 2 + leading * (len(lines) + 1)
    if paper.name == "A4":
        page_h = max(page_h, 841.89)
    ops = ["BT", f"/F1 {font_size:.2f} Tf", f"{leading:.2f} TL",
           f"{margin:.2f} {page_h - margin - font_size:.2f} Td"]
    ops += [f"({_pdf_text(line)}) Tj T*" for line in lines]
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w:.2f} {page_h:.2f}] /Contents 4 0 R "
         "/Resources << /Font << /F1 5 0 R >> >> >>").encode(),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)

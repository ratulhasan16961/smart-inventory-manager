"""Sends receipt text to a printer or a PDF file in the layout of the chosen paper."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import print_service, thermal


def print_document(text: str, paper_name: str = thermal.DEFAULT_PAPER, target: str = "") -> None:
    """Roll paper plus a printer target: raw ESC/POS. Otherwise the normal system printer (plain text)."""
    paper = thermal.paper_for(paper_name)
    target = (target or "").strip()
    if target and paper.thermal:
        try:
            thermal.send_raw(thermal.escpos_document(text.splitlines()), target)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise print_service.PrintError(f"Could not print to {target}: {exc}") from exc
        return
    print_service.print_text(text)


def save_pdf(text: str, paper_name: str, path) -> Path:
    target = Path(path)
    target.write_bytes(thermal.render_pdf(text.splitlines(), thermal.paper_for(paper_name)))
    return target

"""Send plain text to the system printer (macOS / Linux: `lp`; Windows: the default text printer)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from ..errors import POSError


class PrintError(POSError):
    """Printing failed (no printer, no print command, ...)."""


def print_text(text: str) -> None:
    if sys.platform.startswith("win"):
        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        with handle:
            handle.write(text)
        try:
            os.startfile(handle.name, "print")  # type: ignore[attr-defined]
        except OSError as exc:
            raise PrintError(f"Could not print: {exc}") from exc
        return
    command = shutil.which("lp") or shutil.which("lpr")
    if command is None:
        raise PrintError("No print command (lp) was found on this computer.")
    try:
        subprocess.run([command], input=text.encode("utf-8"), check=True, capture_output=True, timeout=30)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip() or "the print command failed"
        raise PrintError(f"Could not print: {detail}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrintError(f"Could not print: {exc}") from exc

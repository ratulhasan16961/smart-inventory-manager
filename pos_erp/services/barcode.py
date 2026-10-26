"""Code 128 barcodes (subsets B and C) for product labels. Pure Python: no libraries and no fonts."""
from __future__ import annotations

from ..errors import ValidationError

PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)
START_B, START_C, STOP = 104, 105, 106
MAX_LENGTH = 40
_LOOKUP = {pattern: value for value, pattern in enumerate(PATTERNS[:STOP])}


def encode_values(text: str) -> list[int]:
    """Symbol values: start, data, checksum, stop. All-digit codes of even length use the compact subset C."""
    if not text or len(text) > MAX_LENGTH:
        raise ValidationError(f"A barcode needs 1 to {MAX_LENGTH} characters.")
    if any(not 32 <= ord(char) <= 126 for char in text):
        raise ValidationError("Barcodes can contain only English letters, digits and basic symbols.")
    if text.isdigit() and len(text) % 2 == 0:
        start, data = START_C, [int(text[i:i + 2]) for i in range(0, len(text), 2)]
    else:
        start, data = START_B, [ord(char) - 32 for char in text]
    checksum = (start + sum(position * value for position, value in enumerate(data, start=1))) % 103
    return [start, *data, checksum, STOP]


def widths(text: str) -> list[int]:
    """Alternating bar / space widths in modules, starting with a bar."""
    return [int(digit) for value in encode_values(text) for digit in PATTERNS[value]]


def total_modules(text: str) -> int:
    return sum(widths(text))


def bar_runs(text: str) -> list[tuple[int, int]]:
    """(offset, width) of every black bar, in modules, without the quiet zone."""
    runs, position = [], 0
    for index, width in enumerate(widths(text)):
        if index % 2 == 0:
            runs.append((position, width))
        position += width
    return runs


def decode(run_widths: list[int]) -> str:
    """The inverse of widths(): used to check the encoder, and handy for tests."""
    if "".join(map(str, run_widths[-7:])) != PATTERNS[STOP] or (len(run_widths) - 7) % 6:
        raise ValueError("not a Code 128 symbol")
    values = [_LOOKUP["".join(map(str, run_widths[i:i + 6]))] for i in range(0, len(run_widths) - 7, 6)]
    start, data, checksum = values[0], values[1:-1], values[-1]
    if (start + sum(position * value for position, value in enumerate(data, start=1))) % 103 != checksum:
        raise ValueError("bad checksum")
    if start == START_C:
        return "".join(f"{value:02d}" for value in data)
    if start == START_B:
        return "".join(chr(value + 32) for value in data)
    raise ValueError("unsupported subset")

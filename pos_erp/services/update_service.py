"""Checks GitHub for a newer release: one read-only HTTPS request. Nothing is downloaded or installed."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .. import __version__
from ..errors import POSError

RELEASES_API = "https://api.github.com/repos/ratulhasan16961/smart-inventory-manager/releases/latest"


class UpdateError(POSError):
    """The update check failed."""


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    url: str
    notes: str
    newer: bool


def parse_version(text: str) -> tuple[int, ...]:
    match = re.match(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    if match is None:
        raise UpdateError(f"Cannot read the version number '{text}'.")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def check(current: str = __version__, opener=None, timeout: float = 8.0) -> UpdateInfo:
    request = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                                            "User-Agent": "enterprise-pos-update-check"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No release has been published yet.") from None
        raise UpdateError(f"GitHub answered with error {exc.code}.") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"Could not check for updates: {exc}") from None
    latest = str(data.get("tag_name") or "")
    return UpdateInfo(current, latest, str(data.get("html_url") or ""), str(data.get("body") or ""),
                      is_newer(latest, current))

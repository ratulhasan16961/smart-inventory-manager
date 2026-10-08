"""Password hashing: salted scrypt (stdlib), with transparent support for legacy SHA-256 hashes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

_N, _R, _P, _DKLEN = 2**14, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024
_CURRENT_PREFIX = f"scrypt${_N}${_R}${_P}$"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM)
    return f"{_CURRENT_PREFIX}{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def legacy_sha256(password: str) -> str:
    """The unsalted format used by the original app. Only ever *verified*, never created."""
    return "sha256$" + hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, stored: str) -> bool:
    try:
        if stored.startswith("scrypt$"):
            _, n, r, p, salt, digest = stored.split("$")
            expected = base64.b64decode(digest)
            actual = hashlib.scrypt(
                password.encode("utf-8"), salt=base64.b64decode(salt),
                n=int(n), r=int(r), p=int(p), dklen=len(expected), maxmem=_MAXMEM,
            )
            return hmac.compare_digest(actual, expected)
        if stored.startswith("sha256$"):
            return hmac.compare_digest(legacy_sha256(password), stored)
    except (ValueError, TypeError):
        return False
    return False


def needs_rehash(stored: str) -> bool:
    return not stored.startswith(_CURRENT_PREFIX)

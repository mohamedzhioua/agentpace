"""Pseudonyms derived from a random local secret.

Nothing about a person goes in: the contributor hash is a fixed label under the secret, so there
is no email or handle for a dictionary attack to guess. The secret never leaves this machine.
Deleting it makes you a new contributor.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

_SECRET_BYTES = 32


def load_secret(home: Path) -> bytes:
    """Read the local secret, creating it on first use with owner-only permissions."""
    path = home / "secret"
    home.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    secret = secrets.token_bytes(_SECRET_BYTES)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(secret.hex())
    return secret


def _digest(secret: bytes, message: str, length: int) -> str:
    return hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()[:length]


def contributor(secret: bytes) -> str:
    return _digest(secret, "contributor-v1", 16)


def batch(secret: bytes, session: str) -> str:
    return _digest(secret, f"batch-v1:{session}", 12)


def project(secret: bytes, folder: str) -> str:
    return _digest(secret, f"project-v1:{folder}", 12)

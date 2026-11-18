"""Encrypts what users entrust the server with: their own provider keys.

A key is sealed with KEYS_SECRET before it is written to Postgres and opened
only when a call is about to be made with it, so a database dump alone gives
none of them away. With no KEYS_SECRET the server takes no keys at all.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

# Shorter than this, the secret could be guessed.
MIN_SECRET_CHARS = 32


class Unreadable(Exception):
    """A sealed value this server's KEYS_SECRET doesn't open: the secret was
    changed since, or the value is damaged."""


def available() -> bool:
    return len(get_settings().keys_secret) >= MIN_SECRET_CHARS


def _box() -> Fernet:
    if not available():
        raise RuntimeError(f"KEYS_SECRET must be at least {MIN_SECRET_CHARS} characters")
    # Fernet wants 32 bytes in base64; any long random secret gives them.
    digest = hashlib.sha256(get_settings().keys_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(text: str) -> str:
    return _box().encrypt(text.encode()).decode()


def unseal(sealed: str) -> str:
    try:
        return _box().decrypt(sealed.encode()).decode()
    except InvalidToken as exc:
        raise Unreadable("a stored key can't be read with this KEYS_SECRET") from exc

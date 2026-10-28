"""Argon2id password hashing.

Argon2id is the OWASP-recommended password hash. argon2-cffi ships sensible
defaults; we expose `hash_password` + `verify_password` so call sites don't
have to know about the hasher singleton.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Single shared hasher — its parameters (time_cost, memory_cost, parallelism)
# are encoded into every hash, so changing them later is backwards-compatible.
_HASHER = PasswordHasher()

# A real hash of a throwaway password, for the unknown-email login path to
# verify against. It must be produced by `_HASHER`: a hand-written literal
# with a too-short salt/output makes argon2 raise instead of doing the work.
DUMMY_PASSWORD_HASH = _HASHER.hash("memory-dump-dummy-password")

MIN_PASSWORD_LEN = 10


class WeakPasswordError(ValueError):
    """Raised when a candidate password fails the minimum policy."""


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise WeakPasswordError(
            f"password must be at least {MIN_PASSWORD_LEN} characters"
        )
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """True if argon2-cffi's defaults have moved past the stored hash's
    parameters — caller should re-hash on next successful login."""
    return _HASHER.check_needs_rehash(password_hash)

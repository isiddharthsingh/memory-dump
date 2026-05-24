"""Access JWT (HS256) + opaque refresh-token helpers.

Access tokens are short-lived stateless JWTs carrying the user id; refresh
tokens are random opaque strings whose sha256 is stored in
`refresh_tokens.token_hash` so we can revoke them. The raw refresh token
only ever lives on the client.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.config import Settings

ACCESS_TOKEN_TYPE = "access"

# 256 bits of entropy, base64url-encoded → ~43 chars.
_REFRESH_TOKEN_BYTES = 32


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    expires_at: datetime


def create_access_token(settings: Settings, user_id: uuid.UUID) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.jwt_access_ttl_min)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "type": ACCESS_TOKEN_TYPE,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    """Returns the claims or raises jwt.PyJWTError on any failure
    (expired, bad signature, wrong type, malformed sub, ...)."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("wrong token type")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise jwt.InvalidTokenError("missing sub")
    try:
        user_id = uuid.UUID(sub)
    except ValueError as e:
        raise jwt.InvalidTokenError("sub is not a uuid") from e
    exp = payload.get("exp")
    if not isinstance(exp, int):
        raise jwt.InvalidTokenError("missing exp")
    return AccessClaims(
        user_id=user_id,
        expires_at=datetime.fromtimestamp(exp, tz=UTC),
    )


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_expiry(settings: Settings) -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days)

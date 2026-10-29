"""APNs delivery and provider-token signing, against a mock transport."""

from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import notifications
from app.config import Settings
from app.notifications import PushTarget, deliver


@pytest.fixture
def p8_key(tmp_path: Path) -> tuple[Path, ec.EllipticCurvePublicKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "AuthKey_TEST.p8"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return path, key.public_key()


@pytest.fixture(autouse=True)
def _reset_token_cache() -> None:
    notifications._provider_token_cache = None


def _settings(key_path: Path) -> Settings:
    return Settings(
        JWT_SECRET="x",
        apns_key_path=str(key_path),
        apns_key_id="KEY123",
        apns_team_id="TEAM456",
    )


def test_provider_token_is_es256_with_kid_and_team(p8_key: tuple) -> None:
    path, public = p8_key
    token = notifications._provider_token(_settings(path), now=1_000_000.0)
    assert jwt.get_unverified_header(token) == {"alg": "ES256", "kid": "KEY123", "typ": "JWT"}
    claims = jwt.decode(token, public, algorithms=["ES256"])
    assert claims == {"iss": "TEAM456", "iat": 1_000_000}


def test_provider_token_is_reused_until_ttl(p8_key: tuple) -> None:
    path, _ = p8_key
    s = _settings(path)
    first = notifications._provider_token(s, now=1_000_000.0)
    assert notifications._provider_token(s, now=1_000_000.0 + 49 * 60) == first
    assert notifications._provider_token(s, now=1_000_000.0 + 50 * 60) != first


async def test_deliver_counts_sent_and_collects_dead_tokens() -> None:
    seen: list[httpx.Request] = []
    replies = {
        "aa": httpx.Response(200),
        "bb": httpx.Response(410, json={"reason": "Unregistered"}),
        "cc": httpx.Response(400, json={"reason": "BadDeviceToken"}),
        "dd": httpx.Response(429, json={"reason": "TooManyRequests"}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies[request.url.path.rsplit("/", 1)[-1]]

    targets = [
        PushTarget("aa", "sandbox"),
        PushTarget("bb", "production"),
        PushTarget("cc", "production"),
        PushTarget("dd", "production"),
    ]
    headers = {"authorization": "bearer t", "apns-topic": "com.example", "apns-push-type": "alert"}
    payload = {"aps": {"alert": {"title": "Save failed", "body": "x"}}, "item_id": "1"}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sent, dead = await deliver(client, targets, headers, payload)

    assert (sent, dead) == (1, ["bb", "cc"])
    assert str(seen[0].url) == "https://api.sandbox.push.apple.com/3/device/aa"
    assert str(seen[1].url) == "https://api.push.apple.com/3/device/bb"
    assert seen[0].headers["apns-topic"] == "com.example"
    assert seen[0].headers["apns-push-type"] == "alert"

"""The circuit breaker app/ai/clients.py keeps for each of the server's
providers."""

from __future__ import annotations

import logging
import time

from app.errors import ProviderUnavailable, is_transient

log = logging.getLogger(__name__)


class Breaker:
    """Stops calling a provider for COOLDOWN_S after THRESHOLD transient
    failures in a row. After the cooldown calls go through again; the next
    failure opens it at once, the next success closes it."""

    THRESHOLD = 5
    COOLDOWN_S = 30.0

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.failures = 0
        self.open_until = 0.0

    def check(self) -> None:
        wait = self.open_until - time.monotonic()
        if wait > 0:
            raise ProviderUnavailable(self.provider, wait)

    def succeeded(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def failed(self, exc: BaseException) -> None:
        if not is_transient(exc) or isinstance(exc, ProviderUnavailable):
            return
        self.failures += 1
        if self.failures >= self.THRESHOLD:
            self.open_until = time.monotonic() + self.COOLDOWN_S
            log.warning(
                "%s failed %d times in a row; not calling it for %ds",
                self.provider,
                self.failures,
                self.COOLDOWN_S,
            )

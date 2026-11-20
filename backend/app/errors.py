"""Which failures a later try can get past, and what a client may see of one.

Transient: a provider that is rate-limiting, overloaded, unreachable or slow; a
site that timed out or answered 5xx; upload storage (SeaweedFS, S3) that is
unreachable or answered 5xx; a provider this process has stopped calling for a
moment (`ProviderUnavailable`). Jobs retry those
(app/workers/jobs.py) and the API answers them with 503 and Retry-After.
Anything else (a bad request, a 404, a page that isn't public) would only fail
the same way again. So would a call past the month's spend limit
(`SpendLimitReached`), until the month ends or the user changes something.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import anthropic
import httpx
from botocore import exceptions as boto_error  # type: ignore[import-untyped]
from voyageai import error as voyage_error

from app.ai import catalog
from app.ai.wire import ProviderError

# Timeout, too early, rate limit, and the server-side failures (529 is
# Anthropic's "overloaded").
TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 529})

_VOYAGE_TRANSIENT = (
    voyage_error.RateLimitError,
    voyage_error.ServiceUnavailableError,
    voyage_error.ServerError,
    voyage_error.Timeout,
    voyage_error.APIConnectionError,
    voyage_error.TryAgain,
)


class ProviderUnavailable(Exception):
    """Claude or Voyage can't take a call now: its circuit is open after
    failures in a row, or every slot of its concurrency cap stayed taken."""

    def __init__(self, provider: str, retry_after: float) -> None:
        super().__init__(f"{provider} is unavailable, try again in {retry_after:.0f}s")
        self.provider = provider
        self.retry_after = retry_after


class Refused(Exception):
    """The model declined the request (stop_reason "refusal"), and so did
    the fallback model when there was one. Asking again gets the same answer.
    `by` names whose model it was when it wasn't Claude."""

    def __init__(self, category: str | None, by: str = "Claude") -> None:
        super().__init__(f"{by} declined this ({category or 'no category given'})")
        self.category = category
        self.by = by


class SpendLimitReached(Exception):
    """The user's spend this month has reached its limit: the free allowance
    on the server's keys, (`own`) the limit they set for their own keys, or
    (`voyage`) what an account on its own model key may use of the server's
    Voyage key. Its text is written for the user; the app shows it as it is."""

    def __init__(self, *, own: bool, limit: Decimal, resets: date, voyage: bool = False) -> None:
        amount, day = f"${limit:.2f}", f"{resets:%B} {resets.day}"
        if own:
            text = (
                f"You've reached the {amount} monthly limit you set for your own key. "
                f"Raise it in Settings, or wait until {day}."
            )
        elif voyage:
            text = (
                f"You've used this month's free {amount} of Voyage, which indexes and "
                f"searches your saves. Add your own Voyage key in Settings, or wait until {day}."
            )
        else:
            text = (
                f"You've used this month's free {amount}. "
                f"Add your own API key in Settings, or wait until {day}."
            )
        super().__init__(text)
        self.own = own
        self.voyage = voyage
        self.limit = limit
        self.resets = resets


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, ProviderUnavailable | TimeoutError | anthropic.APIConnectionError):
        return True
    if isinstance(exc, ProviderError):
        return exc.status_code is None or exc.status_code in TRANSIENT_STATUSES
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code in TRANSIENT_STATUSES
    if isinstance(exc, _VOYAGE_TRANSIENT):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUSES
    if isinstance(exc, boto_error.ConnectionError | boto_error.HTTPClientError):
        return True
    if isinstance(exc, boto_error.ClientError):
        return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") in TRANSIENT_STATUSES
    # A redirect to a scheme httpx can't fetch (intent://, mailto:) fails the
    # same way every time.
    return isinstance(exc, httpx.TransportError) and not isinstance(exc, httpx.UnsupportedProtocol)


def from_site(exc: BaseException) -> bool:
    """Whether the failure came from the site being saved rather than from
    Claude or Voyage. Sites get fewer tries: a dead link mostly stays dead."""
    return isinstance(exc, httpx.HTTPError | TimeoutError)


def retry_after(exc: BaseException) -> float | None:
    """The seconds the provider or site asked us to wait, when it said."""
    if isinstance(exc, ProviderUnavailable | ProviderError):
        return exc.retry_after
    headers = getattr(getattr(exc, "response", None), "headers", None)
    value = headers.get("retry-after") if headers is not None else None
    try:
        return float(value) if value else None
    except ValueError:
        # An HTTP date: the backoff decides instead.
        return None


def user_message(exc: BaseException) -> str:
    """What a client may see of a failure: nothing of its internals."""
    if isinstance(exc, SpendLimitReached):
        return str(exc)
    if isinstance(exc, ProviderError):
        return _own_key_message(exc)
    if is_transient(exc):
        return "That's taking longer than it should right now. Try again in a minute."
    if isinstance(exc, Refused):
        return f"{exc.by} declined to answer that."
    return "Something went wrong. Try again."


def _own_key_message(exc: ProviderError) -> str:
    """A failure on the user's own key says whose it is, so they know to
    look at their account with that provider and not at this app."""
    who = catalog.name(exc.provider)
    if exc.status_code in (401, 403):
        return f"{who} didn't accept your API key. Check it in Settings."
    if exc.status_code in (402, 429):
        return f"{who} says your key is out of credit or over its rate limit."
    if is_transient(exc):
        return f"{who} isn't answering right now. Try again in a minute."
    return f"{who} turned that request down. Try another model in Settings."

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from curl_cffi import requests

from apartment_hunter.sources import BlockedError, ScrapeError


@dataclass(frozen=True)
class HttpResponse:
    status: int
    text: str


def raise_for_status(response: HttpResponse, *, blocked: str, failed: str) -> None:
    """403 and 429 (bot protection, rate limits) raise BlockedError(blocked); any other status but 200
    raises ScrapeError(failed)."""
    if response.status in (403, 429):
        raise BlockedError(blocked)
    if response.status != 200:
        raise ScrapeError(failed)


class HttpClient(Protocol):
    def get(self, url: str) -> HttpResponse: ...


class CurlHttpClient:
    """Plain HTTP with Chrome's TLS fingerprint, no browser."""

    def __init__(
        self, timeout: float = 25.0, impersonate: str = "chrome", headers: Mapping[str, str] | None = None
    ) -> None:
        """`headers` replace Chrome's own on every request (e.g. a User-Agent a service asks for)."""
        self._timeout = timeout
        self._session: requests.Session = requests.Session(  # type: ignore[call-overload]
            impersonate=impersonate, headers=dict(headers or {})
        )

    def get(self, url: str) -> HttpResponse:
        try:
            r = self._session.get(url, timeout=self._timeout)
        except Exception as error:
            raise ScrapeError(f"Couldn't reach {urlsplit(url).hostname}: {error}") from error
        return HttpResponse(r.status_code, r.text)


class PoliteHttpClient:
    """Waits a random gap between requests to the same host."""

    def __init__(
        self,
        inner: HttpClient,
        gap_seconds: tuple[float, float] = (3.0, 8.0),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._inner = inner
        self._gap_seconds = gap_seconds
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._last_request: dict[str, float] = {}

    def get(self, url: str) -> HttpResponse:
        host = urlsplit(url).hostname or ""
        last = self._last_request.get(host)
        if last is not None:
            wait = self._rand(*self._gap_seconds) - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        try:
            return self._inner.get(url)
        finally:
            self._last_request[host] = self._clock()

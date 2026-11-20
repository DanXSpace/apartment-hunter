"""Turn typed text into an Area: a place with its country, not yet placed on any Source.

Each Source places the Area itself (`Source.place`) when a Search is created or its Area changes.
"""

import threading
import time
from collections.abc import Callable
from typing import Protocol

from apartment_hunter.criteria import Area
from apartment_hunter.sources import ScrapeError
from apartment_hunter.sources.http import HttpClient
from apartment_hunter.sources.zillow import Suggestions, best_place, look_up_places

MIN_SUGGEST_CHARACTERS = 3


class AreaNotFound(Exception):
    """The text didn't match a city, neighborhood or ZIP code. The message is plain language."""


class Areas(Protocol):
    def suggest(self, text: str) -> list[str]: ...

    def resolve(self, text: str) -> Area:
        """The place typed, as an Area with its country and no source locations yet."""
        ...


class AreaLookup:
    """Suggests and resolves US places through Zillow's place lookup. Safe to call from several threads.

    The lock guards only the cache and the throttle clock, never an HTTP request, so one slow
    lookup doesn't hold up other autocompletes.
    """

    def __init__(
        self,
        http: HttpClient,
        clock: Callable[[], float] = time.monotonic,
        min_gap_seconds: float = 2.0,
    ) -> None:
        self._http = http
        self._clock = clock
        self._min_gap_seconds = min_gap_seconds
        self._cache: dict[str, Suggestions] = {}
        self._last_request: float | None = None
        self._lock = threading.Lock()

    def suggest(self, text: str) -> list[str]:
        text = text.strip()
        if len(text) < MIN_SUGGEST_CHARACTERS:
            return []
        with self._lock:
            cached = self._cache.get(text.casefold())
            if cached is None:
                now = self._clock()
                if self._last_request is not None and now - self._last_request < self._min_gap_seconds:
                    return []
                self._last_request = now  # take the slot before letting go of the lock
        if cached is not None:
            return [p.display for p in cached.places]
        try:
            return [p.display for p in self._lookup(text).places]
        except ScrapeError:
            return []

    def resolve(self, text: str) -> Area:
        """The place typed exactly, or else Zillow's top result when that is a place. Anything else,
        such as an address listed first, is AreaNotFound rather than a nearby place."""
        text = text.strip()
        with self._lock:
            found = self._cache.get(text.casefold())
            if found is None:
                self._last_request = self._clock()
        if found is None:
            found = self._lookup(text)
        place = best_place(found, text)
        if place is None:
            raise AreaNotFound(
                f'I couldn\'t find a place called "{text}". '
                'Try a city, neighborhood or ZIP code, like "Chicago, IL" or "60614".'
            )
        return Area(place.display, place.kind, "US", place.lat, place.lon)

    def _lookup(self, text: str) -> Suggestions:
        """One request to the place lookup, without the lock; the caller has already taken the slot."""
        found = look_up_places(self._http, text)
        with self._lock:
            self._cache[text.casefold()] = found
        return found

"""Turn typed text into an Area: a Zillow region plus the nearest Craigslist site."""

import json
import math
import threading
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Protocol

from apartment_hunter.criteria import Area
from apartment_hunter.sources import BlockedError, ScrapeError
from apartment_hunter.sources.http import HttpClient

ACCEPTED_KINDS = {"city": 6, "zipcode": 7, "neighborhood": 8}
MIN_SUGGEST_CHARACTERS = 3
CITY_RADIUS_MILES = 10
SMALL_RADIUS_MILES = 2
EARTH_RADIUS_MILES = 3958.8
_LOOKUP_DOWN = "Zillow's place lookup isn't answering right now. Try again in a few minutes."


@dataclass(frozen=True)
class Place:
    display: str
    kind: str
    region_id: int
    lat: float
    lon: float


@dataclass(frozen=True)
class CraigslistSite:
    hostname: str
    lat: float
    lon: float


class AreaNotFound(Exception):
    """The text didn't match a city, neighborhood or ZIP code. The message is plain language."""


class Areas(Protocol):
    def suggest(self, text: str) -> list[str]: ...

    def resolve(self, text: str) -> Area: ...


def suggestions_url(text: str) -> str:
    return "https://www.zillowstatic.com/autocomplete/v3/suggestions?q=" + urllib.parse.quote(text, safe="")


@dataclass(frozen=True)
class Suggestions:
    """What the place lookup offered. `top_is_place` is True when Zillow's first result, before any
    filtering, is one of `places` (an Address or a state listed first makes it False)."""

    places: list[Place]
    top_is_place: bool


def _place(result: object) -> Place | None:
    """The result as a Place when it is a city, neighborhood or ZIP code; otherwise None."""
    if not isinstance(result, dict) or result.get("resultType") != "Region":
        return None
    meta: dict[str, Any] = result.get("metaData") or {}
    kind = meta.get("regionType")
    if kind not in ACCEPTED_KINDS:
        return None
    try:
        return Place(
            str(result["display"]), kind, int(meta["regionId"]), float(meta["lat"]), float(meta["lng"])
        )
    except (KeyError, TypeError, ValueError):
        return None


def read_suggestions(body: str) -> Suggestions:
    try:
        results = json.loads(body).get("results", [])
    except (ValueError, AttributeError):
        return Suggestions([], top_is_place=False)
    found = [_place(result) for result in results]
    places = [place for place in found if place is not None]
    return Suggestions(places, top_is_place=bool(found) and found[0] is not None)


def parse_suggestions(body: str) -> list[Place]:
    return read_suggestions(body).places


def load_craigslist_sites() -> list[CraigslistSite]:
    text = (resources.files("apartment_hunter.sources") / "craigslist_sites.json").read_text()
    return [CraigslistSite(s["hostname"], s["lat"], s["lon"]) for s in json.loads(text)]


def _distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def nearest_craigslist_site(lat: float, lon: float, sites: Sequence[CraigslistSite]) -> str:
    return min(sites, key=lambda s: _distance_miles(lat, lon, s.lat, s.lon)).hostname


class AreaLookup:
    """Suggests and resolves places through Zillow's place lookup. Safe to call from several threads.

    The lock guards only the cache and the throttle clock, never an HTTP request, so one slow
    lookup doesn't hold up other autocompletes.
    """

    def __init__(
        self,
        http: HttpClient,
        sites: Sequence[CraigslistSite] | None = None,
        clock: Callable[[], float] = time.monotonic,
        min_gap_seconds: float = 2.0,
    ) -> None:
        self._http = http
        self._sites = list(sites) if sites is not None else load_craigslist_sites()
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
        exact = [p for p in found.places if p.display.casefold() == text.casefold()]
        place = exact[0] if exact else (found.places[0] if found.top_is_place else None)
        if place is None:
            raise AreaNotFound(
                f'I couldn\'t find a place called "{text}". '
                'Try a city, neighborhood or ZIP code, like "Chicago, IL" or "60614".'
            )
        radius = CITY_RADIUS_MILES if place.kind == "city" else SMALL_RADIUS_MILES
        return Area(
            place.display,
            place.kind,
            place.region_id,
            ACCEPTED_KINDS[place.kind],
            place.lat,
            place.lon,
            nearest_craigslist_site(place.lat, place.lon, self._sites),
            radius,
        )

    def _lookup(self, text: str) -> Suggestions:
        """One request to the place lookup, without the lock; the caller has already taken the slot."""
        try:
            response = self._http.get(suggestions_url(text))
        except ScrapeError:
            raise
        except Exception as error:
            raise ScrapeError(_LOOKUP_DOWN) from error
        if response.status in (403, 429):
            raise BlockedError(_LOOKUP_DOWN)
        if response.status != 200:
            raise ScrapeError(_LOOKUP_DOWN)
        found = read_suggestions(response.text)
        with self._lock:
            self._cache[text.casefold()] = found
        return found

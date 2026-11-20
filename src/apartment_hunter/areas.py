"""Turn typed text into an Area: a place with its country, not yet placed on any Source.

Places come from Photon (photon.komoot.io), the OpenStreetMap search service, limited to the countries
in the countries table. Each Source places the Area itself (`Source.place`) when a Search is created
or its Area changes.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from apartment_hunter.countries import COUNTRIES
from apartment_hunter.criteria import Area
from apartment_hunter.sources import ScrapeError
from apartment_hunter.sources.http import HttpClient, raise_for_status
from apartment_hunter.text import join_and

MIN_SUGGEST_CHARACTERS = 3
PHOTON_URL = "https://photon.komoot.io/api/"
# Photon's fair-use policy asks for a User-Agent that names the application (and nothing personal).
PHOTON_USER_AGENT = "apartment-hunter-discord-bot"
PHOTON_LIMIT = 25  # as many as a Discord autocomplete shows
LOOKUP_DOWN = "The place lookup isn't answering right now. Try again in a few minutes."

# OpenStreetMap's place=* values a Search accepts, by Area kind. Counties, states, hamlets, single
# farms, streets and addresses aren't Areas.
_KINDS = {
    "city": "city", "town": "city", "village": "city",
    "suburb": "neighborhood", "neighbourhood": "neighborhood", "quarter": "neighborhood",
    "borough": "neighborhood", "city_district": "neighborhood",
    "postcode": "zipcode",
}
US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN",
    "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY", "Puerto Rico": "PR",
}


class AreaNotFound(Exception):
    """The text didn't match a city, neighborhood or postcode. The message is plain language."""


class Areas(Protocol):
    def suggest(self, text: str) -> list[str]: ...

    def resolve(self, text: str) -> Area:
        """The place typed, as an Area with its country and no source locations yet."""
        ...

    def cached(self, text: str) -> Area | None:
        """What `resolve(text)` would give, from earlier lookups only (no request); None when `text`
        hasn't been looked up or found no place."""
        ...


@dataclass(frozen=True)
class Suggestions:
    """What the place lookup offered, as Areas. `top_is_place` is True when Photon's first result is
    one of `places` (an address, a street or a county listed first makes it False)."""

    places: list[Area]
    top_is_place: bool


def photon_url(text: str) -> str:
    """Photon's search for `text`, in English, limited to the supported countries."""
    params = [("q", text), ("limit", str(PHOTON_LIMIT)), ("lang", "en")]
    params += [("countrycode", code) for code in COUNTRIES]
    return PHOTON_URL + "?" + urlencode(params)


def _display(props: dict[str, Any], code: str) -> str:
    """ "Camden Town, London, United Kingdom", "Lincoln Park, Chicago, IL, United States"."""
    name = str(props["name"])
    parts = [name]
    city = props.get("city")
    if code == "US" and isinstance(city, str) and city.endswith(" Township"):
        city = None  # OpenStreetMap files some US neighborhoods under a township, not the city
    if city and city != name:
        parts.append(str(city))
    state = props.get("state")
    if code == "US":
        if state in US_STATES:
            parts.append(US_STATES[state])
    elif not city and state and state != name:
        parts.append(str(state))
    parts.append(COUNTRIES[code].name)
    return ", ".join(parts)


def _area(feature: object) -> Area | None:
    """The feature as an Area when it's an accepted kind of place in a supported country."""
    if not isinstance(feature, dict):
        return None
    props: dict[str, Any] = feature.get("properties") or {}
    kind = _KINDS.get(props.get("osm_value", "")) if props.get("osm_key") == "place" else None
    code = props.get("countrycode")
    if kind is None or code not in COUNTRIES or not props.get("name"):
        return None
    if kind == "city" and props.get("type") == "district":  # a town swallowed by a city: Camden Town
        kind = "neighborhood"
    try:
        lon, lat = (float(c) for c in feature["geometry"]["coordinates"])
        extent = props.get("extent")
        # Photon's extent is (west, north, east, south); an Area's bbox is (south, west, north, east).
        bbox = (float(extent[3]), float(extent[0]), float(extent[1]), float(extent[2])) if extent else None
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return Area(_display(props, code), kind, code, lat, lon, bbox)


def read_photon(body: str) -> Suggestions:
    """Photon's answer as Areas, without duplicates, in Photon's order."""
    try:
        features = json.loads(body).get("features", [])
    except (ValueError, AttributeError):
        return Suggestions([], top_is_place=False)
    found = [_area(feature) for feature in features]
    places: list[Area] = []
    for area in found:
        if area is not None and all(p.display != area.display for p in places):
            places.append(area)
    return Suggestions(places, top_is_place=bool(found) and found[0] is not None)


def look_up_places(http: HttpClient, text: str) -> Suggestions:
    """One request to Photon; ScrapeError (or BlockedError) when it doesn't answer."""
    try:
        response = http.get(photon_url(text))
    except ScrapeError:
        raise
    except Exception as error:
        raise ScrapeError(LOOKUP_DOWN) from error
    raise_for_status(response, blocked=LOOKUP_DOWN, failed=LOOKUP_DOWN)
    return read_photon(response.text)


def best_place(found: Suggestions, text: str) -> Area | None:
    """The place called exactly `text` (any case), or else the top result when that is a place."""
    exact = [p for p in found.places if p.display.casefold() == text.casefold()]
    return exact[0] if exact else (found.places[0] if found.top_is_place else None)


class AreaLookup:
    """Suggests and resolves places through Photon. Safe to call from several threads.

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

    def cached(self, text: str) -> Area | None:
        text = text.strip()
        with self._lock:
            found = self._cache.get(text.casefold())
        return best_place(found, text) if found is not None else None

    def resolve(self, text: str) -> Area:
        """The place typed exactly, or else Photon's top result when that is a place. Anything else,
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
            countries = join_and([c.name for c in COUNTRIES.values()])
            raise AreaNotFound(
                f'I couldn\'t find a place called "{text}" in the countries I cover ({countries}). '
                'Try a city, neighborhood or postcode, like "Chicago, IL", "Camden, London" or "60614".'
            )
        return place

    def _lookup(self, text: str) -> Suggestions:
        """One request to the place lookup, without the lock; the caller has already taken the slot."""
        found = look_up_places(self._http, text)
        with self._lock:
            self._cache[text.casefold()] = found
        return found

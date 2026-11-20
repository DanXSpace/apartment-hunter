"""The Fotocasa Source (Spain): places an Area on a Fotocasa location (a city, or a district or
neighbourhood inside one), turns Criteria into a newest-first rental search, and reads Listings from the
results page's embedded data."""

import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from scrapling.parser import Selector

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, SourceLocation
from apartment_hunter.models import Listing
from apartment_hunter.sources import NO_HOME_TYPE_NOTES, Filter, ScrapeError, SourcePage
from apartment_hunter.sources.common import (
    check_response,
    doesnt_know,
    layout_changed,
    look_up,
    no_postcodes,
    same_name,
    slug,
    unreadable,
)
from apartment_hunter.sources.http import HttpClient
from apartment_hunter.text import plain_number

_SITE = "https://www.fotocasa.es"
NAME = "Fotocasa"
NEWEST_FIRST = "publicationDate"  # Fotocasa's sortType for "most recent"
# Property types as Fotocasa's path segment ("viviendas" is every kind of home).
PROPERTY_TYPES = {
    HomeType.APARTMENT_CONDO: "pisos",
    HomeType.HOUSE: "todas-las-casas",
    HomeType.TOWNHOME: "adosados",
}
PETS_ALLOWED = "se-aceptan-mascotas"  # Fotocasa has one pets filter: pets accepted
MONTHLY = 3  # Fotocasa's periodicityId for a rent per month
# Fotocasa's building subtypes as a listing's name says them; others are spelled out ("SemiDetached").
SUBTYPE_NAMES = {"Flat": "flat", "Apartment": "apartment", "Attic": "penthouse", "Study": "studio"}
# Cities Photon names in English (lang=en) whose Spanish name Fotocasa's URLs use.
SPANISH_NAMES = {"Seville": "Sevilla", "Saragossa": "Zaragoza", "Corunna": "A Coruña"}
LOOKUP_DOWN = (
    "Fotocasa isn't answering right now, so its places couldn't be looked up. Try again in a few minutes."
)
_BLOCK_MARKERS = ("captcha-delivery", "Access Denied", "captcha")
_LAYOUT_CHANGED = layout_changed(NAME)


def city_url(city: str) -> str:
    """The page of every rental in a city, which also lists the city's districts and neighbourhoods.
    Fotocasa writes names as slugs: "Barrio de Salamanca" is "barrio-de-salamanca"."""
    return f"{_SITE}/es/alquiler/viviendas/{slug(SPANISH_NAMES.get(city, city))}/todas-las-zonas/l"


def _zones(facets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Districts and the neighbourhoods inside them."""
    found = []
    for facet in facets:
        found.append(facet)
        found.extend(_zones(facet.get("subLocations") or []))
    return found


def find_zone(html: str, name: str | None) -> str | None:
    """From a city's page, the location path to save: the whole city ("madrid-capital/todas-las-zonas")
    when `name` is None, else the district or neighbourhood called `name` ("madrid-capital/chamberi"), or
    None when the page has no such zone or no data."""
    try:
        props = _props(html)
        if props is None:
            return None
        city = str(props["searchContext"]["locationSegment"])
        if name is None:
            return f"{city}/todas-las-zonas"
        facets = props["initialSearch"]["result"]["resultsV2"]["locationFacets"]["edistricts"]
        for zone in _zones(facets):
            if same_name(str(zone.get("literal") or ""), name) and zone.get("segments"):
                return str(zone["segments"])
    except (ScrapeError, KeyError, TypeError, AttributeError):
        return None
    return None


def search_url(criteria: Criteria, page: int = 1) -> str:
    """The rental search for the saved location. Home type and pets are path segments, the page
    number follows "/l", and the other filters are query parameters."""
    path = str(criteria.area.locations["fotocasa"]["path"])
    segments = [PROPERTY_TYPES.get(criteria.home_type, "viviendas"), path]
    if criteria.pets is not Pets.NONE:
        segments.append(PETS_ALLOWED)
    segments.append("l" if page == 1 else f"l/{page}")
    params = [("sortType", NEWEST_FIRST)]
    if criteria.min_price is not None:
        params.append(("minPrice", str(criteria.min_price)))
    if criteria.max_price is not None:
        params.append(("maxPrice", str(criteria.max_price)))
    if criteria.min_beds:  # Fotocasa's rooms are bedrooms; a studio minimum (0) needs no filter
        params.append(("minRooms", str(math.ceil(criteria.min_beds))))
    if criteria.min_baths is not None and criteria.min_baths >= 1:  # whole bathrooms; round down
        params.append(("minBathrooms", str(math.floor(criteria.min_baths))))
    return f"{_SITE}/es/alquiler/" + "/".join(segments) + "?" + urlencode(params)


def _features(raw: dict[str, Any]) -> dict[str, float]:
    return {f["key"]: float(f["value"]) for f in raw.get("features") or []
            if isinstance(f.get("value"), int | float)}


def _kind(raw: dict[str, Any]) -> str:
    subtype = str(raw.get("buildingSubtype") or "")
    return SUBTYPE_NAMES.get(subtype, re.sub(r"(?<!^)(?=[A-Z])", " ", subtype).lower() or "home")


def _listing(raw: dict[str, Any], scraped_at: datetime) -> Listing:
    features, kind = _features(raw), _kind(raw)
    rooms, baths = features.get("rooms"), features.get("bathrooms")
    # Fotocasa's rooms are bedrooms, but a studio counts its one room: a studio has 0 bedrooms.
    beds = 0.0 if kind == "studio" else rooms
    if kind == "studio":
        name = "Studio"
    elif beds is not None:
        name = f"{plain_number(beds)} bedroom {kind}"
    else:
        name = kind.capitalize()
    # periodicityId 3 is per month, the only period seen on rentals; any other is unknown.
    price = raw.get("rawPrice") if raw.get("periodicityId", MONTHLY) == MONTHLY else None
    price = int(price) if isinstance(price, int | float) and price > 0 else None
    town = (raw.get("address") or {}).get("municipality") or ""
    address = ", ".join(part for part in (raw.get("location"), town) if part)
    images = [m["src"] for m in raw.get("multimedia") or [] if m.get("type") == "image" and m.get("src")]
    return Listing(
        source="fotocasa", listing_id=str(raw["id"]), url=_SITE + str(raw["detail"]["es-ES"]).split("?")[0],
        name=name, address=address, price_min=price, price_max=price,
        beds_min=beds, beds_max=beds, baths_min=baths, baths_max=baths,
        image_url=images[0] if images else None, scraped_at=scraped_at,
    )


def _props(html: str) -> dict[str, Any] | None:
    scripts = Selector(html).css("script#__initial_props__")
    if not scripts:
        return None
    try:
        data = json.loads(str(scripts[0].text))
    except ValueError as error:
        raise ScrapeError(_LAYOUT_CHANGED) from error
    return data if isinstance(data, dict) else None


def parse_search_page(html: str, scraped_at: datetime) -> SourcePage:
    """The results page's Listings, newest first. Sorted by date, Fotocasa's paid "top" adverts stay in
    their place in the list, so none are left out."""
    props = _props(html)
    if props is None:
        raise unreadable(html, NAME, _BLOCK_MARKERS)
    try:
        result = props["initialSearch"]["result"]
        listings = [_listing(raw, scraped_at) for raw in result["realEstates"]]
        total = int(result["count"])
        page = result["resultsV2"]["page"]
        has_more = int(page["number"]) * int(page["size"]) < total
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ScrapeError(_LAYOUT_CHANGED) from error
    return SourcePage(listings, total, has_more)


class FotocasaSource:
    """Reads Fotocasa. Places an Area on a city, district or neighbourhood page with one request to the
    city's page, using `lookup_http` (default: the Runs' client) so placing needn't wait behind a Run's
    polite gaps. Fotocasa filters by everything the Criteria hold; its rooms are bedrooms."""

    key = "fotocasa"
    filters = frozenset({Filter.PRICE, Filter.BEDS, Filter.BATHS, Filter.HOME_TYPE, Filter.PETS})
    home_type_notes = NO_HOME_TYPE_NOTES

    def __init__(
        self,
        http: HttpClient,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        lookup_http: HttpClient | None = None,
    ) -> None:
        self._http = http
        self._now = now
        self._lookup_http = lookup_http or http

    def place(self, area: Area) -> SourceLocation:
        """Opens the Area's city on Fotocasa (Fotocasa sends a plain city name on to the city's own
        page, e.g. "madrid" to "madrid-capital"); a district or neighbourhood is then looked up among
        that page's zones by name."""
        if area.kind == "zipcode":
            raise no_postcodes(NAME, "the district or city")
        city = area.context[0] if area.kind != "city" and area.context else area.name
        unknown = str(doesnt_know(NAME, area))
        answer = look_up(self._lookup_http, city_url(city), NAME, down=LOOKUP_DOWN,
                         left_out=lambda status: unknown if status == 404 else None)
        path = find_zone(answer, None if area.kind == "city" else area.name)
        if path is None:
            raise doesnt_know(NAME, area)
        return {"path": path}

    def results_url(self, criteria: Criteria) -> str:
        return search_url(criteria, 1)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(search_url(criteria, page))
        check_response(response, NAME)
        return parse_search_page(response.text, self._now())

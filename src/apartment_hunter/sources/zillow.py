"""The Zillow Source: turns Criteria into a newest-first rentals search and its page into Listings."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from scrapling.parser import Selector

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, SourceLocation
from apartment_hunter.geo import distance_miles
from apartment_hunter.models import Listing
from apartment_hunter.sources import (
    ALL_FILTERS,
    NO_HOME_TYPE_NOTES,
    BlockedError,
    CantPlace,
    ScrapeError,
    SourcePage,
)
from apartment_hunter.sources.http import HttpClient, raise_for_status

SEARCH_URL = "https://www.zillow.com/homes/for_rent/"
# Zillow's region types for the place kinds a Search accepts.
ACCEPTED_KINDS = {"city": 6, "zipcode": 7, "neighborhood": 8}
LOOKUP_DOWN = "Zillow's place lookup isn't answering right now. Try again in a few minutes."
MAX_PLACE_MILES = 20  # a Zillow region farther than this from the Area is some other place
_SITE = "https://www.zillow.com"

_BASE_FILTERS: dict[str, Any] = {
    "sortSelection": {"value": "days"},
    "isForRent": {"value": True},
    "isForSaleByAgent": {"value": False},
    "isForSaleByOwner": {"value": False},
    "isNewConstruction": {"value": False},
    "isComingSoon": {"value": False},
    "isAuction": {"value": False},
    "isForSaleForeclosure": {"value": False},
}
_EXCLUDED_HOME_TYPES = {
    HomeType.HOUSE: (
        "isApartment", "isCondo", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand",
    ),
    HomeType.APARTMENT_CONDO: (
        "isSingleFamily", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand",
    ),
    HomeType.TOWNHOME: (
        "isSingleFamily", "isApartment", "isCondo", "isMultiFamily", "isManufactured", "isLotLand",
    ),
}
_ACCEPTED_HOME_TYPES = {
    HomeType.APARTMENT_CONDO: {"APARTMENT", "CONDO", "BUILDING"},
    HomeType.HOUSE: {"SINGLE_FAMILY"},
    HomeType.TOWNHOME: {"TOWNHOUSE"},
}
_PET_FILTERS = {
    Pets.CATS: "onlyRentalCatsAllowed",
    Pets.SMALL_DOGS: "onlyRentalSmallDogsAllowed",
    Pets.LARGE_DOGS: "onlyRentalLargeDogsAllowed",
}
_BLOCK_MARKERS =("px-captcha", "Press & Hold")
_BLOCKED = (
    "Zillow is blocking requests from this computer right now (bot protection). "
    "This usually clears up on its own."
)
_LAYOUT_CHANGED = "Zillow's page layout changed, so listings couldn't be read. The bot needs an update."


@dataclass(frozen=True)
class ZillowCard:
    listing: Listing
    home_type: str


@dataclass(frozen=True)
class Place:
    """A city, neighborhood or ZIP code from Zillow's place lookup."""

    display: str
    kind: str
    region_id: int
    lat: float
    lon: float


@dataclass(frozen=True)
class Suggestions:
    """What the place lookup offered. `top_is_place` is True when Zillow's first result, before any
    filtering, is one of `places` (an Address or a state listed first makes it False)."""

    places: list[Place]
    top_is_place: bool


def suggestions_url(text: str) -> str:
    return "https://www.zillowstatic.com/autocomplete/v3/suggestions?q=" + quote(text, safe="")


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


def place_query(area: Area) -> str:
    """What to ask Zillow's place lookup for an Area: "Chicago, IL", "Lincoln Park, IL" or "60614".

    Areas resolved by Photon read "Lincoln Park, IL, United States"; older ones "Chicago, IL"."""
    parts = area.display.split(", ")
    if len(parts) > 1 and parts[-1] == "United States":
        parts = parts[:-1]
    if area.kind == "zipcode" or len(parts) == 1:
        return parts[0]
    return f"{parts[0]}, {parts[-1]}"


def nearby_place(found: Suggestions, area: Area, query: str) -> Place | None:
    """The place called exactly `query`, or else the nearest place of the Area's kind within
    MAX_PLACE_MILES of the Area; None when Zillow offered nothing like it near it. A place of another
    kind (the city around a neighbourhood) would search a different area, so it never stands in."""
    near = [p for p in found.places if distance_miles(area.lat, area.lon, p.lat, p.lon) <= MAX_PLACE_MILES]
    exact = [p for p in near if p.display.casefold() == query.casefold()]
    if exact:
        return exact[0]
    same_kind = [p for p in near if p.kind == area.kind]
    return min(same_kind, key=lambda p: distance_miles(area.lat, area.lon, p.lat, p.lon), default=None)


def look_up_places(http: HttpClient, text: str) -> Suggestions:
    """One request to Zillow's place lookup; ScrapeError (or BlockedError) when it doesn't answer."""
    try:
        response = http.get(suggestions_url(text))
    except ScrapeError:
        raise
    except Exception as error:
        raise ScrapeError(LOOKUP_DOWN) from error
    raise_for_status(response, blocked=LOOKUP_DOWN, failed=LOOKUP_DOWN)
    return read_suggestions(response.text)


def search_url(criteria: Criteria, page: int = 1) -> str:
    filters: dict[str, Any] = dict(_BASE_FILTERS)
    payment: dict[str, int] = {}
    if criteria.min_price is not None:
        payment["min"] = criteria.min_price
    if criteria.max_price is not None:
        payment["max"] = criteria.max_price
    if payment:
        filters["monthlyPayment"] = payment
    if criteria.min_beds is not None:
        filters["beds"] = {"min": criteria.min_beds}
    if criteria.min_baths is not None:
        filters["baths"] = {"min": criteria.min_baths}
    if criteria.pets is not Pets.NONE:
        filters[_PET_FILTERS[criteria.pets]] = {"value": True}
    for name in _EXCLUDED_HOME_TYPES.get(criteria.home_type, ()):
        filters[name] = {"value": False}
    location = criteria.area.locations["zillow"]
    state = {
        "pagination": {"currentPage": page} if page > 1 else {},
        "isMapVisible": False,
        "isListVisible": True,
        "regionSelection": [
            {"regionId": int(location["region_id"]), "regionType": int(location["region_type"])}
        ],
        "filterState": filters,
    }
    return SEARCH_URL + "?searchQueryState=" + quote(json.dumps(state, separators=(",", ":")))


def matches_home_type(home_type: str, wanted: HomeType) -> bool:
    return wanted is HomeType.ANY or home_type in _ACCEPTED_HOME_TYPES[wanted]


def _dollars(text: object) -> int | None:
    digits = re.sub(r"[^\d]", "", str(text or "").split(".")[0])
    return int(digits) if digits else None


def _number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _absolute(url: str) -> str:
    return _SITE + url if url.startswith("/") else url


def _card(raw: dict[str, Any], scraped_at: datetime) -> ZillowCard:
    if raw.get("isBuilding"):
        units = raw.get("units") or []
        prices = [p for p in (_dollars(u.get("price")) for u in units) if p is not None]
        unit_beds = [b for b in (_number(u.get("beds")) for u in units) if b is not None]
        listing = Listing(
            source="zillow", listing_id=str(raw["zpid"]), url=_absolute(raw.get("detailUrl", "")),
            name=raw.get("buildingName") or "", address=raw.get("address") or "",
            price_min=min(prices) if prices else None, price_max=max(prices) if prices else None,
            beds_min=min(unit_beds) if unit_beds else None, beds_max=max(unit_beds) if unit_beds else None,
            baths_min=None, baths_max=None, image_url=raw.get("imgSrc"), scraped_at=scraped_at,
        )
        return ZillowCard(listing, "BUILDING")
    price = raw.get("unformattedPrice")
    price = int(price) if isinstance(price, int | float) else None
    beds, baths = _number(raw.get("beds")), _number(raw.get("baths"))
    listing = Listing(
        source="zillow", listing_id=str(raw["zpid"]), url=_absolute(raw.get("detailUrl", "")), name="",
        address=raw.get("address") or "", price_min=price, price_max=price,
        beds_min=beds, beds_max=beds, baths_min=baths, baths_max=baths,
        image_url=raw.get("imgSrc"), scraped_at=scraped_at,
    )
    home_type = ((raw.get("hdpData") or {}).get("homeInfo") or {}).get("homeType") or ""
    return ZillowCard(listing, home_type)


def parse_search_page(html: str, scraped_at: datetime) -> tuple[list[ZillowCard], int | None, bool]:
    if any(marker in html for marker in _BLOCK_MARKERS):
        raise BlockedError(_BLOCKED)
    scripts = Selector(html).css("script#__NEXT_DATA__")
    if not scripts:
        raise ScrapeError(_LAYOUT_CHANGED)
    try:
        data = json.loads(str(scripts[0].text))
        cat1 = data["props"]["pageProps"]["searchPageState"]["cat1"]
        raw_cards = cat1["searchResults"]["listResults"]
        search_list = cat1["searchList"]
        cards = [_card(raw, scraped_at) for raw in raw_cards]
    except (ValueError, KeyError, TypeError) as error:
        raise ScrapeError(_LAYOUT_CHANGED) from error
    total = search_list.get("totalResultCount")
    has_more = bool((search_list.get("pagination") or {}).get("nextUrl"))
    return cards, total if isinstance(total, int) else None, has_more


class ZillowSource:
    """Reads Zillow. Places an Area on a Zillow region through Zillow's place lookup, using
    `lookup_http` (default: the Runs' client) so placing needn't wait behind a Run's polite gaps."""

    key = "zillow"
    filters = ALL_FILTERS
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
        query = place_query(area)
        found = nearby_place(look_up_places(self._lookup_http, query), area, query)
        if found is None:
            raise CantPlace(f"Zillow doesn't know {area.display}.")
        return {"region_id": found.region_id, "region_type": ACCEPTED_KINDS[found.kind]}

    def results_url(self, criteria: Criteria) -> str:
        return search_url(criteria, 1)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(search_url(criteria, page))
        raise_for_status(
            response, blocked=_BLOCKED, failed=f"Zillow answered with an error (HTTP {response.status})."
        )
        cards, total, has_more = parse_search_page(response.text, self._now())
        listings = [c.listing for c in cards if matches_home_type(c.home_type, criteria.home_type)]
        return SourcePage(listings, total, has_more)

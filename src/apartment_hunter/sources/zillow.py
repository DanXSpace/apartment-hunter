"""The Zillow Source: turns Criteria into a newest-first rentals search and its page into Listings."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from scrapling.parser import Selector

from apartment_hunter.criteria import Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import BlockedError, ScrapeError, SourcePage
from apartment_hunter.sources.http import HttpClient

SEARCH_URL = "https://www.zillow.com/homes/for_rent/"
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
_BLOCK_MARKERS = ("px-captcha", "Press & Hold")
_BLOCKED = (
    "Zillow is blocking requests from this computer right now (bot protection). "
    "This usually clears up on its own."
)
_LAYOUT_CHANGED = "Zillow's page layout changed, so listings couldn't be read. The bot needs an update."


@dataclass(frozen=True)
class ZillowCard:
    listing: Listing
    home_type: str


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
    if criteria.pets is Pets.DOGS:
        filters["onlyRentalSmallDogsAllowed"] = {"value": True}
    elif criteria.pets is Pets.CATS:
        filters["onlyRentalCatsAllowed"] = {"value": True}
    for name in _EXCLUDED_HOME_TYPES.get(criteria.home_type, ()):
        filters[name] = {"value": False}
    state = {
        "pagination": {"currentPage": page} if page > 1 else {},
        "isMapVisible": False,
        "isListVisible": True,
        "regionSelection": [
            {"regionId": criteria.area.zillow_region_id, "regionType": criteria.area.zillow_region_type}
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
    key = "zillow"

    def __init__(self, http: HttpClient, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._http = http
        self._now = now

    def results_url(self, criteria: Criteria) -> str:
        return search_url(criteria, 1)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(search_url(criteria, page))
        if response.status in (403, 429):
            raise BlockedError(_BLOCKED)
        if response.status != 200:
            raise ScrapeError(f"Zillow answered with an error (HTTP {response.status}).")
        cards, total, has_more = parse_search_page(response.text, self._now())
        listings = [c.listing for c in cards if matches_home_type(c.home_type, criteria.home_type)]
        return SourcePage(listings, total, has_more)

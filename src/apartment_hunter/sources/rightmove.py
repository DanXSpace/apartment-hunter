"""The Rightmove Source (UK): places an Area on a Rightmove location through Rightmove's typeahead, turns
Criteria into a newest-first rental search, and reads Listings from the results page's embedded data."""

import json
import math
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from scrapling.parser import Selector

from apartment_hunter.criteria import Area, Criteria, HomeType, SourceLocation
from apartment_hunter.models import Listing
from apartment_hunter.sources import NO_HOME_TYPE_NOTES, Filter, ScrapeError, SourcePage
from apartment_hunter.sources.common import (
    check_response,
    find_by_name,
    in_context,
    layout_changed,
    look_up,
    per_month,
    same_name,
    unreadable,
    with_note,
)
from apartment_hunter.sources.http import HttpClient
from apartment_hunter.text import plain_number

SEARCH_URL = "https://www.rightmove.co.uk/property-to-rent/find.html"
TYPEAHEAD_URL = "https://los.rightmove.co.uk/typeahead"
PAGE_SIZE = 24
NEWEST_FIRST = "6"  # Rightmove's sortType for "Newest listed"
# The typeahead's location types a Search can use (not stations, schools or streets), and the search
# radius in miles for each: a full postcode alone is a handful of buildings.
LOCATION_RADIUS = {"REGION": 0.0, "OUTCODE": 0.0, "POSTCODE": 0.25}
PROPERTY_TYPES = {
    HomeType.APARTMENT_CONDO: "flat",
    HomeType.HOUSE: "detached,semi-detached,terraced,bungalow",
    HomeType.TOWNHOME: "terraced",
}
# Rent periods as Rightmove names them.
PERIODS = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year"}
NAME = "Rightmove"
_SITE = "https://www.rightmove.co.uk"
_BLOCK_MARKERS = ("Access Denied", "captcha")


@dataclass(frozen=True)
class RightmoveLocation:
    """One match from Rightmove's typeahead, e.g. REGION 85262 "Camden Town, North West London"."""

    kind: str
    id: str
    display: str

    @property
    def identifier(self) -> str:
        """The locationIdentifier a search takes: "REGION^85262"."""
        return f"{self.kind}^{self.id}"


def typeahead_url(text: str) -> str:
    return f"{TYPEAHEAD_URL}?query={quote(text, safe='')}&limit=10&exclude=STREET"


def parse_typeahead(body: str) -> list[RightmoveLocation]:
    """The typeahead's answer (XML) as the locations a Search can use; a garbled answer has none."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []
    found = []
    for match in root.iter("matches"):
        kind, id_, display = match.findtext("type"), match.findtext("id"), match.findtext("displayName")
        if kind in LOCATION_RADIUS and id_ and display:
            found.append(RightmoveLocation(kind, id_, display))
    return found


def best_location(found: list[RightmoveLocation], area: Area, query: str) -> RightmoveLocation | None:
    """The location named exactly `query`, preferring one whose display mentions the rest of the
    Area ("London" for "Camden Town, London, United Kingdom"); None when nothing is named so."""
    named = [loc for loc in found if same_name(loc.display.split(",")[0], query)]
    best = [loc for loc in named if in_context(loc.display, area)] or named
    return best[0] if best else None


def search_url(criteria: Criteria, page: int = 1) -> str:
    location = criteria.area.locations["rightmove"]
    params = [
        ("locationIdentifier", str(location["location_id"])),
        ("radius", str(float(location["radius_miles"]))),
        ("sortType", NEWEST_FIRST),
        ("channel", "RENT"),
    ]
    if page > 1:
        params.append(("index", str((page - 1) * PAGE_SIZE)))
    if criteria.min_price is not None:
        params.append(("minPrice", str(criteria.min_price)))
    if criteria.max_price is not None:
        params.append(("maxPrice", str(criteria.max_price)))
    if criteria.min_beds is not None:
        params.append(("minBedrooms", plain_number(criteria.min_beds)))
    if criteria.min_baths is not None:  # whole bathrooms only; round down so nothing is passed over
        params.append(("minBathrooms", str(math.floor(criteria.min_baths))))
    if criteria.home_type in PROPERTY_TYPES:
        params.append(("propertyTypes", PROPERTY_TYPES[criteria.home_type]))
    return SEARCH_URL + "?" + urlencode(params, safe="^,")


def monthly_rent(price: dict[str, Any]) -> int | None:
    """A rent per calendar month, whatever period the listing shows: £500 pw is £2,167 pcm."""
    amount, period = price.get("amount"), PERIODS.get(price.get("frequency") or "")
    if not isinstance(amount, int | float) or period is None:
        return None
    return per_month(amount, period)


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _listing(raw: dict[str, Any], scraped_at: datetime) -> Listing:
    price = monthly_rent(raw.get("price") or {})
    beds, baths = _number(raw.get("bedrooms")), _number(raw.get("bathrooms"))
    images = raw.get("images") or []
    url = str(raw.get("propertyUrl") or f"/properties/{raw['id']}").split("#")[0]
    return Listing(
        source="rightmove", listing_id=str(raw["id"]), url=_SITE + url if url.startswith("/") else url,
        name=raw.get("propertyTypeFullDescription") or "", address=raw.get("displayAddress") or "",
        price_min=price, price_max=price, beds_min=beds, beds_max=beds, baths_min=baths, baths_max=baths,
        image_url=images[0].get("srcUrl") if images else None, scraped_at=scraped_at,
    )


def parse_search_page(html: str, scraped_at: datetime) -> SourcePage:
    """The results page's Listings, newest first. Featured cards are adverts pinned above the results
    in any order, so they're left out; each one also shows up in its place in the newest-first list."""
    scripts = Selector(html).css("script#__NEXT_DATA__")
    if not scripts:
        raise unreadable(html, NAME, _BLOCK_MARKERS)
    try:
        results = json.loads(str(scripts[0].text))["props"]["pageProps"]["searchResults"]
        listings = [_listing(raw, scraped_at) for raw in results["properties"]
                    if not raw.get("featuredProperty")]
        count = str(results.get("resultCount") or "").replace(",", "")
        has_more = bool((results.get("pagination") or {}).get("next"))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ScrapeError(layout_changed(NAME)) from error
    return SourcePage(listings, int(count) if count.isdigit() else None, has_more)


class RightmoveSource:
    """Reads Rightmove. Places an Area on a Rightmove location (region, postcode district) through
    Rightmove's typeahead, using `lookup_http` (default: the Runs' client) so placing needn't wait
    behind a Run's polite gaps. Rightmove has no pets filter."""

    key = "rightmove"
    filters = frozenset({Filter.PRICE, Filter.BEDS, Filter.BATHS, Filter.HOME_TYPE})
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
        """The location named like the Area's own name. A full postcode is searched as Rightmove's
        postcode location when the typeahead offers it, else as its outward code ("SW11" for
        "SW11 6HR"), with a note saying so."""
        found, note = find_by_name(area, NAME, lambda query: self._look_up(area, query))
        return with_note({"location_id": found.identifier, "radius_miles": LOCATION_RADIUS[found.kind]}, note)

    def _look_up(self, area: Area, query: str) -> RightmoveLocation | None:
        answer = look_up(self._lookup_http, typeahead_url(query), NAME)
        return best_location(parse_typeahead(answer), area, query)

    def results_url(self, criteria: Criteria) -> str:
        return search_url(criteria, 1)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(search_url(criteria, page))
        check_response(response, NAME)
        return parse_search_page(response.text, self._now())

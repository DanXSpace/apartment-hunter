"""The Zoopla Source (UK): places an Area on a Zoopla place through Zoopla's own search box, turns
Criteria into a newest-first rental search, and reads Listings from the data the results page embeds."""

import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import quote, urlencode

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, SourceLocation
from apartment_hunter.models import Listing
from apartment_hunter.sources import ALL_FILTERS, ScrapeError, SourcePage
from apartment_hunter.sources.common import (
    check_response,
    find_by_name,
    layout_changed,
    look_up,
    per_month,
    same_name,
    unreadable,
    with_note,
)
from apartment_hunter.sources.http import HttpClient
from apartment_hunter.text import plain_number

_SITE = "https://www.zoopla.co.uk"
SEARCH_BOX_URL = f"{_SITE}/search/"
NAME = "Zoopla"
NEWEST_FIRST = "newest_listings"  # Zoopla's results_sort for "Most recent"
# The search path's property type: /to-rent/flats/<place>/. Zoopla has no terraced-only search, so a
# townhome looks at every house.
PROPERTY_PATHS = {
    HomeType.ANY: "property",
    HomeType.APARTMENT_CONDO: "flats",
    HomeType.HOUSE: "houses",
    HomeType.TOWNHOME: "houses",
}
# Rent periods in Zoopla's price labels.
PERIODS = {"pcm": "month", "pw": "week", "pq": "quarter", "pa": "year"}
_PRICE = re.compile(r"£(?P<amount>[\d,]+(?:\.\d+)?)\s*(?P<period>[a-z]+)")
_BLOCK_MARKERS = ("Just a moment", "Access Denied", "captcha")
# Next.js streams the page's data in chunks: <script>self.__next_f.push([1,"<JSON string>"])</script>.
_DATA_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', re.S)


def place_search_url(query: str) -> str:
    """Zoopla's search box: it redirects to the results page of the place it understood the text as,
    or to a "we could not find a place name" page."""
    return f"{SEARCH_BOX_URL}?{urlencode([('q', query.casefold()), ('section', 'to-rent')])}"


def found_place(html: str, query: str) -> str | None:
    """The place identifier ("camden-town", "london/croydon", "sw11") of the results page the search
    box landed on, when that place is named like `query`; None when Zoopla found no such place. A full
    postcode's page is labelled with its street ("Bolingbroke Grove, London SW11") and named by its
    identifier's last part ("london/bolingbroke-grove/sw11-6hr")."""
    try:
        geo = embedded_value(page_data(html), "geoData")
        identifier, label = str(geo["geoIdentifier"]), str(geo["label"])
    except (ValueError, KeyError, TypeError):
        return None
    names = (label.split(",")[0], identifier.rsplit("/", 1)[-1])
    return identifier if identifier and any(same_name(query, name) for name in names) else None


def search_url(criteria: Criteria, page: int = 1) -> str:
    location = criteria.area.locations["zoopla"]
    path = f"/to-rent/{PROPERTY_PATHS[criteria.home_type]}/{quote(str(location['geo']), safe='/')}/"
    params = [("results_sort", NEWEST_FIRST)]
    if page > 1:
        params.append(("pn", str(page)))
    if criteria.min_price is not None:
        params.append(("price_min", str(criteria.min_price)))
    if criteria.max_price is not None:
        params.append(("price_max", str(criteria.max_price)))
    if criteria.min_beds is not None:
        params.append(("beds_min", plain_number(criteria.min_beds)))
    if criteria.min_baths is not None:  # whole bathrooms only; round down so nothing is passed over
        params.append(("baths_min", str(math.floor(criteria.min_baths))))
    if criteria.pets is not Pets.NONE:
        params.append(("pets_allowed", "true"))
    return f"{_SITE}{path}?{urlencode(params)}"


def page_data(html: str) -> str:
    """The page's embedded Next.js data, its chunks joined back into one text."""
    return "".join(json.loads(chunk) for chunk in _DATA_CHUNK.findall(html))


def embedded_value(data: str, key: str) -> Any:
    """The JSON value after the first `"key":` in the page data; KeyError when there's none."""
    found = re.search(rf'"{re.escape(key)}":\s*', data)
    if found is None:
        raise KeyError(key)
    value, _ = json.JSONDecoder().raw_decode(data, found.end())
    return value


def monthly_rent(label: str) -> int | None:
    """A rent per calendar month from the price Zoopla shows: "£1,148 pcm" is 1148, "£500 pw" is 2167
    (52 weeks over 12 months); None for "POA" or a period it doesn't know."""
    found = _PRICE.search(label)
    if found is None or found["period"] not in PERIODS:
        return None
    return per_month(float(found["amount"].replace(",", "")), PERIODS[found["period"]])


def _feature(raw: dict[str, Any], icon: str) -> float | None:
    """A count from a card's features ({"iconId": "bed", "content": 2}); featured cards hold a
    reference string there instead."""
    features = raw.get("features")
    for feature in features if isinstance(features, list) else []:
        if feature.get("iconId") == icon and isinstance(feature.get("content"), int | float):
            return float(feature["content"])
    return None


def _listing(raw: dict[str, Any], scraped_at: datetime) -> Listing:
    title = str(raw.get("title") or "").removesuffix(" to rent")
    price = monthly_rent(str(raw.get("price") or ""))
    beds = _feature(raw, "bed")
    if beds is None and title.casefold().startswith("studio"):
        beds = 0.0
    baths = _feature(raw, "bath")
    detail = str((raw.get("listingUris") or {}).get("detail") or f"/to-rent/details/{raw['listingId']}/")
    return Listing(
        source="zoopla", listing_id=str(raw["listingId"]), url=_SITE + detail.split("#")[0],
        name=title, address=raw.get("address") or "",
        price_min=price, price_max=price, beds_min=beds, beds_max=beds, baths_min=baths, baths_max=baths,
        image_url=(raw.get("image") or {}).get("src"), scraped_at=scraped_at,
    )


def parse_search_page(html: str, scraped_at: datetime) -> SourcePage:
    """The results page's Listings, newest first. Featured adverts (pinned above the results in any
    order) and the "similar properties" Zoopla adds below a short list are kept apart in the page's
    data, so only the regular results are read."""
    try:
        data = page_data(html)
    except ValueError as error:
        raise ScrapeError(layout_changed(NAME)) from error
    if '"regularListingsFormatted"' not in data:
        raise unreadable(html, NAME, _BLOCK_MARKERS)
    try:
        listings = [_listing(raw, scraped_at) for raw in embedded_value(data, "regularListingsFormatted")]
        pagination = embedded_value(data, "pagination")
        total = int(pagination["totalResults"])
        has_more = int(pagination["pageNumber"]) < int(pagination["pageNumberMax"])
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ScrapeError(layout_changed(NAME)) from error
    return SourcePage(listings, total, has_more)


def _server_error(status: int) -> str | None:
    if status < 500:
        return None
    return (f"Zoopla's place search answered with an error (HTTP {status}). "
            "Try adding it again later with /search edit.")


class ZooplaSource:
    """Reads Zoopla. Places an Area through Zoopla's own search box, using `lookup_http` (default: the
    Runs' client) so placing needn't wait behind a Run's polite gaps. Zoopla can apply every filter,
    but has no townhome-only search: a townhome search looks at every house, and replies say so."""

    key = "zoopla"
    filters = ALL_FILTERS
    home_type_notes: Mapping[HomeType, str] = MappingProxyType({
        HomeType.TOWNHOME: "Zoopla can't tell townhomes from houses, so you'll see all houses."})

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
        """The place named like the Area's own name. A full postcode is searched as itself when
        Zoopla knows it, else as its outward code ("SW11" for "SW11 6HR"), with a note saying so."""
        geo, note = find_by_name(area, NAME, self._look_up)
        return with_note({"geo": geo}, note)

    def _look_up(self, query: str) -> str | None:
        """The place Zoopla's search box lands on for `query`. The search box sometimes answers with a
        server error for a while; that leaves Zoopla out of this Search rather than refusing it."""
        answer = look_up(self._lookup_http, place_search_url(query), NAME, left_out=_server_error)
        return found_place(answer, query)

    def results_url(self, criteria: Criteria) -> str:
        return search_url(criteria, 1)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(search_url(criteria, page))
        check_response(response, NAME)
        return parse_search_page(response.text, self._now())

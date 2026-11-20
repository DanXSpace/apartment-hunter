"""The Pararius Source (Netherlands): places an Area on a Pararius city, area, district or neighbourhood
through Pararius's place suggestions, turns Criteria into a rental search (Pararius encodes filters in
the URL path) and reads Listings from the results list's HTML cards."""

import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

from scrapling.parser import Selector

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, SourceLocation
from apartment_hunter.models import Listing
from apartment_hunter.sources import Filter, ScrapeError, SourcePage
from apartment_hunter.sources.common import (
    check_response,
    doesnt_know,
    in_context,
    layout_changed,
    look_up,
    no_postcodes,
    same_name,
    unreadable,
)
from apartment_hunter.sources.http import HttpClient

_SITE = "https://www.pararius.com"
SUGGEST_URL = f"{_SITE}/api/suggest"
PLACE_KINDS = ("city", "city_area", "neighbourhood", "district")  # the suggestions a Search can use
NAME = "Pararius"
# The kinds of place below a city that Pararius searches, as the path segment each one takes.
PLACE_SEGMENTS = {"city_area": "city-area", "district": "district", "neighbourhood": "neighbourhood"}
DWELLING_TYPES = {HomeType.APARTMENT_CONDO: "apartment", HomeType.HOUSE: "house", HomeType.TOWNHOME: "house"}
_BLOCK_MARKERS = ("captcha", "Just a moment", "Access Denied")
_LAYOUT_CHANGED = layout_changed(NAME)
NEWEST_FIRST = "published_at.desc"  # the sort Pararius selects by default ("Newest first")
NO_MAXIMUM = 60000  # how Pararius writes a price range with no maximum ("2000-60000")


def suggest_url(text: str) -> str:
    return f"{SUGGEST_URL}?q={quote(text, safe='')}" + "".join(f"&types[]={kind}" for kind in PLACE_KINDS)


def parse_suggestions(body: str) -> list[dict[str, Any]]:
    """Pararius's place suggestions (JSON) that a Search can use; a garbled answer has none."""
    try:
        found = json.loads(body)["result"]
        return [s for s in found if s["type"] in PLACE_KINDS and s["filters"]["city"]]
    except (ValueError, KeyError, TypeError):
        return []


def best_suggestion(found: list[dict[str, Any]], area: Area) -> dict[str, Any] | None:
    """The suggestion named like the Area's own name, preferring one in the rest of the Area ("De Pijp,
    Amsterdam" over "De Pijp, Beverwijk"), then one of the same kind (a city for a city). City areas
    carry their city's name in front ("The Hague Centrum"). None when nothing is named so."""
    def named(suggestion: dict[str, Any]) -> bool:
        own = str(suggestion["value"]).split(", ")[0]
        return any(same_name(own, name) for name in (area.name, *(f"{c} {area.name}" for c in area.context)))

    def rank(suggestion: dict[str, Any]) -> tuple[bool, bool]:
        same_kind = (suggestion["type"] == "city") == (area.kind == "city")
        return not in_context(str(suggestion["value"]), area), not same_kind

    matches = sorted((s for s in found if named(s)), key=rank)
    return matches[0] if matches else None


def search_url(criteria: Criteria, page: int = 1) -> str:
    location = criteria.area.locations["pararius"]
    path = [str(location["city"])]
    path += [f"{segment}-{location[kind]}" for kind, segment in PLACE_SEGMENTS.items() if kind in location]
    if criteria.home_type in DWELLING_TYPES:
        path.append(DWELLING_TYPES[criteria.home_type])
    if criteria.min_price is not None or criteria.max_price is not None:
        path.append(f"{criteria.min_price or 0}-{criteria.max_price or NO_MAXIMUM}")
    if criteria.min_beds:  # whole bedrooms; 0 (a studio) needs no filter
        path.append(f"{math.ceil(criteria.min_beds)}-bedrooms")
    if criteria.pets is not Pets.NONE:
        path.append("pets-allowed")
    if page > 1:
        path.append(f"page-{page}")
    return f"{_SITE}/apartments/" + "/".join(path)


def _text(node: Selector, css: str) -> str:
    found = node.css(css)
    return " ".join(str(found[0].get_all_text(strip=True)).split()) if found else ""


def _whole_number(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _photo(card: Selector) -> str | None:
    """The card's first photo. Photos below the fold sit in a <template> behind a data: placeholder."""
    for img in card.css("img.picture__image"):
        src = str(img.attrib.get("src", ""))
        if src.startswith("http"):
            return src
    return None


def _listing(item: Selector, scraped_at: datetime) -> Listing:
    card = item.css("wc-listing-search-item")[0]
    href = str(card.css("a.listing-search-item__link--title")[0].attrib["href"]).split("#")[0]
    price = _whole_number(_text(card, ".listing-search-item__price-main"))
    rooms = _whole_number(_text(card, ".illustrated-features__item--number-of-rooms"))
    beds = max(rooms - 1, 0) if rooms is not None else None  # rooms count the living room
    return Listing(
        source="pararius", listing_id=str(card.attrib["data-id"]),
        url=_SITE + href if href.startswith("/") else href, name=_text(card, ".listing-search-item__title"),
        address=_text(card, ".listing-search-item__sub-title"),
        price_min=price, price_max=price, beds_min=beds, beds_max=beds, baths_min=None, baths_max=None,
        image_url=_photo(card), scraped_at=scraped_at,
    )


def _searched(page: Selector, attribute: str) -> dict[str, Any]:
    """What the page says it searched: the JSON Pararius puts in a <body> data attribute."""
    found: dict[str, Any] = json.loads(str(page.css("body")[0].attrib[attribute]))
    return found


def _selected_sort(page: Selector) -> str | None:
    """The sort the results list says it uses ("published_at.desc"); None when it shows none."""
    selected = page.css('select[name="search-list-sorting"] option[selected]')
    return str(selected[0].attrib.get("value")) if selected else None


def parse_search_page(
    html: str, scraped_at: datetime, location: SourceLocation, page_number: int
) -> SourcePage:
    """The results list's Listings, newest first. Highlighted cards are paid adverts pinned to the top
    in any order, so they're left out. The search takes no sort, so the page must say it sorted newest
    first (Pararius's default); otherwise the layout changed rather than the Listings coming in the
    wrong order.

    Pararius quietly widens a search it has nothing for: a district (or city area, neighbourhood) with
    no matches answers with the whole city's, and a page past the last one with page 1. Those answers
    are read as an empty page so they aren't taken for this place's Listings."""
    page = Selector(html)
    if not page.css(".search-list-header__count"):
        raise unreadable(html, NAME, _BLOCK_MARKERS)
    try:
        searched = _searched(page, "data-search-page-filters")["filters"]
        shown = _searched(page, "data-search-page-view-options")["view_options"]["page"]
        if any(searched.get(kind) != value for kind, value in location.items()) or shown != page_number:
            return SourcePage([], 0 if page_number == 1 else None, False)
        listings = [_listing(item, scraped_at) for item in page.css("li.search-list__item--listing")
                    if not item.css(".listing-search-item--featured")]
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        raise ScrapeError(_LAYOUT_CHANGED) from error
    if listings and _selected_sort(page) != NEWEST_FIRST:
        raise ScrapeError(_LAYOUT_CHANGED)
    total = _whole_number(_text(page, ".search-list-header__count"))
    return SourcePage(listings, total, bool(page.css("a.pagination__link--next")))


class ParariusSource:
    """Reads Pararius. Pararius has no bathrooms filter, and its cards show rooms (counting the living
    room), not bedrooms."""

    key = "pararius"
    filters = frozenset({Filter.PRICE, Filter.BEDS, Filter.HOME_TYPE, Filter.PETS})
    home_type_notes: Mapping[HomeType, str] = MappingProxyType({
        HomeType.TOWNHOME: "Pararius can't tell townhomes from houses, so you'll see all houses."})

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
        if area.kind == "zipcode":
            raise no_postcodes(NAME, "the neighbourhood or city")
        answer = look_up(self._lookup_http, suggest_url(area.name), NAME)
        found = best_suggestion(parse_suggestions(answer), area)
        if found is None:
            raise doesnt_know(NAME, area)
        filters = found["filters"]
        location: SourceLocation = {"city": str(filters["city"])}
        location.update({kind: str(filters[kind]) for kind in PLACE_SEGMENTS if filters.get(kind)})
        return location

    def results_url(self, criteria: Criteria) -> str:
        return search_url(criteria, 1)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(search_url(criteria, page))
        check_response(response, NAME)
        return parse_search_page(response.text, self._now(), criteria.area.locations[self.key], page)

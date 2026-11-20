"""The Craigslist Source: Criteria into a newest-first apartment search, and its static list into Listings."""

import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlencode

from scrapling.parser import Selector

from apartment_hunter.criteria import Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import ScrapeError, SourcePage
from apartment_hunter.sources.http import HttpClient, raise_for_status
from apartment_hunter.text import plain_number

# Craigslist's apartment, condo, flat, loft / cottage, duplex, house / townhouse.
HOUSING_TYPES = {
    HomeType.APARTMENT_CONDO: (1, 2, 5, 8),
    HomeType.HOUSE: (3, 4, 6),
    HomeType.TOWNHOME: (9,),
}
_BLOCKED = (
    "Craigslist is blocking requests from this computer right now. This usually clears up on its own."
)
_LAYOUT_CHANGED = (
    "Craigslist's page layout changed, so listings couldn't be read. The bot needs an update."
)


def search_url(criteria: Criteria) -> str:
    area = criteria.area
    params: list[tuple[str, str]] = [
        ("sort", "date"),
        ("lat", str(area.lat)),
        ("lon", str(area.lon)),
        ("search_distance", str(area.craigslist_radius_miles)),
    ]
    if criteria.min_price is not None:
        params.append(("min_price", plain_number(criteria.min_price)))
    if criteria.max_price is not None:
        params.append(("max_price", plain_number(criteria.max_price)))
    if criteria.min_beds is not None:
        params.append(("min_bedrooms", plain_number(criteria.min_beds)))
    if criteria.min_baths is not None:
        params.append(("min_bathrooms", plain_number(criteria.min_baths)))
    if criteria.pets in (Pets.SMALL_DOGS, Pets.LARGE_DOGS):  # Craigslist has one dogs filter
        params.append(("pets_dog", "1"))
    elif criteria.pets is Pets.CATS:
        params.append(("pets_cat", "1"))
    params.extend(("housing_type", str(code)) for code in HOUSING_TYPES.get(criteria.home_type, ()))
    return f"https://{area.craigslist_site}.craigslist.org/search/apa?" + urlencode(params)


def _text(node: Selector, css: str) -> str:
    found = node.css(css)
    return str(found[0].text).strip() if found else ""


def parse_search_page(html: str, scraped_at: datetime) -> list[Listing]:
    lists = Selector(html).css("ol.cl-static-search-results")
    if not lists:
        raise ScrapeError(_LAYOUT_CHANGED)
    listings = []
    for item in lists[0].css("li.cl-static-search-result"):
        links = item.css("a")
        if not links:
            continue
        url = str(links[0].attrib.get("href", ""))
        digits = re.sub(r"[^\d]", "", _text(item, ".price"))
        price = int(digits) if digits else None
        listings.append(Listing(
            source="craigslist", listing_id=url.rstrip("/").rsplit("/", 1)[-1], url=url,
            name=_text(item, ".title"), address=_text(item, ".location"),
            price_min=price, price_max=price, beds_min=None, beds_max=None,
            baths_min=None, baths_max=None, image_url=None, scraped_at=scraped_at,
        ))
    return listings


class CraigslistSource:
    key = "craigslist"

    def __init__(self, http: HttpClient, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._http = http
        self._now = now

    def results_url(self, criteria: Criteria) -> str:
        return search_url(criteria)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        if page > 1:
            return SourcePage([], None, False)
        response = self._http.get(search_url(criteria))
        raise_for_status(
            response, blocked=_BLOCKED, failed=f"Craigslist answered with an error (HTTP {response.status})."
        )
        return SourcePage(parse_search_page(response.text, self._now()), None, False)

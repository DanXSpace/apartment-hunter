"""The Idealista Source, configured once for Italy (idealista.it) and once for Portugal (idealista.pt).

Idealista's place lookup refuses plain requests, but its "draw your area" search takes any polygon, so
an Area is placed by drawing its bounding box (no request). Criteria become path segments
("con-prezzo_2000,trilocali-3"), and Listings are read from the results list's HTML cards.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from scrapling.parser import Selector

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, SourceLocation
from apartment_hunter.models import Listing
from apartment_hunter.sources import (
    ALL_FILTERS,
    NO_HOME_TYPE_NOTES,
    BlockedError,
    Filter,
    ScrapeError,
    SourcePage,
)
from apartment_hunter.sources.http import HttpClient, raise_for_status

# Half the side of the square drawn around an Area that has no bounding box, in kilometres.
HALF_SIDE_KM = {"city": 5.0, "neighborhood": 1.0, "zipcode": 1.0}
_KM_PER_DEGREE = 111.32
_BLOCK_MARKERS = ("captcha-delivery", "datadome")
_BLOCKED = (
    "Idealista is blocking requests from this computer right now (bot protection). "
    "This usually clears up on its own."
)
_LAYOUT_CHANGED = "Idealista's page layout changed, so listings couldn't be read. The bot needs an update."


@dataclass(frozen=True)
class IdealistaSite:
    """One country's Idealista: its address and the words its URLs and cards use.

    `rooms` and `baths` are the site's options as (count, segment), lowest first; each option means
    exactly that count except the last, which means that many or more. `rooms_per_bedroom_offset` is
    how many rooms a listing has beyond its bedrooms (Italy counts the living room; Portugal's T
    numbers count bedrooms). `separators` split a card's title into its name and address."""

    key: str
    host: str
    path: str
    with_word: str
    page_word: str
    order: tuple[str, str]
    max_price: str
    min_price: str
    home_types: dict[HomeType, str]
    rooms: tuple[tuple[int, str], ...]
    rooms_offset: int
    rooms_pattern: str
    baths: tuple[tuple[int, str], ...]
    pets: str | None
    separators: tuple[str, ...]
    monthly: tuple[str, ...]

    @property
    def filters(self) -> frozenset[Filter]:
        return ALL_FILTERS if self.pets else ALL_FILTERS - {Filter.PETS}


ITALY = IdealistaSite(
    key="idealista_it", host="www.idealista.it", path="aree/affitto-case", with_word="con", page_word="lista",
    order=("ordine", "pubblicazione-desc"), max_price="prezzo_{}", min_price="prezzo-min_{}",
    home_types={HomeType.APARTMENT_CONDO: "appartamenti", HomeType.HOUSE: "ville",
                HomeType.TOWNHOME: "villette-a-schiera"},
    rooms=((1, "monolocali-1"), (2, "bilocali-2"), (3, "trilocali-3"), (4, "quadrilocali-4"),
           (5, "5-locali-o-piu")),
    rooms_offset=1, rooms_pattern=r"(\d+)\+?\s+local[ei]\b",
    baths=((1, "bagno-1"), (2, "bagno-2"), (3, "bagno-3")), pets="animali",
    separators=("in",), monthly=("€/mese",),
)
PORTUGAL = IdealistaSite(
    key="idealista_pt", host="www.idealista.pt", path="areas/arrendar-casas", with_word="com",
    page_word="pagina", order=("ordem", "atualizado-desc"),
    max_price="preco-max_{}", min_price="preco-min_{}",
    home_types={HomeType.APARTMENT_CONDO: "apartamentos", HomeType.HOUSE: "moradias",
                HomeType.TOWNHOME: "moradias-em-banda"},
    rooms=((0, "t0"), (1, "t1"), (2, "t2"), (3, "t3"), (4, "t4-t5")),
    rooms_offset=0, rooms_pattern=r"T(\d+)\+?",
    baths=((1, "banho-1"), (2, "banho-2"), (3, "banho-3")), pets=None,  # idealista.pt has no pets filter
    separators=("na", "no", "nas", "nos", "em"), monthly=("€/mês",),
)


def _encode_number(value: int) -> str:
    value = ~(value << 1) if value < 0 else value << 1
    out = ""
    while value >= 0x20:
        out += chr((0x20 | (value & 0x1F)) + 63)
        value >>= 5
    return out + chr(value + 63)


def polyline(points: list[tuple[float, float]]) -> str:
    """(lat, lon) points in Google's encoded polyline format, which Idealista's `shape` takes."""
    encoded, last_lat, last_lon = "", 0, 0
    for lat, lon in points:
        lat_e5, lon_e5 = round(lat * 1e5), round(lon * 1e5)
        encoded += _encode_number(lat_e5 - last_lat) + _encode_number(lon_e5 - last_lon)
        last_lat, last_lon = lat_e5, lon_e5
    return encoded


def area_box(area: Area) -> tuple[float, float, float, float]:
    """The Area's bounding box (south, west, north, east), or a square around its point when the place
    lookup gave none: 1 km each way for a neighbourhood or postcode, 5 km for a city."""
    if area.bbox is not None:
        return area.bbox
    half = HALF_SIDE_KM.get(area.kind, 1.0)
    dlat = half / _KM_PER_DEGREE
    dlon = half / (_KM_PER_DEGREE * math.cos(math.radians(area.lat)))
    return (area.lat - dlat, area.lon - dlon, area.lat + dlat, area.lon + dlon)


def area_shape(area: Area) -> str:
    """The Area's box as a closed polygon, starting at its north-west corner and going clockwise."""
    south, west, north, east = area_box(area)
    return polyline([(north, west), (north, east), (south, east), (south, west), (north, west)])


def _at_least(options: tuple[tuple[int, str], ...], count: int) -> list[str]:
    """The options for `count` or more: every option from `count` up, or the last ("or more") one."""
    return [segment for n, segment in options if n >= count] or [options[-1][1]]


def search_url(site: IdealistaSite, criteria: Criteria, page: int = 1) -> str:
    location = criteria.area.locations[site.key]
    segments = []
    if criteria.max_price is not None:
        segments.append(site.max_price.format(criteria.max_price))
    if criteria.min_price is not None:
        segments.append(site.min_price.format(criteria.min_price))
    if criteria.home_type in site.home_types:
        segments.append(site.home_types[criteria.home_type])
    if criteria.min_beds is not None and criteria.min_beds > 0:  # a studio minimum filters nothing out
        segments += _at_least(site.rooms, math.ceil(criteria.min_beds) + site.rooms_offset)
    if criteria.min_baths is not None and math.floor(criteria.min_baths) > 1:  # round down; 1+ is anything
        segments += _at_least(site.baths, math.floor(criteria.min_baths))
    if site.pets and criteria.pets is not Pets.NONE:
        segments.append(site.pets)
    path = f"https://{site.host}/{site.path}/"
    if segments:
        path += f"{site.with_word}-{','.join(segments)}/"
    if page > 1:
        path += f"{site.page_word}-{page}"
    shape = quote(f"(({location['shape']}))", safe="")
    return f"{path}?shape={shape}&{site.order[0]}={site.order[1]}"


def _split_title(site: IdealistaSite, title: str) -> tuple[str, str]:
    """"Trilocale in Via Roma, Roma" → ("Trilocale", "Via Roma, Roma"); a title without an address is
    all name."""
    words = "|".join(re.escape(word) for word in site.separators)
    match = re.match(rf"(.+?) (?:{words}) (.+)", title)
    return (match[1], match[2]) if match else (title, "")


def _price(site: IdealistaSite, text: str) -> int | None:
    """"1.600 €/mese" → 1600; a price on request or for another period → None."""
    digits = text.split("€")[0].replace(".", "").strip()
    if not digits.isdigit() or not any(text.endswith(period) for period in site.monthly):
        return None
    return int(digits)


def _beds(site: IdealistaSite, details: list[str]) -> float | None:
    """Bedrooms from the card's room count: "3 locali" is 2 bedrooms, "T2" is 2."""
    for detail in details:
        match = re.fullmatch(site.rooms_pattern, detail.strip())
        if match:
            return float(max(int(match[1]) - site.rooms_offset, 0))
    return None


def _listing(site: IdealistaSite, card: Selector, scraped_at: datetime) -> Listing:
    link = card.css("a.item-link")[0]
    title = " ".join(str(link.attrib.get("title") or link.text).split())
    name, address = _split_title(site, title)
    price = _price(site, str(card.css(".item-price")[0].attrib.get("data-price") or ""))
    beds = _beds(site, [str(detail.text) for detail in card.css(".item-detail")])
    images = card.css(".item-gallery img")
    return Listing(
        source=site.key, listing_id=str(card.attrib["data-element-id"]),
        url=f"https://{site.host}{link.attrib['href']}".split("#")[0], name=name, address=address,
        price_min=price, price_max=price, beds_min=beds, beds_max=beds, baths_min=None, baths_max=None,
        image_url=str(images[0].attrib["src"]) if images else None, scraped_at=scraped_at,
    )


def parse_search_page(site: IdealistaSite, html: str, scraped_at: datetime) -> SourcePage:
    """The results list's Listings, newest first. Highlighted cards are kept: on a newest-first page
    they sit in their own place. The total is the heading's leading number ("3.463 case in affitto");
    a no-matches heading has none."""
    page = Selector(html)
    heading = page.css("#h1-container__text")
    if not heading:  # look for a block page only here: a listing's own text could hold the words
        if any(marker in html for marker in _BLOCK_MARKERS):
            raise BlockedError(_BLOCKED)
        raise ScrapeError(_LAYOUT_CHANGED)
    try:
        listings = [_listing(site, card, scraped_at) for card in page.css("article.item[data-element-id]")]
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        raise ScrapeError(_LAYOUT_CHANGED) from error
    count = re.match(r"\s*([\d.]+)", str(heading[0].text))
    total = int(count[1].replace(".", "")) if count else 0
    return SourcePage(listings, total, bool(page.css(".pagination li.next a")))


class IdealistaSource:
    """Reads one country's Idealista. Places an Area by drawing its bounding box, so placing makes no
    request and never fails."""

    def __init__(
        self, site: IdealistaSite, http: HttpClient, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.key = site.key
        self.filters = site.filters
        self.home_type_notes = NO_HOME_TYPE_NOTES
        self._site = site
        self._http = http
        self._now = now

    def place(self, area: Area) -> SourceLocation:
        return {"shape": area_shape(area)}

    def results_url(self, criteria: Criteria) -> str:
        return search_url(self._site, criteria, 1)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(search_url(self._site, criteria, page))
        # Idealista answers a search with no matches with HTTP 404 and its usual results page.
        if not (response.status == 404 and "h1-container__text" in response.text):
            failed = f"Idealista answered with an error (HTTP {response.status})."
            raise_for_status(response, blocked=_BLOCKED, failed=failed)
        return parse_search_page(self._site, response.text, self._now())

"""The Kleinanzeigen Source (Germany): places an Area on a Kleinanzeigen location through the site's own
place suggestions, turns Criteria into a newest-first search of rental offers, and reads Listings from
the results list's HTML cards."""

import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlencode

from scrapling.parser import Selector

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, SourceLocation
from apartment_hunter.models import Listing
from apartment_hunter.sources import (
    NO_HOME_TYPE_NOTES,
    BlockedError,
    CantPlace,
    Filter,
    ScrapeError,
    SourcePage,
)
from apartment_hunter.sources.http import HttpClient, raise_for_status

_SITE = "https://www.kleinanzeigen.de"
# Flats and houses to rent are separate categories: (path, category id, attribute prefix). Kleinanzeigen
# has no terraced-only search, so a townhome looks at every house.
FLATS = ("s-wohnung-mieten", 203, "wohnung_mieten")
HOUSES = ("s-haus-mieten", 205, "haus_mieten")
CATEGORIES = {HomeType.ANY: FLATS, HomeType.APARTMENT_CONDO: FLATS, HomeType.HOUSE: HOUSES,
              HomeType.TOWNHOME: HOUSES}
NEWEST_FIRST = "sortierung:neuste"  # the site's own spelling; "neueste" is ignored
OFFERS_ONLY = "anzeige:angebote"  # leaves out "Gesuche", people looking for a flat
_PRICE = re.compile(r"(?P<amount>\d[\d.]*) €(?: VB)?|VB")
_ROOMS = re.compile(r"(\d+(?:,\d+)?) Zi\.")
_COUNTS = re.compile(r"(?P<first>[\d.]+)\s*-\s*(?P<last>[\d.]+) von (?P<total>[\d.]+)")
_BLOCK_MARKERS = ("captcha", "Access Denied", "Just a moment")
_BLOCKED = (
    "Kleinanzeigen is blocking requests from this computer right now (bot protection). "
    "This usually clears up on its own."
)
_LAYOUT_CHANGED = (
    "Kleinanzeigen's page layout changed, so listings couldn't be read. The bot needs an update."
)
SUGGEST_URL = f"{_SITE}/s-ort-empfehlungen.json"
LOOKUP_DOWN = "Kleinanzeigen's place lookup isn't answering right now. Try again in a few minutes."
# The place lookup (Photon) spells some German cities in English; Kleinanzeigen knows only the German name.
GERMAN_NAMES = {
    "Munich": "München", "Cologne": "Köln", "Nuremberg": "Nürnberg", "Hanover": "Hannover",
    "Brunswick": "Braunschweig", "Frankfurt": "Frankfurt am Main",
}
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slug(name: str) -> str:
    """A place name as Kleinanzeigen writes it in its URLs: "Neukölln" is "neukoelln"."""
    return re.sub(r"[^a-z0-9]+", "-", name.casefold().translate(_UMLAUTS)).strip("-")


def suggest_url(text: str) -> str:
    return f"{SUGGEST_URL}?{urlencode([('query', text)])}"


def parse_suggestions(body: str) -> list[tuple[int, str]]:
    """The lookup's answer as (location id, name) pairs, in its order: {"_3331": "Berlin", "_3518":
    "Mitte - Berlin"}. The whole country ("_0") is left out; a garbled answer has none."""
    try:
        found = json.loads(body)
    except ValueError:
        return []
    if not isinstance(found, dict):
        return []
    return [(int(key[1:]), str(name)) for key, name in found.items()
            if isinstance(key, str) and key[1:].isdigit() and int(key[1:]) != 0]


def matching_location(found: list[tuple[int, str]], area: Area, query: str) -> SourceLocation | None:
    """For a postcode, the first location for it ("10999 Kreuzberg"). Otherwise a location named like
    `query` ("Mitte - Berlin": the name, then the district or state it lies in), preferring one in the
    Area's city. None when nothing matches."""
    if area.kind == "zipcode":
        ids = [id_ for id_, name in found if name.startswith(f"{query} ")]
        return {"location_id": ids[0], "slug": query} if ids else None
    named = [(id_, name) for id_, name in found if slug(name.split(" - ")[0]) == slug(query)]
    parts = area.display.split(", ")
    city = slug(GERMAN_NAMES.get(parts[1], parts[1])) if len(parts) > 2 else ""
    in_city = [(id_, name) for id_, name in named if city and city in slug(name.split(" - ", 1)[-1])]
    best = in_city or named
    return {"location_id": best[0][0], "slug": slug(query)} if best else None


def search_url(criteria: Criteria, page: int = 1) -> str:
    """The search as Kleinanzeigen's own search form writes it: options as path segments, then the
    category and location ("c203l3331"), then attribute filters joined with "+". Rooms count the living
    room, so a minimum of N bedrooms is N + 1 rooms; half bedrooms round down so nothing is passed over,
    and a studio minimum is left out (every flat has a room)."""
    location = criteria.area.locations["kleinanzeigen"]
    path, category, prefix = CATEGORIES[criteria.home_type]
    segments = [NEWEST_FIRST, OFFERS_ONLY]
    if criteria.min_price is not None or criteria.max_price is not None:
        segments.append(f"preis:{criteria.min_price or ''}:{criteria.max_price or ''}")
    if page > 1:
        segments.append(f"seite:{page}")
    query = f"c{category}l{location['location_id']}"
    if criteria.min_beds:
        query += f"+{prefix}.zimmer_d:{math.floor(criteria.min_beds) + 1}%2C"
    if criteria.pets is not Pets.NONE:
        query += f"+options:{prefix}.pets_allowed_b"
    return f"{_SITE}/{path}/{location['slug']}/{'/'.join(segments)}/{query}"


def _price(texts: list[str]) -> int | None:
    """The asking rent: the first of the card's texts that is a price. "1.250 €" and "1.250 € VB" (open
    to offers) are 1250; a reduced price comes before the struck-through old one; "VB" alone is None."""
    for text in texts:
        found = _PRICE.fullmatch(text.strip())
        if found:
            return int(found["amount"].replace(".", "")) if found["amount"] else None
    return None


def _beds(texts: list[str]) -> float | None:
    """Bedrooms from the card's room count ("75 m² · 3 Zi." is 2 bedrooms, "3,5 Zi." is 2.5): rooms
    count the living room, so bedrooms are rooms minus one, never below 0."""
    for text in texts:
        for part in text.split("·"):
            found = _ROOMS.fullmatch(part.strip())
            if found:
                return max(float(found[1].replace(",", ".")) - 1, 0.0)
    return None


def _is_top_ad(card: Selector) -> bool:
    """A paid "TOP" ad: pinned above the results whatever its date, so it would alert as new."""
    return any(str(div.text).strip() == "TOP" for div in card.css("div"))


def _listing(card: Selector, scraped_at: datetime) -> Listing:
    texts = [str(p.text) for p in card.css("p")]
    beds = _beds(texts)
    price = _price(texts)
    images = card.css("[data-image-container] img")
    places = card.css("span")
    return Listing(
        source="kleinanzeigen", listing_id=str(card.attrib["data-adid"]),
        url=_SITE + str(card.attrib["data-href"]).split("#")[0],
        name=" ".join(str(card.css("h3")[0].get_all_text()).split()),  # a link, or a span for some ads
        address=str(places[0].text).strip() if places else "",
        price_min=price, price_max=price, beds_min=beds, beds_max=beds, baths_min=None, baths_max=None,
        image_url=str(images[0].attrib["src"]) if images else None, scraped_at=scraped_at,
    )


def parse_search_page(html: str, scraped_at: datetime) -> SourcePage:
    """The results list's Listings, newest first, leaving out TOP ads. The total comes from the heading
    ("1 - 25 von 1.732 Ergebnissen in Berlin"). With no matches the heading says so ("Es wurden keine
    Ergebnisse … gefunden") and the page shows ads from other places in a separate list instead; those
    aren't in the Area, so the page is empty."""
    page = Selector(html)
    heading = page.css("#srp-breadcrumb-summary")
    if not heading:  # look for a block page only here: an ad's own text could hold the words
        if any(marker in html for marker in _BLOCK_MARKERS):
            raise BlockedError(_BLOCKED)
        raise ScrapeError(_LAYOUT_CHANGED)
    counts = _COUNTS.search(str(heading[0].text))
    if counts is None:
        if "keine Ergebnisse" in str(heading[0].text):
            return SourcePage([], 0, False)
        raise ScrapeError(_LAYOUT_CHANGED)
    last, total = int(counts["last"].replace(".", "")), int(counts["total"].replace(".", ""))
    cards = page.css("#srchrslt-adtable article[data-adid]")
    if not cards:
        raise ScrapeError(_LAYOUT_CHANGED)
    try:
        listings = [_listing(card, scraped_at) for card in cards if not _is_top_ad(card)]
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        raise ScrapeError(_LAYOUT_CHANGED) from error
    return SourcePage(listings, total, last < total)


class KleinanzeigenSource:
    """Reads Kleinanzeigen. Places an Area through the site's place suggestions, using `lookup_http`
    (default: the Runs' client) so placing needn't wait behind a Run's polite gaps. Kleinanzeigen has no
    bathrooms filter."""

    key = "kleinanzeigen"
    filters = frozenset({Filter.PRICE, Filter.BEDS, Filter.HOME_TYPE, Filter.PETS})
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
        """The location named like the Area's own name (in German), or a postcode's own location."""
        name = area.display.split(", ")[0]
        query = name if area.kind == "zipcode" else GERMAN_NAMES.get(name, name)
        try:
            response = self._lookup_http.get(suggest_url(query))
        except ScrapeError as error:
            raise ScrapeError(LOOKUP_DOWN) from error
        raise_for_status(response, blocked=LOOKUP_DOWN, failed=LOOKUP_DOWN)
        location = matching_location(parse_suggestions(response.text), area, query)
        if location is None:
            raise CantPlace(f"Kleinanzeigen doesn't know {area.display}.")
        return location

    def results_url(self, criteria: Criteria) -> str:
        return search_url(criteria, 1)

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(search_url(criteria, page))
        failed = f"Kleinanzeigen answered with an error (HTTP {response.status})."
        raise_for_status(response, blocked=_BLOCKED, failed=failed)
        return parse_search_page(response.text, self._now())

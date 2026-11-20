"""The Bien'ici Source (France): places an Area on a Bien'ici zone through Bien'ici's place suggestions,
turns Criteria into a newest-first rental search, and reads Listings from its JSON results feed."""

import json
import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from apartment_hunter.criteria import Area, Criteria, HomeType, SourceLocation
from apartment_hunter.models import Listing
from apartment_hunter.sources import (
    NO_HOME_TYPE_NOTES,
    PLACE_NOTE,
    BlockedError,
    CantPlace,
    Filter,
    ScrapeError,
    SourcePage,
)
from apartment_hunter.sources.http import HttpClient, raise_for_status

FEED_URL = "https://www.bienici.com/realEstateAds.json"
SUGGEST_URL = "https://res.bienici.com/suggest.json"
SEARCH_PAGE_URL = "https://www.bienici.com/recherche/location"
PAGE_SIZE = 24
# Lookup places too big to stand in for a neighbourhood the lookup doesn't know.
BROAD_KINDS = {"department", "region", "country"}
LOOKUP_DOWN = "Bien'ici's place lookup isn't answering right now. Try again in a few minutes."
# Homes to live in (Bien'ici's feed also holds parking spaces, shops, offices and land).
HOMES = ["flat", "house", "loft", "townhouse"]
PROPERTY_TYPES = {
    HomeType.APARTMENT_CONDO: ["flat", "loft"],
    HomeType.HOUSE: ["house", "townhouse"],
    HomeType.TOWNHOME: ["townhouse"],
}
# How an ad names its home type, in English for the Listing and in French for its page's address.
HOME_NAMES = {"flat": ("flat", "appartement"), "house": ("house", "maison"), "loft": ("loft", "loft"),
              "townhouse": ("townhouse", "maison")}
_SITE = "https://www.bienici.com"
_BLOCK_MARKERS = ("captcha-delivery", "Access Denied", "Just a moment")
_BLOCKED = (
    "Bien'ici is blocking requests from this computer right now (bot protection). "
    "This usually clears up on its own."
)
_LAYOUT_CHANGED = "Bien'ici's results feed changed, so listings couldn't be read. The bot needs an update."


def slug(text: str) -> str:
    """A name as Bien'ici writes it in addresses: "Saint-Étienne" is "saint-etienne"."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-")


def feed_filters(criteria: Criteria, page: int = 1) -> dict[str, Any]:
    """The feed's filters. Bien'ici has a bedrooms filter of its own, so beds need no translating
    into pièces; a studio minimum (0 bedrooms) is left out, as it would only drop ads that don't
    say how many bedrooms they have."""
    location = criteria.area.locations["bienici"]
    filters: dict[str, Any] = {
        "size": PAGE_SIZE, "from": (page - 1) * PAGE_SIZE, "filterType": "rent",
        "propertyType": PROPERTY_TYPES.get(criteria.home_type, HOMES), "page": page,
        "sortBy": "publicationDate", "sortOrder": "desc", "onTheMarket": [True],
        "zoneIdsByTypes": {"zoneIds": str(location["zone_ids"]).split(",")},
    }
    if criteria.min_price is not None:
        filters["minPrice"] = criteria.min_price
    if criteria.max_price is not None:
        filters["maxPrice"] = criteria.max_price
    if criteria.min_beds:  # whole bedrooms only; round down so nothing is passed over
        filters["minBedrooms"] = math.floor(criteria.min_beds)
    return filters


def feed_url(criteria: Criteria, page: int = 1) -> str:
    filters = json.dumps(feed_filters(criteria, page), separators=(",", ":"))
    return f"{FEED_URL}?filters={quote(filters, safe='')}"


def _count(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _listing(ad: dict[str, Any], scraped_at: datetime) -> Listing:
    """One ad. Bedrooms are the ad's own count when it gives one (a "3 pièces" can have 1 bedroom and a
    separate kitchen), else pièces minus one, never below 0. Baths are salles de bains plus salles
    d'eau (shower rooms). The price is the monthly rent, charges included."""
    rooms, bedrooms = _count(ad.get("roomsQuantity")), _count(ad.get("bedroomsQuantity"))
    beds = bedrooms if bedrooms is not None else (max(rooms - 1, 0) if rooms is not None else None)
    bath_counts = [n for n in (_count(ad.get("bathroomsQuantity")), _count(ad.get("showerRoomsQuantity")))
                   if n is not None]
    baths = sum(bath_counts) if bath_counts else None
    price = round(ad["price"]) if isinstance(ad.get("price"), int | float) else None
    english, french = HOME_NAMES.get(ad.get("propertyType") or "", ("home", "bien"))
    city, postal_code = ad.get("city") or "", ad.get("postalCode")
    path = ["annonce", "location", slug(city), french, f"{rooms}pieces" if rooms else "", str(ad["id"])]
    photos = ad.get("photos") or []
    return Listing(
        source="bienici", listing_id=str(ad["id"]), url=_SITE + "/" + "/".join(p for p in path if p),
        name=f"{rooms}-room {english}" if rooms else english.capitalize(),
        address=f"{city} ({postal_code})" if postal_code else city,
        price_min=price, price_max=price, beds_min=beds, beds_max=beds, baths_min=baths, baths_max=baths,
        image_url=photos[0].get("url") if photos else None, scraped_at=scraped_at,
    )


def parse_feed(body: str, scraped_at: datetime) -> SourcePage:
    """The feed's ads, newest first. The feed pins no adverts above its results: every ad is in date
    order, so all of them are kept."""
    try:
        data = json.loads(body)
    except ValueError as error:  # not the feed: look for a block page only then
        if any(marker in body for marker in _BLOCK_MARKERS):
            raise BlockedError(_BLOCKED) from error
        raise ScrapeError(_LAYOUT_CHANGED) from error
    try:
        ads = data["realEstateAds"]
        listings = [_listing(ad, scraped_at) for ad in ads]
        total = int(data["total"])
        has_more = int(data.get("from") or 0) + len(ads) < total
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ScrapeError(_LAYOUT_CHANGED) from error
    return SourcePage(listings, total, has_more)


@dataclass(frozen=True)
class Zone:
    """One place from Bien'ici's lookup, e.g. arrondissement "Paris 18e" (zone -9531, 75018)."""

    name: str
    kind: str
    zone_ids: tuple[str, ...]
    postal_codes: tuple[str, ...]
    bbox: tuple[float, float, float, float]  # south, west, north, east

    def holds(self, lat: float, lon: float) -> bool:
        south, west, north, east = self.bbox
        return south <= lat <= north and west <= lon <= east

    @property
    def size(self) -> float:
        south, west, north, east = self.bbox
        return (north - south) * (east - west)

    @property
    def location(self) -> SourceLocation:
        """What a Search saves: the zone ids the feed takes, and the slug the site's search page
        takes ("paris-18e-75018")."""
        place = slug(self.name)
        return {"zone_ids": ",".join(self.zone_ids),
                "slug": f"{place}-{self.postal_codes[0]}" if self.postal_codes else place}


def suggest_url(text: str) -> str:
    return f"{SUGGEST_URL}?q={quote(text, safe='')}"


def parse_suggestions(body: str) -> list[Zone]:
    """The lookup's answer as zones; a garbled answer, or a place without zone ids or extent, has none."""
    try:
        found = json.loads(body)
    except ValueError:
        return []
    zones = []
    for raw in found if isinstance(found, list) else []:
        try:
            box = raw["boundingBox"]
            zones.append(Zone(
                str(raw["name"]), str(raw.get("type") or ""), tuple(str(z) for z in raw["zoneIds"]),
                tuple(str(code) for code in raw.get("postalCodes") or ()),
                (float(box["south"]), float(box["west"]), float(box["north"]), float(box["east"])),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return [zone for zone in zones if zone.zone_ids]


def matching_zone(found: list[Zone], area: Area, query: str) -> Zone | None:
    """For a postcode, the zone with that postal code; otherwise the zone named `query` (ignoring
    accents and case) around the Area's point, the smallest when several are (city before
    department). None when nothing matches."""
    if area.kind == "zipcode":
        return next((zone for zone in found if query in zone.postal_codes), None)
    named = [zone for zone in found if slug(zone.name) == slug(query) and zone.holds(area.lat, area.lon)]
    return min(named, key=lambda zone: zone.size) if named else None


def smallest_zone_around(found: list[Zone], area: Area) -> Zone | None:
    """The smallest city or district around the Area's point (Paris 18e for Montmartre), never a
    whole department or region."""
    around = [zone for zone in found if zone.kind not in BROAD_KINDS and zone.holds(area.lat, area.lon)]
    return min(around, key=lambda zone: zone.size) if around else None


class BieniciSource:
    """Reads Bien'ici. Places an Area on a Bien'ici zone through Bien'ici's place lookup, using
    `lookup_http` (default: the Runs' client) so placing needn't wait behind a Run's polite gaps.
    Bien'ici has no baths or pets filter."""

    key = "bienici"
    filters = frozenset({Filter.PRICE, Filter.BEDS, Filter.HOME_TYPE})
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
        """The zone named like the Area's own name (or holding its postcode). Bien'ici's lookup knows
        communes and arrondissements but not neighbourhoods, so when that fails the Area goes on the
        smallest zone of its city that holds it: Montmartre is searched as Paris 18e, and the location
        notes that."""
        parts = area.display.split(", ")
        zone = matching_zone(self._suggest(parts[0]), area, parts[0])
        if zone is not None:
            return zone.location
        zone = smallest_zone_around(self._suggest(parts[1]), area) if len(parts) > 2 else None
        if zone is None:
            raise CantPlace(f"Bien'ici doesn't know {area.display}.")
        return {**zone.location, PLACE_NOTE: f"Bien'ici doesn't know {parts[0]}, so it searches {zone.name}."}

    def _suggest(self, text: str) -> list[Zone]:
        try:
            response = self._lookup_http.get(suggest_url(text))
        except ScrapeError as error:
            raise ScrapeError(LOOKUP_DOWN) from error
        raise_for_status(response, blocked=LOOKUP_DOWN, failed=LOOKUP_DOWN)
        return parse_suggestions(response.text)

    def results_url(self, criteria: Criteria) -> str:
        return f"{SEARCH_PAGE_URL}/{criteria.area.locations[self.key]['slug']}"

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        response = self._http.get(feed_url(criteria, page))
        raise_for_status(
            response, blocked=_BLOCKED, failed=f"Bien'ici answered with an error (HTTP {response.status})."
        )
        return parse_feed(response.text, self._now())

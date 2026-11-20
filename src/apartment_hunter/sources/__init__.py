from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, SourceLocation
from apartment_hunter.models import Listing

# The key of a source location's note, when a Source searches a broader area than the one picked
# ("Bien'ici doesn't know Montmartre, so it searches Paris 18e."). Replies about the Search show it.
PLACE_NOTE = "note"
NO_HOME_TYPE_NOTES: Mapping[HomeType, str] = MappingProxyType({})

SOURCE_NAMES = {
    "zillow": "Zillow",
    "craigslist": "Craigslist",
    "rightmove": "Rightmove",
    "zoopla": "Zoopla",
    "bienici": "Bien'ici",
    "kleinanzeigen": "Kleinanzeigen",
    "fotocasa": "Fotocasa",
    "idealista_it": "Idealista Italy",
    "idealista_pt": "Idealista Portugal",
    "pararius": "Pararius",
}


class ScrapeError(Exception):
    """A Source couldn't read a page. The message is a plain-language reason."""


class BlockedError(ScrapeError):
    """The site refused to serve the page (rate limit, CAPTCHA, access denied)."""


class CantPlace(Exception):
    """A Source can't search an Area (its site doesn't know the place). The message is a plain-language
    reason, shown when the Source is left out of a Search."""


class Filter(StrEnum):
    """A part of the Criteria a Source may or may not be able to filter by. The value is how replies
    name it ("can't filter by home type")."""

    PRICE = "price"
    BEDS = "beds"
    BATHS = "baths"
    HOME_TYPE = "home type"
    PETS = "pets"


ALL_FILTERS = frozenset(Filter)


def filters_used(criteria: Criteria) -> list[Filter]:
    """The filters these Criteria set, in Filter order."""
    used = {
        Filter.PRICE: criteria.min_price is not None or criteria.max_price is not None,
        Filter.BEDS: criteria.min_beds is not None,
        Filter.BATHS: criteria.min_baths is not None,
        Filter.HOME_TYPE: criteria.home_type is not HomeType.ANY,
        Filter.PETS: criteria.pets is not Pets.NONE,
    }
    return [f for f in Filter if used[f]]


@dataclass(frozen=True)
class SourcePage:
    listings: list[Listing]
    total: int | None
    has_more: bool


class Source(Protocol):
    """A listing site. `key` names it in SOURCE_NAMES and in the countries table; `filters` are the
    Criteria its site can filter by (the rest are passed over and named in replies).
    `home_type_notes` say what it searches for a home type it can't match exactly ("Zoopla can't tell
    townhomes from houses, so you'll see all houses."); replies about the Search show them."""

    key: str
    filters: frozenset[Filter]
    home_type_notes: Mapping[HomeType, str]

    def place(self, area: Area) -> SourceLocation:
        """What this Source needs to search `area`, worked out once when a Search is created or its
        Area changes and saved with it. A Source searches the Area picked; when it searches a broader
        one, the location says so under PLACE_NOTE. Raises CantPlace when the site can't search
        there, or ScrapeError when its place lookup didn't answer."""
        ...

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        """One page of results, newest first. `criteria.area.locations[self.key]` is what `place`
        returned."""
        ...

    def results_url(self, criteria: Criteria) -> str: ...

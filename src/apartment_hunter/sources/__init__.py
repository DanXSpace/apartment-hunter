from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, SourceLocation
from apartment_hunter.models import Listing

SOURCE_NAMES = {"zillow": "Zillow", "craigslist": "Craigslist", "rightmove": "Rightmove",
                "idealista_it": "Idealista Italy", "idealista_pt": "Idealista Portugal"}


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
    Criteria its site can filter by (the rest are passed over and named in replies)."""

    key: str
    filters: frozenset[Filter]

    def place(self, area: Area) -> SourceLocation:
        """What this Source needs to search `area`, worked out once when a Search is created or its
        Area changes and saved with it. Raises CantPlace when the site can't search there, or
        ScrapeError when its place lookup didn't answer."""
        ...

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        """One page of results, newest first. `criteria.area.locations[self.key]` is what `place`
        returned."""
        ...

    def results_url(self, criteria: Criteria) -> str: ...

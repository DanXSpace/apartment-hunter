from dataclasses import dataclass
from typing import Protocol

from apartment_hunter.criteria import Criteria
from apartment_hunter.models import Listing

SOURCE_NAMES = {"zillow": "Zillow", "craigslist": "Craigslist"}


class ScrapeError(Exception):
    """A Source couldn't read a page. The message is a plain-language reason."""


class BlockedError(ScrapeError):
    """The site refused to serve the page (rate limit, CAPTCHA, access denied)."""


@dataclass(frozen=True)
class SourcePage:
    listings: list[Listing]
    total: int | None
    has_more: bool


class Source(Protocol):
    key: str

    def fetch(self, criteria: Criteria, page: int) -> SourcePage: ...

    def results_url(self, criteria: Criteria) -> str: ...

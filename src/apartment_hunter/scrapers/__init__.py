from typing import Protocol

from apartment_hunter.models import ScrapeResult


class ScrapeError(Exception):
    """A scrape failed; the run counts toward the scraper-health alert."""


class BlockedError(ScrapeError):
    """The site served a bot-protection / access-denied page."""


class Scraper(Protocol):
    def scrape(self, url: str) -> ScrapeResult: ...

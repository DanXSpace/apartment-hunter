from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Listing:
    """One search-result card. Ranges cover multi-unit buildings; min == max for a single unit.

    Any numeric field may be None when the site doesn't show it (e.g. "Call for Rent").
    Studios have beds_min == 0.
    """

    listing_id: str
    url: str
    name: str
    address: str
    price_min: int | None
    price_max: int | None
    beds_min: float | None
    beds_max: float | None
    baths_min: float | None
    baths_max: float | None
    image_url: str | None
    scraped_at: datetime


@dataclass(frozen=True)
class ScrapeResult:
    listings: list[Listing]
    pages_fetched: int
    hit_page_cap: bool

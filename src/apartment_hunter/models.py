from dataclasses import dataclass
from datetime import datetime

from apartment_hunter.criteria import Criteria


@dataclass(frozen=True)
class Listing:
    """One search-result card. Ranges cover multi-unit buildings; min == max for a single unit.

    `source` is the key of the Source it came from ("zillow" or "craigslist"); a Search keys its
    Listings by (source, listing_id).

    Any numeric field may be None when the site doesn't show it (e.g. Zillow's "Contact for price").
    Studios have beds_min == 0.
    """

    source: str
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
class Search:
    """What to look for (its Criteria), on which Sources, and the channel its Alerts go to.

    A Search belongs to one Discord server and is identified by its id, so renaming it doesn't start
    it over. `created_by` is a Discord user id and `sources` holds Source keys. `attention` is the
    reason the Search needs attention, or None when nothing is wrong.
    """

    id: str
    server_id: int
    channel_id: int
    created_by: int
    name: str
    criteria: Criteria
    sources: tuple[str, ...]
    created_at: datetime
    paused: bool = False
    attention: str | None = None

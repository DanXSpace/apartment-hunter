from dataclasses import dataclass
from datetime import datetime

@dataclass
class Listing:
    listing_id: str
    url: str
    address: str
    price: int
    beds: int
    baths: float
    pets_allowed: bool
    commute_miles: float
    scraped_at: datetime
    image_url: str

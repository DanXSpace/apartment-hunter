from datetime import UTC, datetime
from pathlib import Path

import pytest

from apartment_hunter.criteria import Area, Criteria
from apartment_hunter.models import Listing, Search

FIXTURES = Path(__file__).parent / "fixtures"

AT = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
CHICAGO = Area("Chicago, IL", "city", 17426, 6, 41.8781, -87.6298, "chicago", 10)
LINCOLN_PARK = Area("Lincoln Park, Chicago, IL", "neighborhood", 269590, 8, 41.9237, -87.6505, "chicago", 2)


@pytest.fixture
def fixture_html():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")
    return load


def make_listing(listing_id="abc", price=2000, source="zillow", **overrides) -> Listing:
    fields = dict(
        source=source, listing_id=listing_id, url=f"https://www.zillow.com/homedetails/{listing_id}/",
        name="Name", address="1 Main St", price_min=price, price_max=price, beds_min=2, beds_max=2,
        baths_min=1, baths_max=1, image_url=None, scraped_at=AT,
    )
    fields.update(overrides)
    return Listing(**fields)


def make_search(search_id="s1", server_id=1, name="Chicago", sources=("zillow",), **overrides) -> Search:
    fields = dict(
        id=search_id, server_id=server_id, channel_id=10, created_by=99, name=name,
        criteria=Criteria(CHICAGO), sources=sources, created_at=AT,
    )
    fields.update(overrides)
    return Search(**fields)

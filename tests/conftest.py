from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest

from apartment_hunter.areas import AreaNotFound
from apartment_hunter.criteria import Area, Criteria
from apartment_hunter.models import Listing, Search
from apartment_hunter.runner import SearchRunner
from apartment_hunter.scheduler import Scheduler
from apartment_hunter.service import SearchService
from apartment_hunter.sources import ScrapeError
from apartment_hunter.storage import Store
from fakes import FakeClock, FakeNotifier, FakeSource

FIXTURES = Path(__file__).parent / "fixtures"

AT = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
CHICAGO = Area("Chicago, IL", "city", "US", 41.8781, -87.6298, locations={
    "zillow": {"region_id": 17426, "region_type": 6},
    "craigslist": {"site": "chicago", "radius_miles": 10},
})
LINCOLN_PARK = Area("Lincoln Park, Chicago, IL", "neighborhood", "US", 41.9237, -87.6505, locations={
    "zillow": {"region_id": 269590, "region_type": 8},
    "craigslist": {"site": "chicago", "radius_miles": 2},
})


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


# The Search service with fake Areas, Sources and Notifiers, for tests at the service seam.

UNPLACED_CHICAGO = replace(CHICAGO, locations={})
CAMDEN = Area("Camden, London, United Kingdom", "neighborhood", "GB", 51.539, -0.1426)


class FakeAreas:
    """Knows "Chicago, IL" and, for tests that add the UK, "Camden, London, United Kingdom" (neither
    placed on any Source yet); anything else isn't a place. `down` makes every lookup fail. `cache` holds
    what earlier suggestions found, by typed text."""

    def __init__(self):
        self.down: ScrapeError | None = None
        self.cache: dict[str, Area] = {}

    def suggest(self, text: str) -> list[str]:
        return ["Chicago, IL"] if "chi" in text.casefold() else []

    def cached(self, text: str) -> Area | None:
        return self.cache.get(text)

    def resolve(self, text: str) -> Area:
        if self.down:
            raise self.down
        if text == "Chicago, IL":
            return UNPLACED_CHICAGO
        if text == CAMDEN.display:
            return CAMDEN
        raise AreaNotFound(f'I couldn\'t find a place called "{text}".')


@pytest.fixture
def env():
    store = Store(":memory:")
    zillow = FakeSource("zillow")
    notifiers: defaultdict[int, FakeNotifier] = defaultdict(FakeNotifier)
    runner = SearchRunner({"zillow": zillow}, store, lambda search: notifiers[search.channel_id])
    clock = FakeClock()
    scheduler = Scheduler(lambda sid: service.run(sid), clock=clock, next_delay=lambda m: m * 60)
    scheduler.start()
    ids = count(1)
    areas = FakeAreas()
    dms: list[tuple[int, str]] = []
    service = SearchService(
        store=store, runner=runner, scheduler=scheduler, areas=areas, sources={"zillow": zillow},
        clock=lambda: AT + timedelta(seconds=clock()), new_id=lambda: f"s{next(ids)}",
        direct_message=lambda user_id, text: dms.append((user_id, text)),
    )
    return SimpleNamespace(store=store, zillow=zillow, notifiers=notifiers, clock=clock,
                           scheduler=scheduler, service=service, dms=dms, areas=areas)


def add_source(env, key: str) -> FakeSource:
    """Give the bot another (fake) Source."""
    source = FakeSource(key)
    sources = {**env.service.sources, key: source}
    env.service.sources = sources
    env.service.runner = SearchRunner(sources, env.store, lambda search: env.notifiers[search.channel_id])
    return source

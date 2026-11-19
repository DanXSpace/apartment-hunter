import json

import pytest

from apartment_hunter.areas import (
    AreaLookup,
    AreaNotFound,
    CraigslistSite,
    load_craigslist_sites,
    nearest_craigslist_site,
    parse_suggestions,
    suggestions_url,
)
from apartment_hunter.criteria import Area
from apartment_hunter.sources import BlockedError, ScrapeError
from apartment_hunter.sources.http import HttpResponse
from fakes import FakeHttp

SUGGEST_BODY = json.dumps(
    {
        "results": [
            {
                "display": "Lincoln Park, Chicago, IL",
                "resultType": "Region",
                "metaData": {
                    "regionId": 269590,
                    "regionType": "neighborhood",
                    "lat": 41.9237,
                    "lng": -87.6505,
                },
            },
            {
                "display": "1906 N Lincoln Park W Chicago, IL 60614",
                "resultType": "Address",
                "metaData": {"addressType": "rental_address"},
            },
            {
                "display": "Illinois",
                "resultType": "Region",
                "metaData": {"regionId": 21, "regionType": "state", "lat": 40.0, "lng": -89.0},
            },
            {
                "display": "Chicago, IL",
                "resultType": "Region",
                "metaData": {
                    "regionId": 17426,
                    "regionType": "city",
                    "lat": 41.8781,
                    "lng": -87.6298,
                },
            },
        ]
    }
)
SITES = [
    CraigslistSite("chicago", 41.849998, -87.650002),
    CraigslistSite("newyork", 40.7166, -74.0),
    CraigslistSite("milwaukee", 43.0389, -87.9065),
]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_only_cities_neighborhoods_and_zip_codes_are_offered():
    assert [p.display for p in parse_suggestions(SUGGEST_BODY)] == [
        "Lincoln Park, Chicago, IL",
        "Chicago, IL",
    ]


def test_suggestions_url_encodes_the_text():
    assert (
        suggestions_url("Coeur d'Alene, ID")
        == "https://www.zillowstatic.com/autocomplete/v3/suggestions?q=Coeur%20d%27Alene%2C%20ID"
    )


def test_nearest_craigslist_site():
    assert nearest_craigslist_site(41.9237, -87.6505, SITES) == "chicago"
    assert nearest_craigslist_site(43.05, -87.95, SITES) == "milwaukee"


def test_bundled_sites_include_chicago():
    assert nearest_craigslist_site(41.8781, -87.6298, load_craigslist_sites()) == "chicago"


def test_resolve_picks_the_matching_place_and_builds_the_area():
    lookup = AreaLookup(FakeHttp(SUGGEST_BODY), SITES, clock=FakeClock())
    assert lookup.resolve("chicago, il") == Area(
        "Chicago, IL", "city", 17426, 6, 41.8781, -87.6298, "chicago", 10
    )
    lp = lookup.resolve("Lincoln Park, Chicago, IL")
    assert (lp.zillow_region_type, lp.craigslist_radius_miles) == (8, 2)


def test_resolve_with_no_usable_place_raises():
    lookup = AreaLookup(FakeHttp(json.dumps({"results": []})), SITES, clock=FakeClock())
    with pytest.raises(AreaNotFound, match="couldn't find a place called"):
        lookup.resolve("123 Nowhere Rd")


def test_suggest_needs_three_characters_caches_and_throttles():
    http, clock = FakeHttp(SUGGEST_BODY), FakeClock()
    lookup = AreaLookup(http, SITES, clock=clock, min_gap_seconds=2)
    assert lookup.suggest("ch") == []
    assert lookup.suggest("Chi") == ["Lincoln Park, Chicago, IL", "Chicago, IL"]
    assert lookup.suggest("Chic") == []
    assert lookup.suggest("Chi") == ["Lincoln Park, Chicago, IL", "Chicago, IL"]
    clock.advance(2)
    lookup.suggest("Chic")
    assert len(http.requested) == 2


class InterruptingHttp(FakeHttp):
    """While the first request is in flight, another autocomplete arrives and asks for suggestions."""

    def __init__(self, response: str) -> None:
        super().__init__(response)
        self.lookup: AreaLookup | None = None
        self.second: list[str] | None = None

    def get(self, url: str) -> HttpResponse:
        if self.lookup is not None and self.second is None:
            self.second = self.lookup.suggest("Chica")
        return super().get(url)


def test_suggest_inside_the_gap_while_a_lookup_is_in_flight_makes_no_second_request():
    http = InterruptingHttp(SUGGEST_BODY)
    lookup = AreaLookup(http, SITES, clock=FakeClock(), min_gap_seconds=2)
    http.lookup = lookup
    assert lookup.suggest("Chi") == ["Lincoln Park, Chicago, IL", "Chicago, IL"]
    assert http.second == []
    assert len(http.requested) == 1


class RaisingHttp:
    def get(self, url: str) -> HttpResponse:
        raise ScrapeError("Couldn't reach www.zillowstatic.com: timed out")


def test_resolve_raises_when_the_lookup_fails_instead_of_saying_not_found():
    lookup = AreaLookup(FakeHttp(HttpResponse(500, "oops")), SITES, clock=FakeClock())
    with pytest.raises(ScrapeError, match="place lookup isn't answering") as failed:
        lookup.resolve("Chicago, IL")
    assert not isinstance(failed.value, AreaNotFound | BlockedError)


def test_resolve_raises_blocked_on_403_and_429():
    for status in (403, 429):
        lookup = AreaLookup(FakeHttp(HttpResponse(status, "")), SITES, clock=FakeClock())
        with pytest.raises(BlockedError):
            lookup.resolve("Chicago, IL")


def test_resolve_raises_when_the_http_layer_raises():
    lookup = AreaLookup(RaisingHttp(), SITES, clock=FakeClock())
    with pytest.raises(ScrapeError):
        lookup.resolve("Chicago, IL")


def test_suggest_returns_nothing_and_caches_nothing_when_the_lookup_fails():
    http, clock = FakeHttp(HttpResponse(500, "oops")), FakeClock()
    lookup = AreaLookup(http, SITES, clock=clock)
    assert lookup.suggest("Chi") == []
    clock.advance(2)
    http.response = HttpResponse(200, SUGGEST_BODY)
    assert lookup.suggest("Chi") == ["Lincoln Park, Chicago, IL", "Chicago, IL"]
    assert lookup.suggest("Chi") == ["Lincoln Park, Chicago, IL", "Chicago, IL"]
    assert len(http.requested) == 2

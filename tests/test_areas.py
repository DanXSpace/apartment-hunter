import json
from urllib.parse import parse_qsl, urlsplit

import pytest

from apartment_hunter.areas import PHOTON_USER_AGENT, AreaLookup, AreaNotFound
from apartment_hunter.countries import COUNTRIES
from apartment_hunter.criteria import Area
from apartment_hunter.sources import BlockedError, ScrapeError
from apartment_hunter.sources.http import HttpResponse
from fakes import FakeClock, FakeHttp


def feature(name: str, osm_value: str, countrycode: str, lon: float, lat: float, osm_key: str = "place",
            extent: list[float] | None = None, **props: str) -> dict:
    properties: dict = {"osm_key": osm_key, "osm_value": osm_value, "name": name,
                        "countrycode": countrycode, **props}
    if extent is not None:
        properties["extent"] = extent
    return {"type": "Feature", "properties": properties,
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def answer(*features: dict) -> str:
    return json.dumps({"type": "FeatureCollection", "features": list(features)})


CHICAGO = feature("Chicago", "city", "US", -87.6244212, 41.8755616, county="Cook County", state="Illinois",
                  extent=[-87.940101, 42.023135, -87.523661, 41.644286])
LINCOLN_PARK = feature("Lincoln Park", "suburb", "US", -87.6475, 41.9214, city="North Chicago Township",
                       state="Illinois", postcode="60614")
CHICAGO_ANSWER = answer(
    CHICAGO,
    feature("Chicago", "station", "US", -87.6, 41.9, osm_key="railway", city="Chicago", state="Illinois"),
    feature("Chicago Heights", "town", "US", -87.63, 41.50, state="Illinois"),
)


def test_suggestions_from_a_real_photon_answer_show_the_country_and_skip_counties(fixture_html):
    lookup = AreaLookup(FakeHttp(fixture_html("photon/camden.json")), clock=FakeClock())
    assert lookup.suggest("Camden") == [
        "Camden, NJ, United States",
        "Camden, SC, United States",
        "Camden, AR, United States",
        "Camden, AL, United States",
        "Camden, DE, United States",
        "Camden, NY, United States",
        "Camden Town, London, United Kingdom",
        "Camden, TN, United States",
        "Camden, NC, United States",
        "Camden, MO, United States",
        "Camden, OH, United States",
        "Camden, IN, United States",
        "Camden, MI, United States",
        "Camden, Minneapolis, MN, United States",
        "Camden, ME, United States",
    ]


def test_a_town_inside_a_city_is_a_neighborhood(fixture_html):
    lookup = AreaLookup(FakeHttp(fixture_html("photon/camden.json")), clock=FakeClock())
    assert lookup.resolve("Camden Town, London, United Kingdom") == Area(
        "Camden Town, London, United Kingdom", "neighborhood", "GB", 51.5392533, -0.142757)
    assert lookup.resolve("Camden, SC, United States").kind == "city"


@pytest.fixture
def us_and_uk_only(monkeypatch):
    """Narrows the countries table to the US and the UK, so these tests don't change with each new row."""
    for code in [code for code in COUNTRIES if code not in ("US", "GB")]:
        monkeypatch.delitem(COUNTRIES, code)


def test_the_lookup_asks_photon_in_english_for_the_supported_countries_only(us_and_uk_only):
    http = FakeHttp(CHICAGO_ANSWER)
    AreaLookup(http, clock=FakeClock()).suggest("Coeur d'Alene")
    url = urlsplit(http.requested[0])
    assert (url.scheme, url.netloc, url.path) == ("https", "photon.komoot.io", "/api/")
    assert parse_qsl(url.query) == [("q", "Coeur d'Alene"), ("limit", "25"), ("lang", "en"),
                                    ("countrycode", "US"), ("countrycode", "GB")]


def test_the_user_agent_names_the_bot_and_nothing_personal():
    assert PHOTON_USER_AGENT == "apartment-hunter-discord-bot"


def test_resolve_picks_the_matching_place_with_its_country_coordinates_and_bounding_box():
    lookup = AreaLookup(FakeHttp(CHICAGO_ANSWER), clock=FakeClock())
    assert lookup.resolve("chicago, il, united states") == Area(
        "Chicago, IL, United States", "city", "US", 41.8755616, -87.6244212,
        bbox=(41.644286, -87.940101, 42.023135, -87.523661))


def test_resolve_takes_the_top_place_when_nothing_matches_exactly():
    lookup = AreaLookup(FakeHttp(CHICAGO_ANSWER), clock=FakeClock())
    assert lookup.resolve("Chicago").display == "Chicago, IL, United States"


def test_a_us_neighborhood_leaves_out_the_township_osm_files_it_under():
    lookup = AreaLookup(FakeHttp(answer(LINCOLN_PARK)), clock=FakeClock())
    area = lookup.resolve("Lincoln Park")
    assert (area.display, area.kind, area.bbox) == ("Lincoln Park, IL, United States", "neighborhood", None)


def test_postcodes_are_accepted():
    sw11 = feature("SW11 6HR", "postcode", "GB", -0.16, 51.47, city="London",
                   district="London Borough of Wandsworth", state="England")
    lookup = AreaLookup(FakeHttp(answer(sw11)), clock=FakeClock())
    area = lookup.resolve("SW11")
    assert (area.display, area.kind, area.country) == ("SW11 6HR, London, United Kingdom", "zipcode", "GB")


def test_a_uk_city_without_a_city_field_names_its_nation():
    london = feature("London", "city", "GB", -0.1277, 51.5074, state="England")
    assert AreaLookup(FakeHttp(answer(london)), clock=FakeClock()).suggest("London") == [
        "London, England, United Kingdom"]


def test_places_in_countries_the_bot_does_not_cover_are_left_out(us_and_uk_only):
    paris = feature("Paris", "city", "FR", 2.35, 48.86, state="Île-de-France")
    paris_tx = feature("Paris", "town", "US", -95.55, 33.66, state="Texas")
    lookup = AreaLookup(FakeHttp(answer(paris, paris_tx)), clock=FakeClock())
    assert lookup.suggest("Paris") == ["Paris, TX, United States"]


def test_an_unknown_place_is_refused_naming_the_covered_countries(us_and_uk_only):
    lookup = AreaLookup(FakeHttp(answer()), clock=FakeClock())
    with pytest.raises(AreaNotFound) as refused:
        lookup.resolve("Wien")
    assert str(refused.value) == (
        'I couldn\'t find a place called "Wien" in the countries I cover (United States and United '
        'Kingdom). Try a city, neighborhood or postcode, like "Chicago, IL", "Camden, London" or "60614".')


ADDRESS_FIRST = answer(
    feature("1906 North Lincoln Park West", "apartments", "US", -87.64, 41.92, osm_key="building",
            city="Chicago", state="Illinois"),
    LINCOLN_PARK,
)


def test_resolve_refuses_an_address_instead_of_picking_a_nearby_place():
    lookup = AreaLookup(FakeHttp(ADDRESS_FIRST), clock=FakeClock())
    with pytest.raises(AreaNotFound, match='couldn\'t find a place called "1906 N Lincoln Park W'):
        lookup.resolve("1906 N Lincoln Park W Chicago")


def test_resolve_accepts_a_place_typed_exactly_even_when_an_address_comes_first():
    lookup = AreaLookup(FakeHttp(ADDRESS_FIRST), clock=FakeClock())
    assert lookup.resolve("lincoln park, il, united states").kind == "neighborhood"


def test_suggest_needs_three_characters_caches_and_throttles():
    http, clock = FakeHttp(CHICAGO_ANSWER), FakeClock()
    lookup = AreaLookup(http, clock=clock, min_gap_seconds=2)
    found = ["Chicago, IL, United States", "Chicago Heights, IL, United States"]
    assert lookup.suggest("ch") == []
    assert lookup.suggest("Chi") == found
    assert lookup.suggest("Chic") == []
    assert lookup.suggest("Chi") == found
    clock.advance(2)
    lookup.suggest("Chic")
    assert len(http.requested) == 2


def test_cached_gives_what_resolve_would_from_earlier_suggestions_without_a_request(fixture_html):
    http = FakeHttp(fixture_html("photon/camden.json"))
    lookup = AreaLookup(http, clock=FakeClock())
    assert lookup.cached("Camden") is None
    lookup.suggest("Camden")
    # Nothing is named exactly "Camden", so it's Photon's top place, as resolve would pick.
    assert lookup.cached(" camden ").display == "Camden, NJ, United States"
    assert lookup.cached("Camden Town, London, United Kingdom") is None  # not typed before
    assert lookup.cached("Kreuzberg") is None
    assert len(http.requested) == 1


def test_resolve_is_never_throttled():
    http = FakeHttp(CHICAGO_ANSWER)
    lookup = AreaLookup(http, clock=FakeClock(), min_gap_seconds=2)
    lookup.suggest("Chi")
    assert lookup.resolve("Chicago").country == "US"
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
    http = InterruptingHttp(CHICAGO_ANSWER)
    lookup = AreaLookup(http, clock=FakeClock(), min_gap_seconds=2)
    http.lookup = lookup
    assert len(lookup.suggest("Chi")) == 2
    assert http.second == []
    assert len(http.requested) == 1


class RaisingHttp:
    def get(self, url: str) -> HttpResponse:
        raise ScrapeError("Couldn't reach photon.komoot.io: timed out")


def test_resolve_raises_when_the_lookup_fails_instead_of_saying_not_found():
    lookup = AreaLookup(FakeHttp(HttpResponse(500, "oops")), clock=FakeClock())
    with pytest.raises(ScrapeError, match="place lookup isn't answering") as failed:
        lookup.resolve("Chicago")
    assert not isinstance(failed.value, AreaNotFound | BlockedError)


def test_resolve_raises_blocked_on_403_and_429():
    for status in (403, 429):
        lookup = AreaLookup(FakeHttp(HttpResponse(status, "")), clock=FakeClock())
        with pytest.raises(BlockedError):
            lookup.resolve("Chicago")


def test_resolve_raises_when_the_http_layer_raises():
    with pytest.raises(ScrapeError):
        AreaLookup(RaisingHttp(), clock=FakeClock()).resolve("Chicago")


def test_suggest_returns_nothing_and_caches_nothing_when_the_lookup_fails():
    http, clock = FakeHttp(HttpResponse(500, "oops")), FakeClock()
    lookup = AreaLookup(http, clock=clock)
    assert lookup.suggest("Chi") == []
    clock.advance(2)
    http.response = HttpResponse(200, CHICAGO_ANSWER)
    assert len(lookup.suggest("Chi")) == 2
    assert len(lookup.suggest("Chi")) == 2
    assert len(http.requested) == 2

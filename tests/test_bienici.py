import json
from dataclasses import replace
from urllib.parse import parse_qsl, urlsplit

import pytest

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import BlockedError, CantPlace, Filter, ScrapeError
from apartment_hunter.sources.bienici import BieniciSource
from apartment_hunter.sources.http import HttpResponse
from conftest import AT
from fakes import FakeHttp

PARIS_18 = Area("75018, Paris, France", "zipcode", "FR", 48.8925, 2.3484, locations={
    "bienici": {"zone_ids": "-9531", "slug": "paris-18e-75018"},
})


def source(response: HttpResponse | str = "") -> tuple[BieniciSource, FakeHttp]:
    http = FakeHttp(response)
    return BieniciSource(http, now=lambda: AT), http


def filters(url: str) -> dict:
    """The feed's `filters` parameter, decoded."""
    (name, value), = parse_qsl(urlsplit(url).query)
    assert name == "filters"
    return json.loads(value)


def feed(ads: list, total: int, start: int = 0) -> str:
    return json.dumps({"total": total, "from": start, "perPage": 24, "realEstateAds": ads})


def test_the_feed_asks_for_the_newest_rentals_in_the_saved_zone():
    bienici, http = source(feed([], 0))
    bienici.fetch(Criteria(PARIS_18), 1)
    assert http.requested[0].startswith("https://www.bienici.com/realEstateAds.json?filters=")
    assert filters(http.requested[0]) == {
        "size": 24, "from": 0, "filterType": "rent", "propertyType": ["flat", "house", "loft", "townhouse"],
        "page": 1, "sortBy": "publicationDate", "sortOrder": "desc", "onTheMarket": [True],
        "zoneIdsByTypes": {"zoneIds": ["-9531"]},
    }


def test_filters_go_into_the_feed_and_later_pages_start_24_ads_further_on():
    bienici, http = source(feed([], 0))
    bienici.fetch(Criteria(PARIS_18, min_price=900, max_price=1500, min_beds=2, home_type=HomeType.HOUSE), 3)
    asked = filters(http.requested[0])
    assert (asked["from"], asked["page"]) == (48, 3)
    assert (asked["propertyType"], asked["minPrice"], asked["maxPrice"], asked["minBedrooms"]) == (
        ["house", "townhouse"], 900, 1500, 2)


@pytest.mark.parametrize("home_type,types", [
    (HomeType.APARTMENT_CONDO, ["flat", "loft"]),
    (HomeType.TOWNHOME, ["townhouse"]),
])
def test_home_types(home_type, types):
    bienici, http = source(feed([], 0))
    bienici.fetch(Criteria(PARIS_18, home_type=home_type), 1)
    assert filters(http.requested[0])["propertyType"] == types


def test_a_studio_minimum_asks_for_no_bedroom_minimum():
    bienici, http = source(feed([], 0))
    bienici.fetch(Criteria(PARIS_18, min_beds=0), 1)
    assert "minBedrooms" not in filters(http.requested[0])


def test_bienici_cannot_filter_by_baths_or_pets():
    assert BieniciSource.filters == frozenset({Filter.PRICE, Filter.BEDS, Filter.HOME_TYPE})
    bienici, http = source(feed([], 0))
    bienici.fetch(Criteria(PARIS_18, min_baths=2, pets=Pets.CATS), 1)
    assert len(filters(http.requested[0])) == 9


def test_parses_a_saved_real_feed(fixture_html):
    bienici, _ = source(fixture_html("bienici/paris_flats_rent.json"))
    page = bienici.fetch(Criteria(PARIS_18), 1)
    assert (len(page.listings), page.total, page.has_more) == (24, 1365, True)
    assert page.listings[0] == Listing(
        source="bienici", listing_id="ag757532-561534863",
        url="https://www.bienici.com/annonce/location/paris-10e/appartement/3pieces/ag757532-561534863",
        name="3-room flat", address="Paris 10e (75010)", price_min=1550, price_max=1550,
        beds_min=1, beds_max=1, baths_min=1, baths_max=1,
        image_url="https://file.bienici.com/photo/ag757532-561534863_photos.ubiflow.net_757532_561534863_"
                  "photos_1.jpg_INSOON_EB_5512801818_20261120180652",
        scraped_at=AT)


def test_bedrooms_come_from_the_ad_or_else_from_pieces_minus_one(fixture_html):
    bienici, _ = source(fixture_html("bienici/paris_flats_rent.json"))
    ads = {listing.listing_id: listing for listing in bienici.fetch(Criteria(PARIS_18), 1).listings}
    # "3 pièces" with a living room, a bedroom and a separate kitchen: the ad says 1 bedroom.
    assert ads["ag757532-561534863"].beds_min == 1
    # A 1-pièce ad that gives no bedroom count is a studio.
    assert ads["mgc-ancien-215S258-3504-55538"].beds_min == 0
    # Shower rooms (salles d'eau) count as baths: 1 salle de bains + 1 salle d'eau.
    assert ads["immo-facile-61765987"].baths_min == 2
    assert ads["apimo-87618405"].baths_min is None
    # Rents with cents are read as whole euros (1367.11 charges included).
    assert ads["ag922145-561513656"].price_min == 1367


AD = {"id": "x-1", "propertyType": "flat", "city": "Lyon 3e", "postalCode": "69003", "roomsQuantity": 4,
      "price": 900, "photos": []}


def test_an_ad_without_rooms_photos_or_price_and_other_home_types():
    bienici, _ = source(feed([{**AD, "roomsQuantity": None, "price": None},
                              {**AD, "id": "x-2", "propertyType": "house", "city": "Saint-Étienne"}], 2))
    bare, house = bienici.fetch(Criteria(PARIS_18), 1).listings
    assert (bare.name, bare.beds_min, bare.price_min, bare.image_url) == ("Flat", None, None, None)
    assert bare.url == "https://www.bienici.com/annonce/location/lyon-3e/appartement/x-1"
    assert (house.name, house.beds_min) == ("4-room house", 3)
    assert house.url == "https://www.bienici.com/annonce/location/saint-etienne/maison/4pieces/x-2"


def test_the_last_page_has_no_more():
    page = source(feed([AD], 25, start=24))[0].fetch(Criteria(PARIS_18), 2)
    assert (len(page.listings), page.total, page.has_more) == (1, 25, False)


def test_zero_matches_is_an_empty_page_not_an_error():
    page = source(feed([], 0))[0].fetch(Criteria(PARIS_18), 1)
    assert (page.listings, page.total, page.has_more) == ([], 0, False)


@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError, match="Bien'ici is blocking"):
        source(HttpResponse(status, ""))[0].fetch(Criteria(PARIS_18), 1)


def test_other_http_errors_are_plain_scrape_errors():
    with pytest.raises(ScrapeError, match=r"Bien'ici answered with an error \(HTTP 500\)\.") as e:
        source(HttpResponse(500, ""))[0].fetch(Criteria(PARIS_18), 1)
    assert not isinstance(e.value, BlockedError)


def test_a_captcha_page_instead_of_the_feed_is_blocked():
    with pytest.raises(BlockedError):
        source('<html><script src="https://ct.captcha-delivery.com/c.js"></script></html>')[0].fetch(
            Criteria(PARIS_18), 1)


@pytest.mark.parametrize("body", ["<html><body>Page not found</body></html>", '{"error": "bad filters"}',
                                  '{"total": 3, "realEstateAds": [{"price": 1}]}'])
def test_an_answer_without_ads_is_a_layout_change(body):
    with pytest.raises(ScrapeError, match="Bien'ici's results feed changed"):
        source(body)[0].fetch(Criteria(PARIS_18), 1)


def test_results_url_is_the_zones_rental_search_on_the_site():
    assert source()[0].results_url(Criteria(PARIS_18)) == \
        "https://www.bienici.com/recherche/location/paris-18e-75018"


class RoutedHttp:
    """Answers each place lookup by its query (q=…); any other query gets an empty answer."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.requested: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.requested.append(url)
        return HttpResponse(200, self.answers.get(dict(parse_qsl(urlsplit(url).query))["q"], "[]"))


def placer(answers: dict[str, str]) -> tuple[BieniciSource, RoutedHttp, FakeHttp]:
    runs, lookups = FakeHttp(), RoutedHttp(answers)
    return BieniciSource(runs, now=lambda: AT, lookup_http=lookups), lookups, runs


PARIS = Area("Paris, Ile-de-France, France", "city", "FR", 48.8589, 2.3470)
MONTMARTRE = Area("Montmartre, Paris, France", "neighborhood", "FR", 48.8867, 2.3431)


def test_place_finds_a_city_by_its_name_through_the_lookup_client_only(fixture_html):
    bienici, lookups, runs = placer({"Paris": fixture_html("bienici/suggest_paris.json")})
    assert bienici.place(PARIS) == {"zone_ids": "-7444", "slug": "paris-75000"}
    assert lookups.requested == ["https://res.bienici.com/suggest.json?q=Paris"]
    assert runs.requested == []


def test_place_finds_a_postcode_by_its_code(fixture_html):
    bienici, lookups, _ = placer({"75018": fixture_html("bienici/suggest_75018.json")})
    assert bienici.place(replace(PARIS_18, locations={})) == {"zone_ids": "-9531", "slug": "paris-18e-75018"}
    assert lookups.requested == ["https://res.bienici.com/suggest.json?q=75018"]


def test_a_neighbourhood_bienici_does_not_know_falls_back_to_the_smallest_zone_of_its_city_around_it(
        fixture_html):
    # Bien'ici's lookup only offers Montmartin and the like for "Montmartre"; Paris 18e holds it.
    bienici, lookups, _ = placer({"Montmartre": fixture_html("bienici/suggest_montmartre.json"),
                                  "Paris": fixture_html("bienici/suggest_paris.json")})
    assert bienici.place(MONTMARTRE) == {"zone_ids": "-9531", "slug": "paris-18e-75018"}
    assert lookups.requested == ["https://res.bienici.com/suggest.json?q=Montmartre",
                                 "https://res.bienici.com/suggest.json?q=Paris"]


def test_place_refuses_a_place_bienici_does_not_know(fixture_html):
    bienici, _, _ = placer({"Montmartre": fixture_html("bienici/suggest_montmartre.json")})
    with pytest.raises(CantPlace) as refused:
        bienici.place(MONTMARTRE)
    assert str(refused.value) == "Bien'ici doesn't know Montmartre, Paris, France."


def test_place_raises_a_scrape_error_when_the_lookup_is_down():
    with pytest.raises(ScrapeError, match="Bien'ici's place lookup isn't answering"):
        source(HttpResponse(500, ""))[0].place(PARIS)


def test_place_treats_a_garbled_lookup_answer_as_no_match():
    with pytest.raises(CantPlace):
        source("<html>not json")[0].place(PARIS)

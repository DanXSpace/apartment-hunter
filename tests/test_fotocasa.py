import json
from dataclasses import replace
from urllib.parse import parse_qsl, urlsplit

import pytest

from apartment_hunter.countries import Country, country
from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, summarize
from apartment_hunter.models import Listing
from apartment_hunter.sources import ALL_FILTERS, SOURCE_NAMES, BlockedError, CantPlace, ScrapeError
from apartment_hunter.sources.fotocasa import FotocasaSource
from apartment_hunter.sources.http import HttpResponse
from conftest import AT
from fakes import FakeHttp

CHAMBERI = Area("Chamberí, Madrid, Spain", "neighborhood", "ES", 40.4361, -3.7038, locations={
    "fotocasa": {"path": "madrid-capital/chamberi"},
})


def source(response: HttpResponse | str = "") -> tuple[FotocasaSource, FakeHttp]:
    http = FakeHttp(response)
    return FotocasaSource(http, now=lambda: AT), http


def query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query)


def test_results_url_is_the_newest_first_rental_search_for_the_saved_location():
    url = source()[0].results_url(Criteria(CHAMBERI))
    assert url == "https://www.fotocasa.es/es/alquiler/viviendas/madrid-capital/chamberi/l?sortType=publicationDate"


def test_filters_go_into_the_search_and_later_pages_add_the_page_number_to_the_path():
    fotocasa, http = source(HttpResponse(500, ""))
    criteria = Criteria(CHAMBERI, min_price=1500, max_price=2500, min_beds=2, min_baths=2.5)
    with pytest.raises(ScrapeError):
        fotocasa.fetch(criteria, 2)
    url = http.requested[0]
    assert urlsplit(url).path == "/es/alquiler/viviendas/madrid-capital/chamberi/l/2"
    assert query(url) == [("sortType", "publicationDate"), ("minPrice", "1500"), ("maxPrice", "2500"),
                          ("minRooms", "2"), ("minBathrooms", "2")]


def test_fotocasa_rooms_are_bedrooms_so_beds_pass_through_and_half_beds_round_up():
    # Fotocasa's "habitaciones" are bedrooms (a listing with 3 rooms describes "3 amplios dormitorios").
    assert ("minRooms", "2") in query(source()[0].results_url(Criteria(CHAMBERI, min_beds=1.5)))


def test_a_studio_minimum_sets_no_room_filter():
    assert query(source()[0].results_url(Criteria(CHAMBERI, min_beds=0))) == [("sortType", "publicationDate")]


@pytest.mark.parametrize("home_type,segment", [
    (HomeType.APARTMENT_CONDO, "pisos"),
    (HomeType.HOUSE, "todas-las-casas"),
    (HomeType.TOWNHOME, "adosados"),
])
def test_home_types_pick_fotocasas_property_type(home_type, segment):
    url = source()[0].results_url(Criteria(CHAMBERI, home_type=home_type))
    assert urlsplit(url).path == f"/es/alquiler/{segment}/madrid-capital/chamberi/l"


def test_pets_ask_for_listings_that_accept_pets():
    url = source()[0].results_url(Criteria(CHAMBERI, pets=Pets.SMALL_DOGS))
    assert urlsplit(url).path == "/es/alquiler/viviendas/madrid-capital/chamberi/se-aceptan-mascotas/l"


def test_fotocasa_filters_by_everything():
    assert FotocasaSource.filters == ALL_FILTERS


def test_parses_a_saved_real_page_newest_first(fixture_html):
    fotocasa, _ = source(fixture_html("fotocasa/chamberi_newest.html"))
    page = fotocasa.fetch(Criteria(CHAMBERI), 1)
    assert (len(page.listings), page.total, page.has_more) == (30, 760, True)
    assert page.listings[0] == Listing(
        source="fotocasa", listing_id="191456033",
        url="https://www.fotocasa.es/es/alquiler/vivienda/madrid-capital/aire-acondicionado/191456033/d",
        name="Studio", address="Calle de Sagasta, Trafalgar, Madrid", price_min=1250, price_max=1250,
        beds_min=0, beds_max=0, baths_min=1, baths_max=1,
        image_url="https://static.fotocasa.es/images/ads/8e0d3b51-d112-4573-9c7c-5794cc6399b7?rule=original",
        scraped_at=AT)


def test_rooms_are_read_as_bedrooms_and_missing_rooms_stay_unknown(fixture_html):
    fotocasa, _ = source(fixture_html("fotocasa/chamberi_newest.html"))
    listings = {listing.listing_id: listing for listing in fotocasa.fetch(Criteria(CHAMBERI), 1).listings}
    flat = listings["191445613"]  # a 3-room flat at 2.800 €
    assert (flat.name, flat.beds_min, flat.baths_min, flat.price_min) == ("3 bedroom flat", 3, 2, 2800)
    assert listings["191437788"].beds_min is None
    assert listings["191453231"].name == "2 bedroom penthouse"  # Fotocasa's "Attic"


def fotocasa_page(real_estates: list, count: int, page: int = 1) -> str:
    data = {"initialSearch": {"result": {"count": count, "realEstates": real_estates,
                                         "resultsV2": {"page": {"number": page, "size": 30}}}}}
    return f'<html><script type="application/json" id="__initial_props__">{json.dumps(data)}</script></html>'


AD = {"id": 1, "buildingSubtype": "Flat", "features": [{"key": "rooms", "value": 2}], "rawPrice": 900,
      "periodicityId": 3, "location": "Calle Mayor", "address": {"municipality": "Madrid"},
      "detail": {"es-ES": "/es/alquiler/vivienda/madrid-capital/1/d"}, "multimedia": []}


def test_a_listing_without_photos_or_baths_and_rents_for_other_periods():
    ads = [AD, {**AD, "id": 2, "periodicityId": 1}, {**AD, "id": 3, "rawPrice": 0},
           {**AD, "id": 4, "buildingSubtype": "SemiDetached"}]
    fotocasa, _ = source(fotocasa_page(ads, 4))
    plain, other_period, unpriced, semi = fotocasa.fetch(Criteria(CHAMBERI), 1).listings
    assert (plain.name, plain.price_min, plain.beds_min, plain.baths_min, plain.image_url) == (
        "2 bedroom flat", 900, 2, None, None)
    assert plain.address == "Calle Mayor, Madrid"
    assert other_period.price_min is None
    assert unpriced.price_min is None
    assert semi.name == "2 bedroom semi detached"


def test_the_last_page_has_no_more():
    page = source(fotocasa_page([AD], 31, page=2))[0].fetch(Criteria(CHAMBERI), 2)
    assert (page.total, page.has_more) == (31, False)


def test_zero_matches_is_an_empty_page_not_an_error():
    page = source(fotocasa_page([], 0))[0].fetch(Criteria(CHAMBERI), 1)
    assert (page.listings, page.total, page.has_more) == ([], 0, False)


@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError, match="Fotocasa is blocking"):
        source(HttpResponse(status, ""))[0].fetch(Criteria(CHAMBERI), 1)


def test_other_http_errors_are_plain_scrape_errors():
    with pytest.raises(ScrapeError, match=r"Fotocasa answered with an error \(HTTP 404\)\.") as e:
        source(HttpResponse(404, ""))[0].fetch(Criteria(CHAMBERI), 1)
    assert not isinstance(e.value, BlockedError)


def test_a_captcha_page_is_blocked():
    with pytest.raises(BlockedError):
        source('<html><script src="https://ct.captcha-delivery.com/c.js"></script></html>')[0].fetch(
            Criteria(CHAMBERI), 1)


@pytest.mark.parametrize("html", [
    "<html><body>Página no encontrada</body></html>",
    '<html><script type="application/json" id="__initial_props__">{"initialSearch": {}}</script></html>',
    '<html><script type="application/json" id="__initial_props__">not json</script></html>',
])
def test_page_without_listing_data_is_a_layout_change(html):
    with pytest.raises(ScrapeError, match="Fotocasa's page layout changed"):
        source(html)[0].fetch(Criteria(CHAMBERI), 1)


def placer(response: HttpResponse | str) -> tuple[FotocasaSource, FakeHttp]:
    lookups = FakeHttp(response)
    return FotocasaSource(FakeHttp(), now=lambda: AT, lookup_http=lookups), lookups


def test_place_finds_a_district_on_its_citys_page(fixture_html):
    fotocasa, lookups = placer(fixture_html("fotocasa/madrid_city.html"))
    assert fotocasa.place(replace(CHAMBERI, locations={})) == {"path": "madrid-capital/chamberi"}
    # Fotocasa sends a city's plain name on to its page ("madrid" to "madrid-capital").
    assert lookups.requested == ["https://www.fotocasa.es/es/alquiler/viviendas/madrid/todas-las-zonas/l"]


def test_place_finds_a_neighbourhood_inside_a_district(fixture_html):
    fotocasa, _ = placer(fixture_html("fotocasa/madrid_city.html"))
    arguelles = Area("Argüelles, Madrid, Spain", "neighborhood", "ES", 40.43, -3.72)
    assert fotocasa.place(arguelles) == {"path": "madrid-capital/arguelles"}


def test_place_searches_a_whole_city(fixture_html):
    fotocasa, _ = placer(fixture_html("fotocasa/madrid_city.html"))
    madrid = Area("Madrid, Community of Madrid, Spain", "city", "ES", 40.42, -3.70)
    assert fotocasa.place(madrid) == {"path": "madrid-capital/todas-las-zonas"}


def test_place_uses_the_spanish_name_of_a_city_photon_spells_in_english():
    fotocasa, lookups = placer(HttpResponse(404, ""))
    with pytest.raises(CantPlace):
        fotocasa.place(Area("Seville, Andalusia, Spain", "city", "ES", 37.39, -5.99))
    assert lookups.requested == ["https://www.fotocasa.es/es/alquiler/viviendas/sevilla/todas-las-zonas/l"]


def test_place_refuses_a_district_fotocasa_does_not_know(fixture_html):
    fotocasa, _ = placer(fixture_html("fotocasa/madrid_city.html"))
    with pytest.raises(CantPlace) as refused:
        fotocasa.place(Area("Malasaña, Madrid, Spain", "neighborhood", "ES", 40.43, -3.70))
    assert str(refused.value) == "Fotocasa doesn't know Malasaña, Madrid, Spain."


def test_place_refuses_a_city_fotocasa_has_no_page_for():
    fotocasa, _ = placer(HttpResponse(404, ""))
    with pytest.raises(CantPlace) as refused:
        fotocasa.place(Area("Villanueva, Asturias, Spain", "city", "ES", 43.4, -5.2))
    assert str(refused.value) == "Fotocasa doesn't know Villanueva, Asturias, Spain."


def test_place_refuses_a_postcode_without_asking_fotocasa():
    fotocasa, lookups = placer("")
    with pytest.raises(CantPlace) as refused:
        fotocasa.place(Area("28004, Madrid, Spain", "zipcode", "ES", 40.42, -3.70))
    assert str(refused.value) == "Fotocasa can't search by postcode. Try the district or city instead."
    assert lookups.requested == []


@pytest.mark.parametrize("response", [HttpResponse(500, ""), HttpResponse(403, "")])
def test_place_raises_a_scrape_error_when_fotocasa_does_not_answer(response):
    with pytest.raises(ScrapeError, match="Fotocasa isn't answering"):
        placer(response)[0].place(replace(CHAMBERI, locations={}))


def test_spanish_searches_read_fotocasa_in_euros():
    assert country("ES") == Country("ES", "Spain", "€", ("fotocasa",))
    assert SOURCE_NAMES["fotocasa"] == "Fotocasa"
    summary = summarize(Criteria(CHAMBERI, min_beds=2, max_price=1500))
    assert summary == "Chamberí, Madrid, Spain · 2+ bd · up to €1,500"


def test_place_treats_a_page_without_data_as_no_match():
    with pytest.raises(CantPlace):
        placer("<html>nothing here</html>")[0].place(replace(CHAMBERI, locations={}))

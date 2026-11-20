import json
from dataclasses import replace
from urllib.parse import parse_qsl, urlsplit

import pytest

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import BlockedError, CantPlace, Filter, ScrapeError
from apartment_hunter.sources.http import HttpResponse
from apartment_hunter.sources.rightmove import RightmoveSource
from conftest import AT
from fakes import FakeHttp, ScriptedHttp

CAMDEN_TOWN = Area("Camden Town, London, United Kingdom", "neighborhood", "GB", 51.5390, -0.1426, locations={
    "rightmove": {"location_id": "REGION^85262", "radius_miles": 0.0},
})


def source(response: HttpResponse | str = "") -> tuple[RightmoveSource, FakeHttp]:
    http = FakeHttp(response)
    return RightmoveSource(http, now=lambda: AT), http


def query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query)


def rightmove_page(properties: list, result_count: str, next_index: str | None = None) -> str:
    pagination = {"total": 1, "first": "0", "last": "0", "page": "1"}
    if next_index is not None:
        pagination["next"] = next_index
    data = {"props": {"pageProps": {"searchResults": {
        "properties": properties, "resultCount": result_count, "pagination": pagination,
    }}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_results_url_is_the_newest_first_rental_search_for_the_saved_location():
    url = source()[0].results_url(Criteria(CAMDEN_TOWN))
    assert url.startswith("https://www.rightmove.co.uk/property-to-rent/find.html?")
    assert query(url) == [("locationIdentifier", "REGION^85262"), ("radius", "0.0"), ("sortType", "6"),
                          ("channel", "RENT")]


def test_filters_go_into_the_search_and_later_pages_start_24_results_further_on():
    rightmove, http = source(rightmove_page([], "0"))
    criteria = Criteria(CAMDEN_TOWN, min_price=1500, max_price=2100, min_beds=2, min_baths=1.5,
                        home_type=HomeType.HOUSE)
    rightmove.fetch(criteria, 3)
    assert query(http.requested[0]) == [
        ("locationIdentifier", "REGION^85262"), ("radius", "0.0"), ("sortType", "6"), ("channel", "RENT"),
        ("index", "48"), ("minPrice", "1500"), ("maxPrice", "2100"), ("minBedrooms", "2"),
        ("minBathrooms", "1"), ("propertyTypes", "detached,semi-detached,terraced,bungalow"),
    ]


@pytest.mark.parametrize("home_type,types", [
    (HomeType.APARTMENT_CONDO, "flat"),
    (HomeType.TOWNHOME, "terraced"),
])
def test_home_types(home_type, types):
    url = source()[0].results_url(Criteria(CAMDEN_TOWN, home_type=home_type))
    assert ("propertyTypes", types) in query(url)


def test_a_studio_minimum_is_zero_bedrooms():
    url = source()[0].results_url(Criteria(CAMDEN_TOWN, min_beds=0))
    assert ("minBedrooms", "0") in query(url)


def test_rightmove_cannot_filter_by_pets():
    assert RightmoveSource.filters == frozenset({Filter.PRICE, Filter.BEDS, Filter.BATHS, Filter.HOME_TYPE})
    url = source()[0].results_url(Criteria(CAMDEN_TOWN, pets=Pets.CATS))
    assert len(query(url)) == 4


def test_parses_a_saved_real_page_leaving_out_the_featured_advert(fixture_html):
    rightmove, _ = source(fixture_html("rightmove/camden_town_rentals.html"))
    page = rightmove.fetch(Criteria(CAMDEN_TOWN), 2)
    assert (len(page.listings), page.total, page.has_more) == (24, 112, True)
    assert "95620908" not in [listing.listing_id for listing in page.listings]  # the featured one
    assert page.listings[0] == Listing(
        source="rightmove", listing_id="94950687", url="https://www.rightmove.co.uk/properties/94950687",
        name="1 bedroom flat", address="York Way, London N7", price_min=2200, price_max=2200,
        beds_min=1, beds_max=1, baths_min=1, baths_max=1,
        image_url="https://media.rightmove.co.uk:443/dir/crop/10:9-16:9/property-photo/677b9a7bc/94950687/"
                  "677b9a7bcb1e06d4bc651a5b2489ee10_max_476x317.jpeg",
        scraped_at=AT)


def test_weekly_rents_are_read_per_calendar_month(fixture_html):
    rightmove, _ = source(fixture_html("rightmove/camden_town_rentals.html"))
    listings = {listing.listing_id: listing for listing in rightmove.fetch(Criteria(CAMDEN_TOWN), 2).listings}
    # Rightmove's own page shows £500 pw as £2,167 pcm.
    assert (listings["95537220"].price_min, listings["95537220"].price_max) == (2167, 2167)


CARD = {"id": 1, "bedrooms": 0, "bathrooms": None, "displayAddress": "Camden Road, NW1",
        "propertyTypeFullDescription": "Studio flat", "propertyUrl": "/properties/1#/?channel=RES_LET",
        "price": {"amount": 1615, "frequency": "weekly"}, "images": [], "featuredProperty": False}


def test_a_studio_without_photos_or_baths_and_other_rent_periods():
    rightmove, _ = source(rightmove_page([CARD, {**CARD, "id": 2, "price": {"amount": 30000,
                                                                             "frequency": "yearly"}},
                                          {**CARD, "id": 3, "price": {"amount": None}}], "3"))
    first, yearly, unpriced = rightmove.fetch(Criteria(CAMDEN_TOWN), 1).listings
    assert (first.price_min, first.beds_min, first.baths_min, first.image_url) == (6998, 0, None, None)
    assert yearly.price_min == 2500
    assert unpriced.price_min is None


def test_zero_matches_is_an_empty_page_not_an_error():
    page = source(rightmove_page([], "0"))[0].fetch(Criteria(CAMDEN_TOWN), 1)
    assert (page.listings, page.total, page.has_more) == ([], 0, False)


@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError, match="Rightmove is blocking"):
        source(HttpResponse(status, ""))[0].fetch(Criteria(CAMDEN_TOWN), 1)


def test_other_http_errors_are_plain_scrape_errors():
    with pytest.raises(ScrapeError, match=r"Rightmove answered with an error \(HTTP 500\)\.") as e:
        source(HttpResponse(500, ""))[0].fetch(Criteria(CAMDEN_TOWN), 1)
    assert not isinstance(e.value, BlockedError)


def test_an_access_denied_page_is_blocked():
    with pytest.raises(BlockedError):
        source("<html><title>Access Denied</title></html>")[0].fetch(Criteria(CAMDEN_TOWN), 1)


def test_page_without_listing_data_is_a_layout_change():
    with pytest.raises(ScrapeError, match="Rightmove's page layout changed"):
        source("<html><body>Page not found</body></html>")[0].fetch(Criteria(CAMDEN_TOWN), 1)


def test_place_finds_the_region_through_rightmoves_typeahead(fixture_html):
    lookups = FakeHttp(fixture_html("rightmove/typeahead_camden_town.xml"))
    rightmove = RightmoveSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    assert rightmove.place(replace(CAMDEN_TOWN, locations={})) == {
        "location_id": "REGION^85262", "radius_miles": 0.0}
    assert lookups.requested == [
        "https://los.rightmove.co.uk/typeahead?query=Camden%20Town&limit=10&exclude=STREET"]


OUTCODES = ("<TypeaheadDTO><matches>"
            "<matches><id>2497</id><type>OUTCODE</type><displayName>SW11</displayName></matches>"
            "<matches><id>2504</id><type>OUTCODE</type><displayName>SW18</displayName></matches>"
            "</matches></TypeaheadDTO>")


SW11_6HR = Area("SW11 6HR, London, United Kingdom", "zipcode", "GB", 51.47, -0.16)
NO_MATCHES = "<TypeaheadDTO><matches/></TypeaheadDTO>"


def test_place_finds_a_full_postcode_as_rightmoves_postcode_location(fixture_html):
    lookups = FakeHttp(fixture_html("rightmove/typeahead_sw11_6hr.xml"))
    rightmove = RightmoveSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    assert rightmove.place(SW11_6HR) == {"location_id": "POSTCODE^832581", "radius_miles": 0.25}
    assert lookups.requested == [
        "https://los.rightmove.co.uk/typeahead?query=SW11%206HR&limit=10&exclude=STREET"]


def test_a_postcode_rightmove_does_not_offer_falls_back_to_its_outward_code_and_says_so():
    lookups = ScriptedHttp(NO_MATCHES, OUTCODES)
    rightmove = RightmoveSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    assert rightmove.place(SW11_6HR) == {
        "location_id": "OUTCODE^2497", "radius_miles": 0.0,
        "note": "Rightmove doesn't know SW11 6HR, so it searches all of SW11."}
    assert lookups.requested[1] == "https://los.rightmove.co.uk/typeahead?query=SW11&limit=10&exclude=STREET"


def test_a_postcode_rightmove_does_not_know_at_all_is_left_out():
    rightmove = RightmoveSource(FakeHttp(), now=lambda: AT, lookup_http=ScriptedHttp(NO_MATCHES, NO_MATCHES))
    with pytest.raises(CantPlace) as refused:
        rightmove.place(SW11_6HR)
    assert str(refused.value) == "Rightmove doesn't know SW11 6HR, London, United Kingdom."


def test_place_refuses_a_place_rightmove_does_not_know(fixture_html):
    rightmove, _ = source(fixture_html("rightmove/typeahead_camden_town.xml"))
    kentish = Area("Kentish Town, London, United Kingdom", "neighborhood", "GB", 51.55, -0.14)
    with pytest.raises(CantPlace) as refused:
        rightmove.place(kentish)
    assert str(refused.value) == "Rightmove doesn't know Kentish Town, London, United Kingdom."


def test_place_raises_a_scrape_error_when_the_typeahead_is_down():
    with pytest.raises(ScrapeError, match="Rightmove's place lookup isn't answering"):
        source(HttpResponse(500, ""))[0].place(replace(CAMDEN_TOWN, locations={}))


def test_place_treats_a_garbled_typeahead_answer_as_no_match():
    with pytest.raises(CantPlace):
        source("not xml <")[0].place(replace(CAMDEN_TOWN, locations={}))

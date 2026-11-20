import json
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import BlockedError, CantPlace, ScrapeError
from apartment_hunter.sources.http import HttpResponse
from apartment_hunter.sources.zillow import (
    ZillowSource,
    parse_search_page,
    parse_suggestions,
    search_url,
    suggestions_url,
)
from conftest import AT, CHICAGO, LINCOLN_PARK
from fakes import FakeHttp


def query_state(url: str) -> dict:
    return json.loads(parse_qs(urlsplit(url).query)["searchQueryState"][0])


def zillow_page(cards: list, total: int, next_url: str | None = None) -> str:
    data = {"props": {"pageProps": {"searchPageState": {"cat1": {
        "searchResults": {"listResults": cards},
        "searchList": {"totalResultCount": total, "pagination": {"nextUrl": next_url} if next_url else {}},
    }}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


SINGLE = {"zpid": "456940401", "id": "456940401",
          "detailUrl": "https://www.zillow.com/apartments/chicago-il/inside-info-fremont/CrvVmL/",
          "statusText": "Apartment for rent", "price": "$2,550/mo", "unformattedPrice": 2550,
          "address": "Inside Info Fremont, 1529 N Fremont St #217, Chicago, IL 60642",
          "beds": 1, "baths": 1, "imgSrc": "https://photos.zillowstatic.com/fp/d17f.jpg",
          "hdpData": {"homeInfo": {"homeType": "APARTMENT"}}}
BUILDING = {"zpid": "41.88502--87.6548", "id": "41.88502--87.6548",
            "detailUrl": "/apartments/chicago-il/one-six-six/Chg442/", "statusText": "One Six Six",
            "address": "166 N Aberdeen St, Chicago, IL", "buildingName": "One Six Six", "isBuilding": True,
            "units": [{"price": "$2,263+", "beds": "0"}, {"price": "$2,688+", "beds": "1"},
                      {"price": "$4,194+", "beds": "2"}],
            "imgSrc": "https://photos.zillowstatic.com/fp/0457.jpg"}
BASE_KEYS = {"sortSelection", "isForRent", "isForSaleByAgent", "isForSaleByOwner",
             "isNewConstruction", "isComingSoon", "isAuction", "isForSaleForeclosure"}


def test_search_url_carries_region_filters_paging_and_newest_first():
    c = Criteria(LINCOLN_PARK, min_price=1500, max_price=2400, min_beds=2, min_baths=1,
                 pets=Pets.SMALL_DOGS)
    url = search_url(c, page=2)
    assert url.startswith("https://www.zillow.com/homes/for_rent/?searchQueryState=")
    state = query_state(url)
    assert state["regionSelection"] == [{"regionId": 269590, "regionType": 8}]
    assert state["pagination"] == {"currentPage": 2}
    f = state["filterState"]
    assert f["sortSelection"] == {"value": "days"} and f["isForRent"] == {"value": True}
    assert f["monthlyPayment"] == {"min": 1500, "max": 2400}
    assert f["beds"] == {"min": 2} and f["baths"] == {"min": 1}
    assert f["onlyRentalSmallDogsAllowed"] == {"value": True}


def test_page_one_has_empty_paging_and_only_the_base_filters():
    state = query_state(search_url(Criteria(CHICAGO, max_price=2400)))
    assert state["pagination"] == {}
    assert set(state["filterState"]) == BASE_KEYS | {"monthlyPayment"}
    assert state["filterState"]["monthlyPayment"] == {"max": 2400}


@pytest.mark.parametrize("home_type,excluded", [
    (HomeType.HOUSE,
     {"isApartment", "isCondo", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand"}),
    (HomeType.APARTMENT_CONDO,
     {"isSingleFamily", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand"}),
    (HomeType.TOWNHOME,
     {"isSingleFamily", "isApartment", "isCondo", "isMultiFamily", "isManufactured", "isLotLand"}),
])
def test_home_type_excludes_the_other_types(home_type, excluded):
    f = query_state(search_url(Criteria(CHICAGO, home_type=home_type)))["filterState"]
    assert set(f) - BASE_KEYS == excluded
    assert all(f[k] == {"value": False} for k in excluded)


def test_large_dogs_filter():
    f = query_state(search_url(Criteria(CHICAGO, pets=Pets.LARGE_DOGS)))["filterState"]
    assert set(f) - BASE_KEYS == {"onlyRentalLargeDogsAllowed"}
    assert f["onlyRentalLargeDogsAllowed"] == {"value": True}


def test_cats_filter():
    f = query_state(search_url(Criteria(CHICAGO, pets=Pets.CATS)))["filterState"]
    assert f["onlyRentalCatsAllowed"] == {"value": True}


def test_parses_a_single_unit():
    cards, total, has_more = parse_search_page(zillow_page([SINGLE], 205, "/x/2_p/"), AT)
    assert (total, has_more, cards[0].home_type) == (205, True, "APARTMENT")
    assert cards[0].listing == Listing(
        source="zillow", listing_id="456940401",
        url="https://www.zillow.com/apartments/chicago-il/inside-info-fremont/CrvVmL/", name="",
        address="Inside Info Fremont, 1529 N Fremont St #217, Chicago, IL 60642",
        price_min=2550, price_max=2550, beds_min=1, beds_max=1, baths_min=1, baths_max=1,
        image_url="https://photos.zillowstatic.com/fp/d17f.jpg", scraped_at=AT)


def test_parses_a_building_as_ranges_with_an_absolute_url():
    card = parse_search_page(zillow_page([BUILDING], 1), AT)[0][0]
    listing = card.listing
    assert (card.home_type, listing.listing_id, listing.name) == (
        "BUILDING", "41.88502--87.6548", "One Six Six")
    assert listing.url == "https://www.zillow.com/apartments/chicago-il/one-six-six/Chg442/"
    assert (listing.price_min, listing.price_max, listing.beds_min, listing.beds_max, listing.baths_min) == (
        2263, 4194, 0, 2, None)


def test_missing_price_is_none():
    card = {**SINGLE, "price": "Contact for price", "unformattedPrice": None}
    listing = parse_search_page(zillow_page([card], 1), AT)[0][0].listing
    assert (listing.price_min, listing.price_max) == (None, None)


def test_zero_matches_is_an_empty_page_not_an_error():
    assert parse_search_page(zillow_page([], 0), AT) == ([], 0, False)


def test_challenge_page_is_blocked():
    with pytest.raises(BlockedError, match="Zillow is blocking"):
        parse_search_page('<html><div id="px-captcha"></div></html>', AT)


def test_page_without_listing_data_is_a_layout_change():
    with pytest.raises(ScrapeError, match="layout changed"):
        parse_search_page("<html><body>hello</body></html>", AT)


def test_fetch_drops_other_home_types_and_reports_the_page():
    house = {**SINGLE, "zpid": "9", "hdpData": {"homeInfo": {"homeType": "SINGLE_FAMILY"}}}
    http = FakeHttp(HttpResponse(200, zillow_page([BUILDING, house], 2)))
    page = ZillowSource(http, now=lambda: AT).fetch(Criteria(CHICAGO, home_type=HomeType.HOUSE), 1)
    assert [listing.listing_id for listing in page.listings] == ["9"]
    assert (page.total, page.has_more) == (2, False)
    assert len(http.requested) == 1
    state = query_state(http.requested[0])
    assert state["regionSelection"] == [{"regionId": 17426, "regionType": 6}]
    assert state["pagination"] == {}
    assert set(state["filterState"]) - BASE_KEYS == {
        "isApartment", "isCondo", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand"}


@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError):
        ZillowSource(FakeHttp(HttpResponse(status, "")), now=lambda: AT).fetch(Criteria(CHICAGO), 1)


def test_other_http_errors_are_plain_scrape_errors():
    with pytest.raises(ScrapeError, match=r"Zillow answered with an error \(HTTP 500\)\.") as e:
        ZillowSource(FakeHttp(HttpResponse(500, "")), now=lambda: AT).fetch(Criteria(CHICAGO), 1)
    assert not isinstance(e.value, BlockedError)


def test_parses_a_saved_real_page(fixture_html):
    cards, total, has_more = parse_search_page(fixture_html("zillow/chicago_rentals.html"), AT)
    assert len(cards) == 5 and total is not None and total > 0 and has_more
    assert all(
        c.listing.listing_id and c.listing.url.startswith("https://www.zillow.com/") and c.listing.address
        for c in cards)


PLACES_BODY = json.dumps({"results": [
    {"display": "Lincoln Park, Chicago, IL", "resultType": "Region",
     "metaData": {"regionId": 269590, "regionType": "neighborhood", "lat": 41.9237, "lng": -87.6505}},
    {"display": "Chicago, IL", "resultType": "Region",
     "metaData": {"regionId": 17426, "regionType": "city", "lat": 41.8781, "lng": -87.6298}},
]})


def test_place_finds_the_zillow_region_through_its_place_lookup():
    runs, lookups = FakeHttp(), FakeHttp(PLACES_BODY)
    source = ZillowSource(runs, now=lambda: AT, lookup_http=lookups)
    assert source.place(replace(CHICAGO, locations={})) == {"region_id": 17426, "region_type": 6}
    assert source.place(replace(LINCOLN_PARK, locations={})) == {"region_id": 269590, "region_type": 8}
    assert runs.requested == []
    assert lookups.requested[0].endswith("?q=Chicago%2C%20IL")


def test_place_refuses_an_area_zillow_does_not_know():
    source = ZillowSource(FakeHttp(json.dumps({"results": []})), now=lambda: AT)
    with pytest.raises(CantPlace, match="Zillow doesn't know Chicago, IL"):
        source.place(replace(CHICAGO, locations={}))


def test_place_raises_a_scrape_error_when_the_lookup_is_down():
    source = ZillowSource(FakeHttp(HttpResponse(500, "")), now=lambda: AT)
    with pytest.raises(ScrapeError, match="place lookup isn't answering"):
        source.place(replace(CHICAGO, locations={}))


def region(display: str, kind: str, region_id: int, lat: float, lng: float) -> dict:
    return {"display": display, "resultType": "Region",
            "metaData": {"regionId": region_id, "regionType": kind, "lat": lat, "lng": lng}}


LINCOLN_PARKS = json.dumps({"results": [
    region("Lincoln Park, MI", "city", 26148, 42.2506, -83.1785),
    region("Lincoln Park, NJ", "city", 25390, 40.9242, -74.3021),
    region("Lincoln Park, Chicago, IL", "neighborhood", 269590, 41.9237, -87.6505),
]})


def test_place_finds_a_photon_area_on_the_nearby_zillow_region_of_its_kind():
    lookups = FakeHttp(LINCOLN_PARKS)
    source = ZillowSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    area = Area("Lincoln Park, IL, United States", "neighborhood", "US", 41.9214, -87.6475)
    assert source.place(area) == {"region_id": 269590, "region_type": 8}
    assert lookups.requested[0].endswith("?q=Lincoln%20Park%2C%20IL")


def test_place_looks_up_a_zip_code_by_itself():
    lookups = FakeHttp(json.dumps({"results": [region("60614", "zipcode", 84617, 41.9227, -87.6533)]}))
    source = ZillowSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    area = Area("60614, IL, United States", "zipcode", "US", 41.92, -87.65)
    assert source.place(area) == {"region_id": 84617, "region_type": 7}
    assert lookups.requested[0].endswith("?q=60614")


def test_place_refuses_a_zillow_region_far_from_the_area():
    source = ZillowSource(FakeHttp(LINCOLN_PARKS), now=lambda: AT)
    area = Area("Lincoln Park, NY, United States", "city", "US", 41.95, -74.0)
    with pytest.raises(CantPlace, match="Zillow doesn't know Lincoln Park, NY, United States"):
        source.place(area)


def test_only_cities_neighborhoods_and_zip_codes_are_offered():
    body = json.dumps({"results": [
        region("Lincoln Park, Chicago, IL", "neighborhood", 269590, 41.9237, -87.6505),
        {"display": "1906 N Lincoln Park W Chicago, IL 60614", "resultType": "Address", "metaData": {}},
        region("Illinois", "state", 21, 40.0, -89.0),
        region("Chicago, IL", "city", 17426, 41.8781, -87.6298),
    ]})
    assert [p.display for p in parse_suggestions(body)] == ["Lincoln Park, Chicago, IL", "Chicago, IL"]


def test_suggestions_url_encodes_the_text():
    assert (suggestions_url("Coeur d'Alene, ID")
            == "https://www.zillowstatic.com/autocomplete/v3/suggestions?q=Coeur%20d%27Alene%2C%20ID")

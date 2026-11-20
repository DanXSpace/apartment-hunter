import json
from dataclasses import replace
from urllib.parse import parse_qsl, urlsplit

import pytest

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import ALL_FILTERS, BlockedError, CantPlace, ScrapeError
from apartment_hunter.sources.http import HttpResponse
from apartment_hunter.sources.zoopla import ZooplaSource
from conftest import AT
from fakes import FakeHttp, ScriptedHttp

CAMDEN_TOWN = Area("Camden Town, London, United Kingdom", "neighborhood", "GB", 51.5390, -0.1426, locations={
    "zoopla": {"geo": "camden-town"},
})


def source(response: HttpResponse | str = "") -> tuple[ZooplaSource, FakeHttp]:
    http = FakeHttp(response)
    return ZooplaSource(http, now=lambda: AT), http


def query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query)


def test_results_url_is_the_newest_first_rental_search_for_the_saved_place():
    url = source()[0].results_url(Criteria(CAMDEN_TOWN))
    assert url == "https://www.zoopla.co.uk/to-rent/property/camden-town/?results_sort=newest_listings"


def test_filters_go_into_the_search_and_later_pages_are_numbered(fixture_html):
    zoopla, http = source(fixture_html("zoopla/sw11_no_matches.html"))
    criteria = Criteria(CAMDEN_TOWN, min_price=1500, max_price=2100, min_beds=2, min_baths=1.5,
                        home_type=HomeType.APARTMENT_CONDO, pets=Pets.SMALL_DOGS)
    zoopla.fetch(criteria, 3)
    url = http.requested[0]
    assert url.startswith("https://www.zoopla.co.uk/to-rent/flats/camden-town/?")
    assert query(url) == [
        ("results_sort", "newest_listings"), ("pn", "3"), ("price_min", "1500"), ("price_max", "2100"),
        ("beds_min", "2"), ("baths_min", "1"), ("pets_allowed", "true"),
    ]


@pytest.mark.parametrize("home_type,path", [
    (HomeType.HOUSE, "/to-rent/houses/camden-town/"),
    (HomeType.TOWNHOME, "/to-rent/houses/camden-town/"),  # Zoopla has no terraced-only search
])
def test_home_types_pick_the_search_path(home_type, path):
    url = source()[0].results_url(Criteria(CAMDEN_TOWN, home_type=home_type))
    assert urlsplit(url).path == path


def test_a_studio_minimum_is_zero_bedrooms():
    url = source()[0].results_url(Criteria(CAMDEN_TOWN, min_beds=0))
    assert ("beds_min", "0") in query(url)


def test_zoopla_can_apply_every_filter_but_says_townhomes_mean_all_houses():
    assert ZooplaSource.filters == ALL_FILTERS
    assert ZooplaSource.home_type_notes == {
        HomeType.TOWNHOME: "Zoopla can't tell townhomes from houses, so you'll see all houses."}
    assert "/to-rent/houses/" in source()[0].results_url(Criteria(CAMDEN_TOWN, home_type=HomeType.TOWNHOME))


def test_parses_a_saved_real_page_leaving_out_the_featured_adverts(fixture_html):
    zoopla, _ = source(fixture_html("zoopla/camden_town_rentals_page2.html"))
    page = zoopla.fetch(Criteria(CAMDEN_TOWN), 2)
    assert (len(page.listings), page.total, page.has_more) == (25, 613, True)
    ids = [listing.listing_id for listing in page.listings]
    assert not {"74765592", "68533230", "60471185"} & set(ids)  # the three featured adverts
    assert page.listings[0] == Listing(
        source="zoopla", listing_id="74806074", url="https://www.zoopla.co.uk/to-rent/details/74806074/",
        name="3 bed maisonette", address="Gloucester Avenue, Primrose Hill NW1", price_min=4850,
        price_max=4850, beds_min=3, beds_max=3, baths_min=1, baths_max=1,
        image_url="https://lid.zoocdn.com/645/430/653930c4e83e2dbe6a60ae1dc67265acf4c1b1c4.jpg",
        scraped_at=AT)


def test_rents_are_read_per_calendar_month(fixture_html):
    zoopla, _ = source(fixture_html("zoopla/camden_town_rentals_page2.html"))
    listings = {listing.listing_id: listing for listing in zoopla.fetch(Criteria(CAMDEN_TOWN), 2).listings}
    # Advertised at £265 pw; Zoopla's own page shows £1,148 pcm.
    assert (listings["74806034"].price_min, listings["74806034"].price_max) == (1148, 1148)
    # A studio Zoopla shows no bed or bath count for.
    studio = listings["70776471"]
    assert (studio.name, studio.beds_min, studio.baths_min) == ("Studio", 0, None)


def test_zero_matches_is_an_empty_page_not_an_error(fixture_html):
    page = source(fixture_html("zoopla/sw11_no_matches.html"))[0].fetch(Criteria(CAMDEN_TOWN), 1)
    assert (page.listings, page.total, page.has_more) == ([], 0, False)


def zoopla_page(listings: list, total: int, page: int = 1, last_page: int = 1) -> str:
    """A results page shaped like Zoopla's: its data streamed in two Next.js chunks."""
    data = json.dumps({"regularListingsFormatted": listings, "featuredListingsFormatted": [],
                       "pagination": {"pageNumber": page, "pageNumberMax": last_page, "totalResults": total}})
    half = len(data) // 2
    chunks = "".join(f"<script>self.__next_f.push([1,{json.dumps(part)}])</script>"
                     for part in (data[:half], data[half:]))
    return f"<html><body>{chunks}</body></html>"


CARD = {"listingId": "1", "title": "1 bed flat to rent", "address": "Camden Road, NW1", "price": "£500 pw",
        "features": [{"content": 1, "iconId": "bed"}], "listingUris": {"detail": "/to-rent/details/1/"},
        "image": None}


def test_weekly_and_yearly_rents_and_missing_prices_photos_and_baths():
    zoopla, _ = source(zoopla_page([CARD, {**CARD, "listingId": "2", "price": "£30,000 pa"},
                                    {**CARD, "listingId": "3", "price": "POA"}], 3))
    weekly, yearly, unpriced = zoopla.fetch(Criteria(CAMDEN_TOWN), 1).listings
    assert (weekly.price_min, weekly.beds_min, weekly.baths_min, weekly.image_url) == (2167, 1, None, None)
    assert yearly.price_min == 2500
    assert unpriced.price_min is None


def test_the_last_page_has_no_more():
    page = source(zoopla_page([CARD], 26, page=2, last_page=2))[0].fetch(Criteria(CAMDEN_TOWN), 2)
    assert (len(page.listings), page.total, page.has_more) == (1, 26, False)


@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError, match="Zoopla is blocking"):
        source(HttpResponse(status, ""))[0].fetch(Criteria(CAMDEN_TOWN), 1)


def test_other_http_errors_are_plain_scrape_errors():
    with pytest.raises(ScrapeError, match=r"Zoopla answered with an error \(HTTP 500\)\.") as e:
        source(HttpResponse(500, ""))[0].fetch(Criteria(CAMDEN_TOWN), 1)
    assert not isinstance(e.value, BlockedError)


def test_a_bot_check_page_is_blocked():
    with pytest.raises(BlockedError):
        source("<html><title>Just a moment...</title></html>")[0].fetch(Criteria(CAMDEN_TOWN), 1)


def test_place_finds_the_place_through_zooplas_search_box(fixture_html):
    lookups = FakeHttp(fixture_html("zoopla/search_camden_town.html"))
    zoopla = ZooplaSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    assert zoopla.place(replace(CAMDEN_TOWN, locations={})) == {"geo": "camden-town"}
    assert lookups.requested == ["https://www.zoopla.co.uk/search/?q=camden+town&section=to-rent"]


SW11_6HR = Area("SW11 6HR, London, United Kingdom", "zipcode", "GB", 51.47, -0.16)
SW11_PAGE = (zoopla_page([], 0) + "<script>self.__next_f.push([1,"
             + json.dumps('{"geoData":{"geoIdentifier":"sw11","geoType":"outcode","label":"SW11"}}')
             + "])</script>")


def test_place_finds_a_full_postcode_through_zooplas_search_box(fixture_html):
    lookups = FakeHttp(fixture_html("zoopla/search_sw11_6hr.html"))
    zoopla = ZooplaSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    assert zoopla.place(SW11_6HR) == {"geo": "london/bolingbroke-grove/sw11-6hr"}
    assert lookups.requested == ["https://www.zoopla.co.uk/search/?q=sw11+6hr&section=to-rent"]


def test_a_postcode_zoopla_does_not_know_falls_back_to_its_outward_code_and_says_so(fixture_html):
    lookups = ScriptedHttp(fixture_html("zoopla/search_not_found.html"), SW11_PAGE)
    zoopla = ZooplaSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    assert zoopla.place(SW11_6HR) == {
        "geo": "sw11", "note": "Zoopla doesn't know SW11 6HR, so it searches all of SW11."}
    assert lookups.requested[1] == "https://www.zoopla.co.uk/search/?q=sw11&section=to-rent"


def test_zooplas_search_box_answering_with_a_server_error_leaves_zoopla_out():
    with pytest.raises(CantPlace) as refused:
        source(HttpResponse(503, ""))[0].place(replace(CAMDEN_TOWN, locations={}))
    assert str(refused.value) == ("Zoopla's place search answered with an error (HTTP 503). "
                                  "Try adding it again later with /search edit.")


class UnreachableHttp:
    def get(self, url: str) -> HttpResponse:
        raise ScrapeError("Couldn't reach www.zoopla.co.uk: timed out")


def test_place_refuses_a_place_zoopla_does_not_know(fixture_html):
    zoopla, _ = source(fixture_html("zoopla/search_not_found.html"))
    with pytest.raises(CantPlace) as refused:
        zoopla.place(replace(CAMDEN_TOWN, locations={}))
    assert str(refused.value) == "Zoopla doesn't know Camden Town, London, United Kingdom."


def test_place_refuses_a_search_that_lands_on_a_differently_named_place(fixture_html):
    zoopla, _ = source(fixture_html("zoopla/search_camden_town.html"))
    kentish = Area("Kentish Town, London, United Kingdom", "neighborhood", "GB", 51.55, -0.14)
    with pytest.raises(CantPlace, match="Zoopla doesn't know Kentish Town"):
        zoopla.place(kentish)


def test_place_raises_a_scrape_error_when_zoopla_cannot_be_reached():
    zoopla = ZooplaSource(FakeHttp(), now=lambda: AT, lookup_http=UnreachableHttp())
    with pytest.raises(ScrapeError, match="Zoopla's place lookup isn't answering"):
        zoopla.place(replace(CAMDEN_TOWN, locations={}))


def test_place_treats_a_garbled_answer_as_no_match():
    with pytest.raises(CantPlace):
        source("<script>self.__next_f.push([1,\"not json\"])</script>")[0].place(
            replace(CAMDEN_TOWN, locations={}))


@pytest.mark.parametrize("html", [
    "<html><body>Page not found</body></html>",
    zoopla_page([{"title": "a card without an id"}], 1),
])
def test_page_without_readable_listing_data_is_a_layout_change(html):
    with pytest.raises(ScrapeError, match="Zoopla's page layout changed"):
        source(html)[0].fetch(Criteria(CAMDEN_TOWN), 1)

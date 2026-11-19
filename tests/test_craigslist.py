import pytest

from apartment_hunter.criteria import Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import BlockedError, ScrapeError, SourcePage
from apartment_hunter.sources.craigslist import CraigslistSource, parse_search_page, search_url
from apartment_hunter.sources.http import HttpResponse
from conftest import AT, CHICAGO, LINCOLN_PARK
from fakes import FakeHttp

ITEM = ('<li class="cl-static-search-result" title="Sunny 2BR near the park">'
        '<a href="https://www.craigslist.org/view/d/chicago-sunny-2br-near-the-park/ehuHXXepmPCU6amEth4GEd">'
        '<div class="title">Sunny 2BR near the park</div><div class="details">'
        '<div class="price">$2,100</div><div class="location">\n  Lincoln Park\n</div></div></a></li>')


def page(*items):
    return ('<html><body class="no-js"><ol class="cl-static-search-results">'
            '<li class="cl-static-hub-links"><div>see also</div></li>' + "".join(items)
            + "</ol></body></html>")


def test_search_url():
    c = Criteria(LINCOLN_PARK, min_price=1500, max_price=2400, min_beds=2, min_baths=1,
                 home_type=HomeType.TOWNHOME, pets=Pets.DOGS)
    assert search_url(c) == ("https://chicago.craigslist.org/search/apa?sort=date&lat=41.9237&lon=-87.6505"
                             "&search_distance=2&min_price=1500&max_price=2400&min_bedrooms=2"
                             "&min_bathrooms=1&pets_dog=1&housing_type=9")


def test_search_url_repeats_housing_types_and_skips_unset_filters():
    assert search_url(Criteria(CHICAGO, home_type=HomeType.HOUSE)) == (
        "https://chicago.craigslist.org/search/apa?sort=date&lat=41.8781&lon=-87.6298&search_distance=10"
        "&housing_type=3&housing_type=4&housing_type=6")


def test_parses_a_result():
    assert parse_search_page(page(ITEM), AT) == [Listing(
        source="craigslist", listing_id="ehuHXXepmPCU6amEth4GEd",
        url="https://www.craigslist.org/view/d/chicago-sunny-2br-near-the-park/ehuHXXepmPCU6amEth4GEd",
        name="Sunny 2BR near the park", address="Lincoln Park", price_min=2100, price_max=2100,
        beds_min=None, beds_max=None, baths_min=None, baths_max=None, image_url=None, scraped_at=AT)]


def test_missing_price_is_none():
    no_price = ITEM.replace('<div class="price">$2,100</div>', "")
    assert parse_search_page(page(no_price), AT)[0].price_min is None


def test_no_results_is_an_empty_list():
    assert parse_search_page(page(), AT) == []


def test_page_without_the_results_list_is_a_layout_change():
    with pytest.raises(ScrapeError, match="layout changed"):
        parse_search_page("<html><body>hi</body></html>", AT)


def test_page_two_makes_no_request():
    http = FakeHttp(HttpResponse(200, page(ITEM)))
    assert CraigslistSource(http, now=lambda: AT).fetch(Criteria(CHICAGO), 2) == SourcePage([], None, False)
    assert http.requested == []


def test_refused_status_is_blocked():
    with pytest.raises(BlockedError, match="Craigslist is blocking"):
        CraigslistSource(FakeHttp(HttpResponse(403, "")), now=lambda: AT).fetch(Criteria(CHICAGO), 1)


def test_parses_a_saved_real_page(fixture_html):
    listings = parse_search_page(fixture_html("craigslist/chicago_apa.html"), AT)
    assert len(listings) == 5
    assert all(x.listing_id and x.url.startswith("https://www.craigslist.org/") and x.name for x in listings)

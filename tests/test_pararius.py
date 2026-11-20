import html
import json
from dataclasses import replace

import pytest

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.service import NewSearch
from apartment_hunter.sources import BlockedError, CantPlace, Filter, ScrapeError
from apartment_hunter.sources.http import HttpResponse
from apartment_hunter.sources.pararius import ParariusSource
from conftest import AT
from fakes import FakeHttp
from test_service import add_source, env  # noqa: F401  (env is a fixture)

AMSTERDAM = Area("Amsterdam, North Holland, Netherlands", "city", "NL", 52.37, 4.90, locations={
    "pararius": {"city": "amsterdam"},
})
DE_PIJP = Area("De Pijp, Amsterdam, Netherlands", "neighborhood", "NL", 52.35, 4.89, locations={
    "pararius": {"city": "amsterdam", "district": "de-pijp"},
})


def source(response: HttpResponse | str = "") -> tuple[ParariusSource, FakeHttp]:
    http = FakeHttp(response)
    return ParariusSource(http, now=lambda: AT), http


def test_results_url_is_the_rental_search_for_the_saved_place():
    assert source()[0].results_url(Criteria(AMSTERDAM)) == "https://www.pararius.com/apartments/amsterdam"
    assert source()[0].results_url(Criteria(DE_PIJP)) == (
        "https://www.pararius.com/apartments/amsterdam/district-de-pijp")


def test_filters_go_into_the_path_in_pararius_order():
    criteria = Criteria(AMSTERDAM, max_price=2500, min_beds=2, home_type=HomeType.APARTMENT_CONDO,
                        pets=Pets.CATS)
    assert source()[0].results_url(criteria) == (
        "https://www.pararius.com/apartments/amsterdam/apartment/0-2500/2-bedrooms/pets-allowed")


@pytest.mark.parametrize("min_price,max_price,segment", [
    (1000, 2500, "1000-2500"),
    (2000, None, "2000-60000"),  # Pararius's own way of writing "no maximum"
    (None, 1800, "0-1800"),
])
def test_price_ranges(min_price, max_price, segment):
    url = source()[0].results_url(Criteria(DE_PIJP, min_price=min_price, max_price=max_price))
    assert url == f"https://www.pararius.com/apartments/amsterdam/district-de-pijp/{segment}"


@pytest.mark.parametrize("home_type,segment", [
    (HomeType.APARTMENT_CONDO, "apartment"),
    (HomeType.HOUSE, "house"),
    (HomeType.TOWNHOME, "house"),  # a Dutch terraced house is a house on Pararius
])
def test_home_types(home_type, segment):
    url = source()[0].results_url(Criteria(AMSTERDAM, home_type=home_type))
    assert url == f"https://www.pararius.com/apartments/amsterdam/{segment}"


def test_a_studio_minimum_needs_no_bedroom_filter():
    assert source()[0].results_url(Criteria(AMSTERDAM, min_beds=0)) == (
        "https://www.pararius.com/apartments/amsterdam")


def test_pararius_cannot_filter_by_baths():
    assert ParariusSource.filters == frozenset({Filter.PRICE, Filter.BEDS, Filter.HOME_TYPE, Filter.PETS})
    assert source()[0].results_url(Criteria(AMSTERDAM, min_baths=2)) == (
        "https://www.pararius.com/apartments/amsterdam")


def test_parses_a_saved_real_page_leaving_out_the_highlighted_advert(fixture_html):
    pararius, _ = source(fixture_html("pararius/amsterdam_rentals.html"))
    page = pararius.fetch(Criteria(AMSTERDAM), 1)
    assert (len(page.listings), page.total, page.has_more) == (30, 902, True)
    assert "534613e4-eca2-5326-85c9-5568a03b38f6" not in [listing.listing_id for listing in page.listings]
    assert page.listings[0] == Listing(
        source="pararius", listing_id="6eb6be18-562b-5929-8f18-fe1ac1e6a6b5",
        url="https://www.pararius.com/apartment-for-rent/amsterdam/6eb6be18/van-spilbergenstraat",
        name="Flat Van Spilbergenstraat", address="1057 RK Amsterdam (Hoofdweg e.o.)",
        price_min=2350, price_max=2350, beds_min=2, beds_max=2, baths_min=None, baths_max=None,  # 3 rooms
        image_url="https://casco-media-prod.global.ssl.fastly.net/listings/api/media/"
                  "6eb6be18-562b-5929-8f18-fe1ac1e6a6b5/49b2ee67-4152-5070-a564-a178c099d082?original_uri="
                  "https%3A%2F%2Fpararius-office-prod.global.ssl.fastly.net%2F10788%2Ffiles%2Fphotos%2F"
                  "originals%2Fc0214741.jpg&sig=42498b529c156573e3c7fc1f0bfa2601c175b221301c8f5287e53538063069f6"
                  "&width=600&auto=webp",
        scraped_at=AT)


def test_lazy_loaded_photos_are_read_from_their_template(fixture_html):
    pararius, _ = source(fixture_html("pararius/amsterdam_rentals.html"))
    last = pararius.fetch(Criteria(AMSTERDAM), 1).listings[-1]
    assert last.listing_id == "0b5f351b-d971-593d-a742-c64092d1bb5c"
    assert last.image_url is not None and last.image_url.startswith(
        "https://casco-media-prod.global.ssl.fastly.net/listings/api/media/0b5f351b-")


def pararius_page(cards: list[str], count: int, filters: dict, page: int = 1, next_page: bool = False) -> str:
    """A results page shaped like Pararius's: the search it ran on <body>, the count, cards, pagination."""
    searched = html.escape(json.dumps({"filters": {"type": "for_rent", **filters}}))
    view = html.escape(json.dumps({"view_options": {"view": "list", "page": page}}))
    items = "".join(f'<li class="search-list__item search-list__item--listing">{card}</li>' for card in cards)
    more = '<a class="pagination__link pagination__link--next" href="/next">Next</a>' if next_page else ""
    return (f'<html><body data-search-page-view-options="{view}" data-search-page-filters="{searched}">'
            f'<span class="search-list-header__count">{count}</span>'
            f'<ul class="search-list__list">{items}</ul>'
            f'<wc-pagination class="pagination">{more}</wc-pagination></body></html>')


def card(id_: str = "1", price: str = "€1,250 pcm", rooms: str | None = "2 rooms", photo: bool = True) -> str:
    img = '<img class="picture__image" src="https://img.example/1.jpg"/>' if photo else ""
    features = (f'<li class="illustrated-features__item illustrated-features__item--number-of-rooms">'
                f'<span class="illustrated-features__item-icon"></span> {rooms}</li>') if rooms else ""
    return (f'<wc-listing-search-item data-id="{id_}"><section class="listing-search-item">{img}'
            f'<h3 class="listing-search-item__title"><a class="listing-search-item__link '
            f'listing-search-item__link--title" href="/apartment-for-rent/amsterdam/{id_}/street">'
            f' Flat Street </a></h3>'
            f'<div class="listing-search-item__sub-title"> 1072 AB Amsterdam (De Pijp) </div>'
            f'<span class="listing-search-item__price-main"> {price} </span>'
            f'<ul class="illustrated-features">{features}</ul></section></wc-listing-search-item>')


PIJP_SEARCHED = {"city": "amsterdam", "district": "de-pijp"}


def test_later_pages_are_read_from_their_page_path():
    pararius, http = source(pararius_page([card()], 75, PIJP_SEARCHED, page=3))
    page = pararius.fetch(Criteria(DE_PIJP, min_beds=2), 3)
    assert http.requested == ["https://www.pararius.com/apartments/amsterdam/district-de-pijp/2-bedrooms/page-3"]
    assert (len(page.listings), page.total) == (1, 75)


def test_a_page_past_the_end_is_empty():
    """Pararius answers a page past the last one with page 1, which would repeat its Listings."""
    page = source(pararius_page([card()], 1, PIJP_SEARCHED, page=1))[0].fetch(Criteria(DE_PIJP), 2)
    assert (page.listings, page.has_more) == ([], False)


def test_an_empty_district_widened_to_the_whole_city_is_zero_matches():
    """With no matches in a district, Pararius drops the district and shows the whole city's matches."""
    widened = pararius_page([card()], 4, {"city": "amsterdam"})
    page = source(widened)[0].fetch(Criteria(DE_PIJP), 1)
    assert (page.listings, page.total, page.has_more) == ([], 0, False)


def test_zero_matches_is_an_empty_page_not_an_error():
    page = source(pararius_page([], 0, PIJP_SEARCHED))[0].fetch(Criteria(DE_PIJP), 1)
    assert (page.listings, page.total, page.has_more) == ([], 0, False)


def test_a_one_room_studio_without_photo_and_a_price_on_request():
    pararius, _ = source(pararius_page([card("1", rooms="1 room", photo=False),
                                        card("2", price="Price on request", rooms=None)], 2, PIJP_SEARCHED))
    studio, unpriced = pararius.fetch(Criteria(DE_PIJP), 1).listings
    assert (studio.price_min, studio.beds_min, studio.beds_max, studio.image_url) == (1250, 0, 0, None)
    assert (unpriced.price_min, unpriced.price_max, unpriced.beds_min) == (None, None, None)


@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError, match="Pararius is blocking"):
        source(HttpResponse(status, ""))[0].fetch(Criteria(AMSTERDAM), 1)


def test_other_http_errors_are_plain_scrape_errors():
    with pytest.raises(ScrapeError, match=r"Pararius answered with an error \(HTTP 500\)\.") as e:
        source(HttpResponse(500, ""))[0].fetch(Criteria(AMSTERDAM), 1)
    assert not isinstance(e.value, BlockedError)


def test_a_captcha_page_is_blocked():
    with pytest.raises(BlockedError):
        source("<html><title>Just a moment...</title><div class='captcha'></div></html>")[0].fetch(
            Criteria(AMSTERDAM), 1)


def test_page_without_a_results_list_is_a_layout_change():
    with pytest.raises(ScrapeError, match="Pararius's page layout changed"):
        source("<html><body>Page not found</body></html>")[0].fetch(Criteria(AMSTERDAM), 1)


def test_garbled_search_data_is_a_layout_change():
    garbled = ('<html><body data-search-page-filters="{not json">'
               '<span class="search-list-header__count">3</span></body></html>')
    with pytest.raises(ScrapeError, match="Pararius's page layout changed"):
        source(garbled)[0].fetch(Criteria(AMSTERDAM), 1)


def test_place_finds_the_district_in_the_areas_city_through_pararius_suggestions(fixture_html):
    lookups = FakeHttp(fixture_html("pararius/suggest_de_pijp.json"))
    pararius = ParariusSource(FakeHttp(), now=lambda: AT, lookup_http=lookups)
    # The suggestions also hold a De Pijp in Beverwijk; the Area says Amsterdam.
    assert pararius.place(replace(DE_PIJP, locations={})) == {"city": "amsterdam", "district": "de-pijp"}
    assert lookups.requested == ["https://www.pararius.com/api/suggest?q=De%20Pijp"
                                 "&types[]=city&types[]=city_area&types[]=neighbourhood&types[]=district"]


def test_place_finds_a_city_by_its_english_name(fixture_html):
    pararius, _ = source(fixture_html("pararius/suggest_the_hague.json"))
    the_hague = Area("The Hague, South Holland, Netherlands", "city", "NL", 52.08, 4.31)
    assert pararius.place(the_hague) == {"city": "den-haag"}


def test_place_finds_a_city_area_named_after_its_city(fixture_html):
    pararius, _ = source(fixture_html("pararius/suggest_the_hague.json"))
    centrum = Area("Centrum, The Hague, Netherlands", "neighborhood", "NL", 52.08, 4.31)
    assert pararius.place(centrum) == {"city": "den-haag", "city_area": "centrum"}


def test_place_refuses_a_place_pararius_does_not_know(fixture_html):
    pararius, _ = source(fixture_html("pararius/suggest_de_pijp.json"))
    with pytest.raises(CantPlace) as refused:
        pararius.place(Area("Jordaan, Amsterdam, Netherlands", "neighborhood", "NL", 52.37, 4.88))
    assert str(refused.value) == "Pararius doesn't know Jordaan, Amsterdam, Netherlands."


def test_place_refuses_a_postcode_without_asking_pararius():
    pararius, http = source()
    with pytest.raises(CantPlace) as refused:
        pararius.place(Area("1012 AG, Amsterdam, Netherlands", "zipcode", "NL", 52.37, 4.89))
    assert str(refused.value) == ("Pararius can't search by postcode. "
                                  "Try the neighbourhood or city instead.")
    assert http.requested == []


def test_place_raises_a_scrape_error_when_the_suggestions_are_down():
    with pytest.raises(ScrapeError, match="Pararius's place lookup isn't answering"):
        source(HttpResponse(500, ""))[0].place(replace(DE_PIJP, locations={}))


class DutchAreas:
    """Resolves only De Pijp (not yet placed on any Source)."""

    def suggest(self, text: str) -> list[str]:
        return []

    def resolve(self, text: str) -> Area:
        return replace(DE_PIJP, locations={})


def test_a_dutch_search_reads_pararius_in_euros_and_notes_the_baths_it_cannot_filter(env):  # noqa: F811
    pararius = add_source(env, "pararius")
    pararius.filters = ParariusSource.filters
    env.service.areas = DutchAreas()
    search, pending = env.service.create(1, 10, 99, NewSearch(DE_PIJP.display, max_price=1800, min_baths=1),
                                         can_post=True)
    env.scheduler.run_pending()
    assert search.sources == ("pararius",)
    assert search.name == "De Pijp, Amsterdam, Netherlands · 1+ ba · up to €1,800"
    assert pararius.locations == [{"at": DE_PIJP.display}]
    assert pending.notes == ("Pararius can't filter by baths, so you'll see all of its matches.",)


def test_place_treats_a_garbled_suggestions_answer_as_no_match():
    with pytest.raises(CantPlace):
        source("<html>not json")[0].place(replace(DE_PIJP, locations={}))

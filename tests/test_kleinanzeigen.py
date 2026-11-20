from dataclasses import replace

import pytest

from apartment_hunter.criteria import Area, Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import ALL_FILTERS, BlockedError, CantPlace, Filter, ScrapeError
from apartment_hunter.sources.http import HttpResponse
from apartment_hunter.sources.kleinanzeigen import KleinanzeigenSource
from conftest import AT
from fakes import FakeHttp

BERLIN = Area("Berlin, Berlin, Germany", "city", "DE", 52.5170, 13.3889, locations={
    "kleinanzeigen": {"location_id": 3331, "slug": "berlin"},
})
SITE = "https://www.kleinanzeigen.de"


def source(response: HttpResponse | str = "") -> tuple[KleinanzeigenSource, FakeHttp]:
    http = FakeHttp(response)
    return KleinanzeigenSource(http, now=lambda: AT), http


# The search URL


def test_results_url_is_the_newest_first_flat_offers_for_the_saved_place():
    assert source()[0].results_url(Criteria(BERLIN)) == (
        f"{SITE}/s-wohnung-mieten/berlin/sortierung:neuste/anzeige:angebote/c203l3331")


def test_price_and_bedrooms_as_rooms_go_into_the_search_the_way_the_site_takes_them():
    # The URL Kleinanzeigen's own search form redirected to for max €1,500 and 3+ rooms (2+ bedrooms).
    url = source()[0].results_url(Criteria(BERLIN, max_price=1500, min_beds=2))
    assert url == (f"{SITE}/s-wohnung-mieten/berlin/sortierung:neuste/anzeige:angebote/preis::1500/"
                   "c203l3331+wohnung_mieten.zimmer_d:3%2C")


def test_later_pages_and_pets_and_a_minimum_price():
    kleinanzeigen, http = source(HttpResponse(500, ""))
    with pytest.raises(Exception):  # noqa: B017 - only the URL asked for matters here
        kleinanzeigen.fetch(Criteria(BERLIN, min_price=800, max_price=1500, min_beds=2, pets=Pets.CATS), 2)
    assert http.requested == [
        f"{SITE}/s-wohnung-mieten/berlin/sortierung:neuste/anzeige:angebote/preis:800:1500/seite:2/"
        "c203l3331+wohnung_mieten.zimmer_d:3%2C+options:wohnung_mieten.pets_allowed_b"]


FLATS = (f"{SITE}/s-wohnung-mieten/berlin/sortierung:neuste/anzeige:angebote/"
         "c203l3331+wohnung_mieten.zimmer_d:4%2C+options:wohnung_mieten.pets_allowed_b")
HOUSES = (f"{SITE}/s-haus-mieten/berlin/sortierung:neuste/anzeige:angebote/"
          "c205l3331+haus_mieten.zimmer_d:4%2C+options:haus_mieten.pets_allowed_b")


@pytest.mark.parametrize(("home_type", "url"), [
    (HomeType.APARTMENT_CONDO, FLATS),
    (HomeType.HOUSE, HOUSES),
    (HomeType.TOWNHOME, HOUSES),  # Kleinanzeigen has no terraced-only search: every house
])
def test_houses_are_their_own_category(home_type, url):
    criteria = Criteria(BERLIN, min_beds=3, home_type=home_type, pets=Pets.SMALL_DOGS)
    assert source()[0].results_url(criteria) == url


def test_a_studio_minimum_filters_nothing_and_half_bedrooms_round_down():
    assert source()[0].results_url(Criteria(BERLIN, min_beds=0)).endswith("/c203l3331")
    assert source()[0].results_url(Criteria(BERLIN, min_beds=1.5)).endswith(
        "/c203l3331+wohnung_mieten.zimmer_d:2%2C")


# Reading a results page


def test_parses_a_saved_real_page_leaving_out_the_top_ads(fixture_html):
    page = source(fixture_html("kleinanzeigen/berlin_3_rooms_newest.html"))[0].fetch(Criteria(BERLIN), 1)
    assert (len(page.listings), page.total, page.has_more) == (25, 1732, True)
    assert page.listings[0] == Listing(
        source="kleinanzeigen", listing_id="3334482409",
        url=f"{SITE}/s-anzeige/wohnungsswap-3-zimmer-110-m-weichselstrasse-neukoelln-berlin/3334482409-203-3397",
        name="Wohnungsswap - 3 Zimmer, 110 m² - Weichselstraße, Neukölln, Berlin", address="12045 Neukölln",
        price_min=700, price_max=700, beds_min=2.0, beds_max=2.0, baths_min=None, baths_max=None,
        image_url="https://img.kleinanzeigen.de/api/v1/prod-ads/images/ad/adaca409-9cc1-4174-96a1-ee02a0fdf8a6"
                  "?rule=$_2.AUTO",
        scraped_at=AT,
    )
    ids = [listing.listing_id for listing in page.listings]
    assert "3568114349" not in ids and "3574922138" not in ids  # the two "TOP" ads pinned above the results


def test_rooms_become_bedrooms_and_thousands_use_dots(fixture_html):
    page = source(fixture_html("kleinanzeigen/berlin_3_rooms_newest.html"))[0].fetch(Criteria(BERLIN), 1)
    by_id = {listing.listing_id: listing for listing in page.listings}
    # "89 m² · 3,5 Zi." and "1.200 €"
    assert (by_id["3491069656"].beds_min, by_id["3491069656"].price_min) == (2.5, 1200)
    assert (by_id["3577408732"].price_min, by_id["3577408732"].image_url) == (1294, None)  # no photo


def test_the_last_page_has_no_more_and_a_reduced_price_reads_the_new_one(fixture_html):
    page = source(fixture_html("kleinanzeigen/10999_newest.html"))[0].fetch(Criteria(BERLIN), 1)
    assert (len(page.listings), page.total, page.has_more) == (16, 16, False)
    by_id = {listing.listing_id: listing for listing in page.listings}
    assert by_id["3564918649"].price_min == 949  # shown as "949 €" next to a struck-through "950 €"
    assert by_id["3560999968"].price_min is None  # "VB" alone: open to offers, no figure


def test_no_matches_is_an_empty_page_not_the_ads_from_other_places_shown_instead(fixture_html):
    page = source(fixture_html("kleinanzeigen/10999_no_matches.html"))[0].fetch(Criteria(BERLIN), 1)
    assert (page.listings, page.total, page.has_more) == ([], 0, False)


def test_kleinanzeigen_cannot_filter_by_baths():
    assert KleinanzeigenSource(FakeHttp()).filters == ALL_FILTERS - {Filter.BATHS}


def test_kleinanzeigen_says_which_homes_it_searches_when_the_home_type_does_not_match_exactly():
    assert KleinanzeigenSource(FakeHttp()).home_type_notes == {
        HomeType.ANY: "Kleinanzeigen searches flats unless you pick House.",
        HomeType.TOWNHOME: "Kleinanzeigen can't tell townhomes from houses, so you'll see all houses.",
    }


# Errors


@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError, match="Kleinanzeigen is blocking"):
        source(HttpResponse(status, ""))[0].fetch(Criteria(BERLIN), 1)


def test_other_http_errors_are_plain_scrape_errors():
    with pytest.raises(ScrapeError, match=r"Kleinanzeigen answered with an error \(HTTP 500\)\.") as e:
        source(HttpResponse(500, ""))[0].fetch(Criteria(BERLIN), 1)
    assert not isinstance(e.value, BlockedError)


def test_a_bot_check_page_is_blocked():
    with pytest.raises(BlockedError):
        source("<html><title>Just a moment...</title></html>")[0].fetch(Criteria(BERLIN), 1)


@pytest.mark.parametrize("html", [
    "<html><body>Seite nicht gefunden</body></html>",
    '<span id="srp-breadcrumb-summary">1 - 25 von 1.732 Ergebnissen in Berlin</span>'
    '<ul id="srchrslt-adtable"></ul>',
    '<span id="srp-breadcrumb-summary">Mietwohnungen in Berlin</span>',  # a heading it can't read
])
def test_a_page_without_readable_results_is_a_layout_change(html):
    with pytest.raises(ScrapeError, match="Kleinanzeigen's page layout changed"):
        source(html)[0].fetch(Criteria(BERLIN), 1)


# Placing an Area


def placing(answer: HttpResponse | str) -> tuple[KleinanzeigenSource, FakeHttp]:
    lookups = FakeHttp(answer)
    return KleinanzeigenSource(FakeHttp(HttpResponse(500, "")), now=lambda: AT, lookup_http=lookups), lookups


def test_place_finds_a_city_through_the_sites_place_suggestions(fixture_html):
    kleinanzeigen, lookups = placing(fixture_html("kleinanzeigen/suggest_berlin.json"))
    assert kleinanzeigen.place(replace(BERLIN, locations={})) == {"location_id": 3331, "slug": "berlin"}
    assert lookups.requested == [f"{SITE}/s-ort-empfehlungen.json?query=Berlin"]


def test_place_finds_a_district_named_like_the_area(fixture_html):
    kleinanzeigen, _ = placing(fixture_html("kleinanzeigen/suggest_kreuzberg.json"))
    kreuzberg = Area("Kreuzberg, Berlin, Germany", "neighborhood", "DE", 52.4986, 13.4030)
    assert kleinanzeigen.place(kreuzberg) == {"location_id": 3375, "slug": "kreuzberg"}


def test_place_prefers_the_match_in_the_areas_city():
    kleinanzeigen, _ = placing('{"_0": "Deutschland", "_1": "Mitte - Hamburg", "_3518": "Mitte - Berlin"}')
    mitte = Area("Mitte, Berlin, Germany", "neighborhood", "DE", 52.52, 13.40)
    assert kleinanzeigen.place(mitte) == {"location_id": 3518, "slug": "mitte"}


def test_place_spells_neighbourhoods_with_umlauts_the_way_the_site_does():
    kleinanzeigen, _ = placing('{"_0": "Deutschland", "_3386": "Neukölln - Berlin"}')
    neukoelln = Area("Neukölln, Berlin, Germany", "neighborhood", "DE", 52.48, 13.43)
    assert kleinanzeigen.place(neukoelln) == {"location_id": 3386, "slug": "neukoelln"}


def test_place_finds_a_postcode(fixture_html):
    kleinanzeigen, lookups = placing(fixture_html("kleinanzeigen/suggest_10999.json"))
    postcode = Area("10999, Berlin, Germany", "zipcode", "DE", 52.497, 13.42)
    assert kleinanzeigen.place(postcode) == {"location_id": 9629, "slug": "10999"}
    assert lookups.requested == [f"{SITE}/s-ort-empfehlungen.json?query=10999"]


def test_place_asks_for_a_city_by_its_german_name():
    # The place lookup spells names in English; Kleinanzeigen only knows "München" (it answers
    # "Munich" with lookalikes such as "Much - Nordrhein-Westfalen").
    kleinanzeigen, lookups = placing('{"_0": "Deutschland", "_6411": "München - Bayern"}')
    munich = Area("Munich, Bavaria, Germany", "city", "DE", 48.137, 11.575)
    assert kleinanzeigen.place(munich) == {"location_id": 6411, "slug": "muenchen"}
    assert lookups.requested == [f"{SITE}/s-ort-empfehlungen.json?query=M%C3%BCnchen"]


def test_place_refuses_a_place_kleinanzeigen_does_not_know(fixture_html):
    kleinanzeigen, _ = placing(fixture_html("kleinanzeigen/suggest_munich.json"))
    nowhere = Area("Munchow, Bavaria, Germany", "city", "DE", 48.0, 11.0)
    with pytest.raises(CantPlace) as refused:
        kleinanzeigen.place(nowhere)
    assert str(refused.value) == "Kleinanzeigen doesn't know Munchow, Bavaria, Germany."


@pytest.mark.parametrize("answer", [HttpResponse(500, ""), HttpResponse(403, "")])
def test_place_raises_a_scrape_error_when_the_lookup_does_not_answer(answer):
    with pytest.raises(ScrapeError, match="Kleinanzeigen's place lookup isn't answering"):
        placing(answer)[0].place(replace(BERLIN, locations={}))


def test_place_treats_a_garbled_answer_as_no_match():
    with pytest.raises(CantPlace):
        placing("<html>not json</html>")[0].place(replace(BERLIN, locations={}))

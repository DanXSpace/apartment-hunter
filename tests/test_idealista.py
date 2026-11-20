from dataclasses import replace
from urllib.parse import parse_qsl, urlsplit

import pytest

from apartment_hunter.countries import COUNTRIES, format_price
from apartment_hunter.criteria import Area, Criteria, HomeType, Pets
from apartment_hunter.models import Listing
from apartment_hunter.sources import SOURCE_NAMES, BlockedError, Filter, ScrapeError
from apartment_hunter.sources.http import HttpResponse
from apartment_hunter.sources.idealista import ITALY, PORTUGAL, IdealistaConfig, IdealistaSource
from conftest import AT
from fakes import FakeHttp

# The drawn areas the saved pages were fetched with (each a rectangle around a neighbourhood).
TRASTEVERE_SHAPE = "wru~F_r`kA?_|Bv|A??~{Bw|A?"
ALCANTARA_SHAPE = "_}gkFf}`w@?_|B~iA??~{B_jA?"
TRASTEVERE = Area("Trastevere, Rome, Italy", "neighborhood", "IT", 41.8876, 12.4700,
                  bbox=(41.880, 12.460, 41.895, 12.480),
                  locations={"idealista_it": {"shape": TRASTEVERE_SHAPE}})
ALCANTARA = Area("Alcântara, Lisbon, Portugal", "neighborhood", "PT", 38.706, -9.175,
                 bbox=(38.700, -9.185, 38.712, -9.165),
                 locations={"idealista_pt": {"shape": ALCANTARA_SHAPE}})


def source(config: IdealistaConfig, response: HttpResponse | str = "") -> tuple[IdealistaSource, FakeHttp]:
    http = FakeHttp(response)
    return IdealistaSource(config, http, now=lambda: AT), http


def path_and_query(url: str) -> tuple[str, list[tuple[str, str]]]:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}", parse_qsl(parts.query)


def card(id_: str = "1", title: str = "Bilocale in Via Roma, Trastevere, Roma", price: str = "1.200 €/mese",
         details: tuple[str, ...] = ("2 locali", "50 m²"), photo: bool = True, path: str = "immobile") -> str:
    img = '<div class="item-gallery"><img src="https://img4.idealista.it/blur/1.jpg"/></div>' if photo else ""
    spans = "".join(f'<span class="item-detail">{d}</span>' for d in details)
    return (f'<article class="item extended-item" data-element-id="{id_}">'
            f'<picture class="item-multimedia">{img}</picture><div class="item-info-container">'
            f'<a href="/{path}/{id_}/" class="item-link" title="{title}">{title}</a><div class="price-row">'
            f'<span class="item-price" data-price="{price}"></span></div>'
            f'<div class="item-detail-char">{spans}</div></div></article>')


def results_page(cards: list[str], heading: str, next_page: bool = False) -> str:
    pagination = '<div class="pagination"><ul><li class="next"><a href="lista-2">Next</a></li></ul></div>'
    return (f'<html><h1 id="h1-container"><span id="h1-container__text">{heading}</span></h1>'
            f'<section>{"".join(cards)}</section>{pagination if next_page else ""}</html>')


# The search URL


def test_results_url_is_the_newest_first_rental_search_in_the_drawn_area():
    assert path_and_query(source(ITALY)[0].results_url(Criteria(TRASTEVERE))) == (
        "https://www.idealista.it/aree/affitto-case/",
        [("shape", f"(({TRASTEVERE_SHAPE}))"), ("ordine", "pubblicazione-desc")])
    assert path_and_query(source(PORTUGAL)[0].results_url(Criteria(ALCANTARA))) == (
        "https://www.idealista.pt/areas/arrendar-casas/",
        [("shape", f"(({ALCANTARA_SHAPE}))"), ("ordem", "atualizado-desc")])


def test_the_drawn_area_goes_into_the_url_the_way_idealista_took_it():
    url = source(ITALY)[0].results_url(Criteria(TRASTEVERE, max_price=2000, min_beds=1))
    assert url == ("https://www.idealista.it/aree/affitto-case/con-prezzo_2000,bilocali-2,trilocali-3,"
                   "quadrilocali-4,5-locali-o-piu/?shape=%28%28wru~F_r%60kA%3F_%7CBv%7CA%3F%3F~%7BBw%7CA%3F%29%29"
                   "&ordine=pubblicazione-desc")


def test_italian_filters_and_later_pages():
    italy, http = source(ITALY, results_page([], "0"))
    criteria = Criteria(TRASTEVERE, min_price=1000, max_price=3000, min_beds=2, min_baths=2.5,
                        home_type=HomeType.APARTMENT_CONDO, pets=Pets.SMALL_DOGS)
    italy.fetch(criteria, 3)
    assert path_and_query(http.requested[0])[0] == (
        "https://www.idealista.it/aree/affitto-case/con-prezzo_3000,prezzo-min_1000,appartamenti,"
        "trilocali-3,quadrilocali-4,5-locali-o-piu,bagno-2,bagno-3,animali/lista-3")


def test_portuguese_filters_and_later_pages():
    portugal, http = source(PORTUGAL, results_page([], "0"))
    criteria = Criteria(ALCANTARA, min_price=1000, max_price=3000, min_beds=2, min_baths=3,
                        home_type=HomeType.HOUSE)
    portugal.fetch(criteria, 2)
    assert path_and_query(http.requested[0])[0] == (
        "https://www.idealista.pt/areas/arrendar-casas/com-preco-max_3000,preco-min_1000,moradias,"
        "t2,t3,t4-t5,banho-3/pagina-2")


@pytest.mark.parametrize("config,area,beds,rooms", [
    (ITALY, TRASTEVERE, 0, ""),  # a studio minimum filters nothing out
    (ITALY, TRASTEVERE, 1, "con-bilocali-2,trilocali-3,quadrilocali-4,5-locali-o-piu/"),
    (ITALY, TRASTEVERE, 3, "con-quadrilocali-4,5-locali-o-piu/"),
    (ITALY, TRASTEVERE, 4, "con-5-locali-o-piu/"),
    (ITALY, TRASTEVERE, 6, "con-5-locali-o-piu/"),
    (ITALY, TRASTEVERE, 1.5, "con-trilocali-3,quadrilocali-4,5-locali-o-piu/"),
    (PORTUGAL, ALCANTARA, 0, ""),
    (PORTUGAL, ALCANTARA, 1, "com-t1,t2,t3,t4-t5/"),  # T1 is one bedroom
    (PORTUGAL, ALCANTARA, 5, "com-t4-t5/"),
])
def test_bedrooms_become_rooms_in_italy_and_t_numbers_in_portugal(config, area, beds, rooms):
    url = source(config)[0].results_url(Criteria(area, min_beds=beds))
    assert urlsplit(url).path.endswith("/" + rooms)


@pytest.mark.parametrize("baths,segment", [(1, ""), (2, "con-bagno-2,bagno-3/"), (4, "con-bagno-3/")])
def test_bathroom_minimums(baths, segment):
    url = source(ITALY)[0].results_url(Criteria(TRASTEVERE, min_baths=baths))
    assert urlsplit(url).path == "/aree/affitto-case/" + segment


@pytest.mark.parametrize("config,area,home_type,segment", [
    (ITALY, TRASTEVERE, HomeType.HOUSE, "ville"),
    (ITALY, TRASTEVERE, HomeType.TOWNHOME, "villette-a-schiera"),
    (PORTUGAL, ALCANTARA, HomeType.APARTMENT_CONDO, "apartamentos"),
    (PORTUGAL, ALCANTARA, HomeType.TOWNHOME, "moradias-em-banda"),
])
def test_home_types(config, area, home_type, segment):
    url = source(config)[0].results_url(Criteria(area, home_type=home_type))
    assert urlsplit(url).path.endswith(f"-{segment}/")


def test_idealista_portugal_cannot_filter_by_pets_but_italy_can():
    assert ITALY.filters == frozenset(Filter)
    assert PORTUGAL.filters == frozenset({Filter.PRICE, Filter.BEDS, Filter.BATHS, Filter.HOME_TYPE})
    portugal = source(PORTUGAL)[0]
    assert portugal.filters == PORTUGAL.filters
    with_pets = portugal.results_url(Criteria(ALCANTARA, pets=Pets.CATS))
    assert with_pets == portugal.results_url(Criteria(ALCANTARA))


# Reading the saved pages


def test_parses_a_saved_real_italian_page(fixture_html):
    italy, _ = source(ITALY, fixture_html("idealista_it/trastevere_rentals.html"))
    page = italy.fetch(Criteria(TRASTEVERE), 1)
    assert (len(page.listings), page.total, page.has_more) == (30, 58, True)
    assert page.listings[0] == Listing(
        source="idealista_it", listing_id="37548364", url="https://www.idealista.it/immobile/37548364/",
        name="Trilocale", address="Via Giacomo Venezian, 15, Trastevere, Roma",
        price_min=1600, price_max=1600, beds_min=2, beds_max=2, baths_min=None, baths_max=None,
        image_url="https://img4.idealista.it/blur/480_360_mq/0/id.pro.it.image.master/df/6f/5b/860146999.jpg",
        scraped_at=AT)
    # On a newest-first page a highlighted ("in evidenza") card sits in its own place, so it's kept.
    assert "37506304" in [listing.listing_id for listing in page.listings]


def test_parses_a_saved_real_portuguese_page(fixture_html):
    portugal, _ = source(PORTUGAL, fixture_html("idealista_pt/alcantara_rentals.html"))
    page = portugal.fetch(Criteria(ALCANTARA), 1)
    assert (len(page.listings), page.total, page.has_more) == (30, 149, True)
    assert page.listings[0] == Listing(
        source="idealista_pt", listing_id="34553748", url="https://www.idealista.pt/imovel/34553748/",
        name="Apartamento T2",
        address="Rua Rodrigues de Faria, 57, Largo do Calvário - Lx Factory, Alcântara",
        price_min=1550, price_max=1550, beds_min=2, beds_max=2, baths_min=None, baths_max=None,
        image_url="https://img4.idealista.pt/blur/480_360_mq/0/id.pro.pt.image.master/9e/ba/b9/298661698.jpg",
        scraped_at=AT)
    listings = {listing.listing_id: listing for listing in page.listings}
    # "Garagem incluída" comes before the T number on this card.
    assert (listings["35369412"].beds_min, listings["35369412"].price_min) == (1, 1650)


@pytest.mark.parametrize("config,area,fixture", [(ITALY, TRASTEVERE, "idealista_it/no_matches.html"),
                                               (PORTUGAL, ALCANTARA, "idealista_pt/no_matches.html")])
def test_zero_matches_is_an_empty_page_although_idealista_answers_404(config, area, fixture, fixture_html):
    page = source(config, HttpResponse(404, fixture_html(fixture)))[0].fetch(Criteria(area, max_price=200), 1)
    assert (page.listings, page.total, page.has_more) == ([], 0, False)


def test_a_studio_a_card_without_photo_and_a_card_without_price():
    italy, _ = source(ITALY, results_page([
        card("1", "Monolocale in Via Roma, Roma", details=("1 locale", "25 m²"), photo=False),
        card("2", "Appartamento a Roma", price="su richiesta", details=("90 m²",)),
    ], "2 case in affitto"))
    studio, unpriced = italy.fetch(Criteria(TRASTEVERE), 1).listings
    assert (studio.name, studio.address, studio.beds_min, studio.image_url) == (
        "Monolocale", "Via Roma, Roma", 0, None)
    assert (unpriced.name, unpriced.address, unpriced.price_min, unpriced.beds_min) == (
        "Appartamento a Roma", "", None, None)


def test_a_portuguese_t0_is_a_studio():
    portugal, _ = source(PORTUGAL, results_page([
        card("7", "Estúdio em Rua da Junqueira, Alcântara", price="900 €/mês", details=("T0 ", "30 m²"),
             path="imovel"),
    ], "1 casa"))
    (studio,) = portugal.fetch(Criteria(ALCANTARA), 1).listings
    assert (studio.url, studio.name, studio.address, studio.price_min, studio.beds_min) == (
        "https://www.idealista.pt/imovel/7/", "Estúdio", "Rua da Junqueira, Alcântara", 900, 0)


# Errors


@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError, match="Idealista is blocking"):
        source(ITALY, HttpResponse(status, ""))[0].fetch(Criteria(TRASTEVERE), 1)


def test_other_http_errors_are_plain_scrape_errors():
    for status in (500, 404):
        with pytest.raises(ScrapeError, match=rf"Idealista answered with an error \(HTTP {status}\)\.") as e:
            source(PORTUGAL, HttpResponse(status, "<html>Not found</html>"))[0].fetch(Criteria(ALCANTARA), 1)
        assert not isinstance(e.value, BlockedError)


def test_a_datadome_challenge_is_blocked():
    challenge = '<html><script src="https://ct.captcha-delivery.com/c.js"></script></html>'
    with pytest.raises(BlockedError):
        source(ITALY, challenge)[0].fetch(Criteria(TRASTEVERE), 1)


def test_page_without_results_heading_is_a_layout_change():
    with pytest.raises(ScrapeError, match="Idealista's page layout changed"):
        source(ITALY, "<html><body>Benvenuto</body></html>")[0].fetch(Criteria(TRASTEVERE), 1)


# Placing


def test_place_draws_the_areas_bounding_box_without_a_request():
    italy, http = source(ITALY)
    assert italy.place(replace(TRASTEVERE, locations={})) == {"shape": TRASTEVERE_SHAPE}
    assert source(PORTUGAL)[0].place(replace(ALCANTARA, locations={})) == {"shape": ALCANTARA_SHAPE}
    assert http.requested == []


def test_place_draws_a_kilometre_around_a_neighbourhood_without_a_bounding_box():
    portugal = source(PORTUGAL)[0]
    around = Area("Alcântara, Lisbon, Portugal", "neighborhood", "PT", 38.706, -9.175)
    # 1 km is 0.00898° of latitude, and 0.01151° of longitude at this latitude.
    drawn = replace(around, bbox=(38.69702, -9.18651, 38.71498, -9.16349))
    assert portugal.place(around) == portugal.place(drawn)


def test_italy_and_portugal_read_their_idealista_in_euros():
    assert (COUNTRIES["IT"].name, COUNTRIES["IT"].sources) == ("Italy", ("idealista_it",))
    assert (COUNTRIES["PT"].name, COUNTRIES["PT"].sources) == ("Portugal", ("idealista_pt",))
    assert (format_price(1500, "IT"), format_price(1500, "PT")) == ("€1,500", "€1,500")
    assert (SOURCE_NAMES["idealista_it"], SOURCE_NAMES["idealista_pt"]) == (
        "Idealista Italy", "Idealista Portugal")

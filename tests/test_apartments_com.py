import pytest

from apartment_hunter.fetchers import FetchError, FetchResult
from apartment_hunter.scrapers import BlockedError, ScrapeError
from apartment_hunter.scrapers.apartments_com import (
    ApartmentsComScraper,
    is_blocked,
    page_url,
    parse_page,
    parse_prices,
    parse_range,
)

ACCESS_DENIED = "<html><head><title>Access Denied</title></head><body>nope</body></html>"


# --- pure helpers ----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("2 bd", (2, 2)),
    ("2 - 3 bd", (2, 3)),
    ("1 - 1.5 ba", (1, 1.5)),
    ("Studio", (0, 0)),
    ("Studio - 2 bd", (0, 2)),
    ("", (None, None)),
    (None, (None, None)),
])
def test_parse_range(text, expected):
    assert parse_range(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("$2,375", (2375, 2375)),
    ("$1,515 - $2,410", (1515, 2410)),
    ("Total Monthly Price: $1,454 - $2,216", (1454, 2216)),
    ("$1,515+", (1515, 1515)),
    ("Call for Rent", (None, None)),
])
def test_parse_prices(text, expected):
    assert parse_prices(text) == expected


@pytest.mark.parametrize("page,expected", [
    (1, "https://www.apartments.com/philadelphia-pa/?sk=abc&rt=4"),
    (2, "https://www.apartments.com/philadelphia-pa/2/?sk=abc&rt=4"),
    (3, "https://www.apartments.com/philadelphia-pa/3/?sk=abc&rt=4"),
])
def test_page_url(page, expected):
    assert page_url("https://www.apartments.com/philadelphia-pa/?sk=abc&rt=4", page) == expected


def test_page_url_normalises_pasted_page_two():
    assert page_url("https://www.apartments.com/philadelphia-pa/2/", 3) == \
        "https://www.apartments.com/philadelphia-pa/3/"


def test_is_blocked():
    assert is_blocked(ACCESS_DENIED, None)
    assert is_blocked("<html></html>", 403)
    assert not is_blocked("<html><title>Apartments</title></html>", 200)


# --- real page fixture -----------------------------------------------------

def test_parses_every_card_on_real_page(fixture_html):
    listings, has_next = parse_page(fixture_html("apartments_com/search_page1.html"))
    assert len(listings) == 40
    assert has_next
    assert all(x.url.startswith("https://www.apartments.com/") for x in listings)
    assert all(x.address and x.price_min and x.beds_min is not None for x in listings)


def test_multi_unit_card_keeps_ranges(fixture_html):
    listings, _ = parse_page(fixture_html("apartments_com/search_page1.html"))
    first = listings[0]
    assert first.listing_id == "7bjgd3y"
    assert first.name == "Lynnewood Gardens"
    assert first.address == "1950 Ashbourne Rd, Elkins Park, PA 19027"
    assert (first.price_min, first.price_max) == (1515, 2410)
    assert (first.beds_min, first.beds_max) == (2, 3)
    assert (first.baths_min, first.baths_max) == (1, 1.5)
    assert first.image_url and first.image_url.startswith("https://images1.apartments.com/")


def test_total_monthly_price_prefix(fixture_html):
    listings, _ = parse_page(fixture_html("apartments_com/search_page1.html"))
    by_id = {x.listing_id: x for x in listings}
    assert (by_id["h2yzew1"].price_min, by_id["h2yzew1"].price_max) == (1454, 2216)
    assert (by_id["gp3x56x"].price_min, by_id["gp3x56x"].price_max) == (2375, 2375)


def test_last_page_has_no_next(fixture_html):
    html = fixture_html("apartments_com/search_page1.html").replace('class="next"', 'class="x"')
    _, has_next = parse_page(html)
    assert not has_next


# --- scraper loop with fake fetcher ----------------------------------------

class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages  # list of FetchResult | Exception, consumed in order
        self.urls = []

    def fetch(self, url, ready_selector=None):
        self.urls.append(url)
        item = self.pages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _scraper(fetcher, **kw):
    return ApartmentsComScraper(fetcher, sleep=lambda s: None, **kw)


def test_paginates_until_no_next(fixture_html):
    html = fixture_html("apartments_com/search_page1.html")
    last = html.replace('class="next"', 'class="x"')
    fetcher = FakeFetcher([FetchResult(html, None), FetchResult(last, None)])
    result = _scraper(fetcher).scrape("https://www.apartments.com/x/")
    assert result.pages_fetched == 2
    assert fetcher.urls == ["https://www.apartments.com/x/", "https://www.apartments.com/x/2/"]
    assert len(result.listings) == 40  # same cards on both pages are deduplicated
    assert not result.hit_page_cap


def test_page_cap(fixture_html):
    html = fixture_html("apartments_com/search_page1.html")
    fetcher = FakeFetcher([FetchResult(html, None)] * 2)
    result = _scraper(fetcher, max_pages=2).scrape("https://www.apartments.com/x/")
    assert result.hit_page_cap


def test_retries_then_succeeds(fixture_html):
    last = fixture_html("apartments_com/search_page1.html").replace('class="next"', 'class="x"')
    fetcher = FakeFetcher([FetchError("boom"), FetchResult(ACCESS_DENIED, None),
                           FetchResult(last, None)])
    assert len(_scraper(fetcher).scrape("https://www.apartments.com/x/").listings) == 40


def test_blocked_after_all_retries():
    fetcher = FakeFetcher([FetchResult(ACCESS_DENIED, None)] * 3)
    with pytest.raises(BlockedError):
        _scraper(fetcher).scrape("https://www.apartments.com/x/")


def test_zero_cards_on_page_one_is_a_failure():
    fetcher = FakeFetcher([FetchResult("<html><body>redesigned</body></html>", 200)])
    with pytest.raises(ScrapeError, match="0 listings"):
        _scraper(fetcher).scrape("https://www.apartments.com/x/")

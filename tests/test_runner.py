import pytest

from apartment_hunter.config import Search
from apartment_hunter.models import ScrapeResult
from apartment_hunter.notifiers import NotifyError
from apartment_hunter.runner import SearchRunner
from apartment_hunter.scrapers import ScrapeError
from apartment_hunter.storage import Store
from conftest import make_listing

SEARCH = Search("Downtown", "https://www.apartments.com/x/", 15, "https://discord/webhook")


class FakeScraper:
    def __init__(self):
        self.next = []

    def scrape(self, url):
        item = self.next.pop(0)
        if isinstance(item, Exception):
            raise item
        return ScrapeResult(item, 1, False)


class FakeNotifier:
    def __init__(self):
        self.listings, self.texts, self.fail = [], [], False

    def send_listing(self, listing, search_name, previous_price=None):
        if self.fail:
            raise NotifyError("down")
        self.listings.append((listing.listing_id, previous_price))

    def send_text(self, message):
        if self.fail:
            raise NotifyError("down")
        self.texts.append(message)


@pytest.fixture
def env():
    scraper, notifier, store = FakeScraper(), FakeNotifier(), Store(":memory:")
    runner = SearchRunner(scraper, store, lambda s: notifier)
    return scraper, notifier, store, runner


def test_first_run_seeds_silently(env):
    scraper, notifier, store, runner = env
    scraper.next = [[make_listing("a"), make_listing("b")]]
    runner.run(SEARCH)
    assert notifier.listings == []
    assert len(notifier.texts) == 1 and "2 current listings" in notifier.texts[0]
    assert store.search_state(SEARCH.name).seeded


def test_alert_existing_skips_seeding():
    scraper, notifier, store = FakeScraper(), FakeNotifier(), Store(":memory:")
    runner = SearchRunner(scraper, store, lambda s: notifier, alert_existing=True)
    scraper.next = [[make_listing("a"), make_listing("b")]]
    runner.run(SEARCH)
    assert [l for l, _ in notifier.listings] == ["a", "b"]


def test_new_listing_alerts_once(env):
    scraper, notifier, store, runner = env
    scraper.next = [[make_listing("a")], [make_listing("a"), make_listing("b")],
                    [make_listing("a"), make_listing("b")]]
    for _ in range(3):
        runner.run(SEARCH)
    assert notifier.listings == [("b", None)]


def test_price_drop_alerts_but_increase_does_not(env):
    scraper, notifier, store, runner = env
    scraper.next = [[make_listing("a", 2000)], [make_listing("a", 2100)],
                    [make_listing("a", 1900)], [make_listing("a", 1900)]]
    for _ in range(4):
        runner.run(SEARCH)
    assert notifier.listings == [("a", 2100)]


def test_failed_delivery_is_retried_next_run(env):
    scraper, notifier, store, runner = env
    scraper.next = [[], [make_listing("a")], [make_listing("a")]]
    runner.run(SEARCH)  # seed with nothing
    notifier.fail = True
    runner.run(SEARCH)
    assert store.get_listing(SEARCH.name, "a") is None
    notifier.fail = False
    runner.run(SEARCH)
    assert notifier.listings == [("a", None)]


def test_health_alert_after_threshold_then_recovery(env):
    scraper, notifier, store, runner = env
    scraper.next = [ScrapeError("blocked")] * 4 + [[make_listing("a")]]
    for _ in range(2):
        runner.run(SEARCH)
    assert notifier.texts == []
    runner.run(SEARCH)
    assert len(notifier.texts) == 1 and "looks broken" in notifier.texts[0]
    runner.run(SEARCH)  # still failing: no second alert
    assert len(notifier.texts) == 1
    runner.run(SEARCH)
    assert "recovered" in notifier.texts[1]
    assert store.search_state(SEARCH.name).consecutive_failures == 0


def test_searches_are_tracked_independently(env):
    scraper, notifier, store, runner = env
    other = Search("Suburbs", SEARCH.url, 15, SEARCH.webhook_url)
    scraper.next = [[make_listing("a")], [make_listing("a")]]
    runner.run(SEARCH)
    runner.run(other)
    assert store.search_state(other.name).seeded
    assert store.get_listing(other.name, "a") is not None


def test_legacy_import_runs_once(tmp_path):
    seen = tmp_path / "seen_listings.txt"
    seen.write_text("a\nb\n\n")
    store = Store(tmp_path / "db.sqlite")
    assert store.import_legacy_seen_file(seen, ["Downtown"]) == 2
    assert store.import_legacy_seen_file(seen, ["Downtown"]) == 0
    assert store.get_listing("Downtown", "a") is not None

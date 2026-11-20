from types import SimpleNamespace

import pytest

from apartment_hunter.runner import SearchRunner, SourceResult
from apartment_hunter.sources import ScrapeError, SourcePage
from apartment_hunter.storage import SourceHealth, Store
from conftest import make_listing, make_search
from fakes import FakeNotifier, FakeSource

SEARCH = make_search(name="Chicago")
BOTH = make_search("s2", name="Both", sources=("zillow", "craigslist"))


@pytest.fixture
def env():
    store = Store(":memory:")
    zillow, craigslist, notifier = FakeSource("zillow"), FakeSource("craigslist"), FakeNotifier()
    runner = SearchRunner({"zillow": zillow, "craigslist": craigslist}, store, lambda search: notifier)
    store.add_search(SEARCH)
    store.add_search(BOTH)
    return SimpleNamespace(store=store, zillow=zillow, craigslist=craigslist, notifier=notifier,
                           runner=runner)


def test_seeding_run_records_silently_and_reports_the_total(env):
    env.zillow.pages = [SourcePage([make_listing("a"), make_listing("b")], 214, False)]
    outcome = env.runner.run(SEARCH)
    assert outcome.results == (SourceResult("zillow", True, True, 2, 214, False, None),)
    assert env.notifier.listings == [] and env.notifier.texts == []
    assert env.store.listing_count(SEARCH.id) == 2


def test_seeding_reads_up_to_three_pages_then_runs_read_page_one(env):
    more = SourcePage([make_listing("a")], 500, True)
    env.zillow.pages = [more, more, more, more]
    env.runner.run(SEARCH)
    env.runner.run(SEARCH)
    assert env.zillow.requested == [1, 2, 3, 1]


def test_new_listing_alerts_once(env):
    env.zillow.pages = [[make_listing("a")], [make_listing("a"), make_listing("b")],
                        [make_listing("a"), make_listing("b")]]
    for _ in range(3):
        env.runner.run(SEARCH)
    assert env.notifier.listings == [("b", None)]


def test_price_drop_alerts_but_increase_does_not(env):
    env.zillow.pages = [[make_listing("a", 2000)], [make_listing("a", 2100)],
                        [make_listing("a", 1900)], [make_listing("a", 1900)]]
    for _ in range(4):
        env.runner.run(SEARCH)
    assert env.notifier.listings == [("a", 2100)]


def test_unknown_price_is_never_a_price_drop(env):
    env.zillow.pages = [[make_listing("a", 2000)], [make_listing("a", None)], [make_listing("a", 2000)]]
    for _ in range(3):
        env.runner.run(SEARCH)
    assert env.notifier.listings == []


def test_failed_delivery_is_retried_next_run(env):
    env.zillow.pages = [[], [make_listing("a")], [make_listing("a")]]
    env.runner.run(SEARCH)
    env.notifier.fail = True
    env.runner.run(SEARCH)
    assert env.store.get_listing(SEARCH.id, "zillow", "a") is None
    env.notifier.fail = False
    env.runner.run(SEARCH)
    assert env.notifier.listings == [("a", None)]


def test_burst_cap_sends_ten_and_summarises_the_rest(env):
    fourteen = [make_listing(str(i)) for i in range(14)]
    env.zillow.pages = [[], fourteen, fourteen]
    for _ in range(3):
        env.runner.run(SEARCH)
    assert [i for i, _ in env.notifier.listings] == [str(i) for i in range(10)]
    assert env.notifier.texts == ["…and 4 more new places on Zillow: https://zillow.example/results"]
    assert env.store.listing_count(SEARCH.id) == 14


def test_burst_summary_does_not_call_price_drops_new_places(env):
    ten_new = [make_listing(f"n{i}") for i in range(10)]
    env.zillow.pages = [
        [make_listing("d0", 2000), make_listing("d1", 2000)],
        ten_new + [make_listing("d0", 1900), make_listing("n10")],
        [make_listing(f"m{i}") for i in range(10)] + [make_listing("d1", 1800)],
    ]
    env.runner.run(SEARCH)
    env.runner.run(SEARCH)
    assert env.notifier.texts == [
        "…and 2 more new places and price drops on Zillow: https://zillow.example/results"]
    env.runner.run(SEARCH)
    assert env.notifier.texts[-1] == "…and 1 more price drop on Zillow: https://zillow.example/results"


def test_channel_gone_stops_the_run_without_marking_seen(env):
    env.zillow.pages = [[], [make_listing("a"), make_listing("b")]]
    env.runner.run(SEARCH)
    env.notifier.gone = True
    outcome = env.runner.run(SEARCH)
    assert outcome.channel_gone and outcome.alerts_sent == 0
    assert env.store.get_listing(SEARCH.id, "zillow", "a") is None
    assert env.store.get_listing(SEARCH.id, "zillow", "b") is None


def test_health_alert_per_source_names_the_source_still_working(env):
    env.zillow.pages = [[]] + [ScrapeError("blocked.")] * 4 + [[]]
    env.craigslist.pages = [[]] * 6
    for _ in range(4):
        env.runner.run(BOTH)
    assert env.notifier.texts == [
        "⚠️ Zillow hasn't worked for **Both** in 3 checks in a row: blocked. "
        "Craigslist is still being checked."]
    env.runner.run(BOTH)
    env.runner.run(BOTH)
    assert env.notifier.texts[-1] == "✅ Zillow is working again for **Both**."


def test_failed_seeding_then_success_says_it_is_working_now(env):
    env.zillow.pages = [ScrapeError("blocked"), SourcePage([make_listing("a")], 1, False)]
    first = env.runner.run(SEARCH)
    assert first.results[0].ok is False and first.results[0].error == "blocked"
    env.runner.run(SEARCH)
    assert env.notifier.texts == [
        "✅ Zillow is working for **Chicago** now: 1 place matches. I'll post new ones here."]


def test_zero_matches_is_healthy(env):
    env.zillow.pages = [SourcePage([], 0, False), SourcePage([], 0, False)]
    env.runner.run(SEARCH)
    outcome = env.runner.run(SEARCH)
    assert outcome.results[0].ok
    assert env.store.source_health(SEARCH.id, "zillow") == SourceHealth(True, 0, False, None)


def test_a_failing_source_does_not_stop_the_other_from_seeding(env):
    env.zillow.pages = [ScrapeError("blocked")]
    env.craigslist.pages = [[make_listing("c", source="craigslist")]]
    env.runner.run(BOTH)
    assert env.store.source_health(BOTH.id, "craigslist").seeded
    assert not env.store.source_health(BOTH.id, "zillow").seeded

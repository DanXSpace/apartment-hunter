from dataclasses import replace
from datetime import UTC, datetime

import pytest

from apartment_hunter.storage import DuplicateName, SeenListing, SourceHealth, Store
from conftest import make_listing, make_search


@pytest.fixture
def store():
    return Store(":memory:")


def test_searches_round_trip_and_list_per_server(store):
    a, b, c = make_search("a", 1, "A"), make_search("b", 1, "B"), make_search("c", 2, "C")
    for s in (a, b, c):
        store.add_search(s)
    assert store.get_search("a") == a
    assert [s.name for s in store.searches(1)] == ["A", "B"]
    assert len(store.searches()) == 3
    assert store.find_search(1, "a") == a and store.find_search(2, "A") is None


def test_names_are_unique_within_a_server_only(store):
    store.add_search(make_search("a", 1, "Home"))
    with pytest.raises(DuplicateName):
        store.add_search(make_search("b", 1, "home"))
    store.add_search(make_search("c", 2, "Home"))


def test_reseed_clears_listings_and_health_but_a_rename_does_not(store):
    s = make_search()
    store.add_search(s)
    store.save_listing(s.id, make_listing("x"))
    store.mark_seeded(s.id, "zillow")
    store.update_search(replace(s, name="Renamed"), reseed=False)
    assert store.listing_count(s.id) == 1 and store.source_health(s.id, "zillow").seeded
    store.update_search(replace(s, name="Renamed"), reseed=True)
    assert store.listing_count(s.id) == 0 and not store.source_health(s.id, "zillow").seeded


def test_delete_removes_the_search_with_its_listings(store):
    s = make_search()
    store.add_search(s)
    store.save_listing(s.id, make_listing("x"))
    store.delete_search(s.id)
    assert store.get_search(s.id) is None and store.listing_count(s.id) == 0


def test_failures_count_per_source_and_reset_on_success(store):
    store.add_search(make_search())
    assert store.record_failure("s1", "zillow", "blocked") == 1
    assert store.record_failure("s1", "zillow", "blocked") == 2
    store.mark_broken_alert_sent("s1", "zillow")
    assert store.source_health("s1", "craigslist").consecutive_failures == 0
    store.record_success("s1", "zillow")
    assert store.source_health("s1", "zillow") == SourceHealth(False, 0, False, None)


def test_listings_are_keyed_by_source_and_keep_the_last_known_price(store):
    store.add_search(make_search())
    store.save_listing("s1", make_listing("x", 2000, source="zillow"))
    store.save_listing("s1", make_listing("x", 1500, source="craigslist"))
    store.save_listing("s1", make_listing("x", None, source="zillow"))
    assert store.listing_count("s1") == 2
    assert store.get_listing("s1", "zillow", "x") == SeenListing("x", 2000)


def test_last_run_and_owner_alert_flag_round_trip():
    moment = datetime(2026, 11, 6, 8, 30, tzinfo=UTC)
    store = Store(":memory:", now=lambda: moment)
    store.add_search(make_search())
    assert store.last_run("s1") is None
    store.record_run("s1")
    assert store.last_run("s1") == moment
    assert not store.owner_alert_sent("zillow")
    store.set_owner_alert_sent("zillow", True)
    assert store.owner_alert_sent("zillow") and not store.owner_alert_sent("craigslist")
    store.set_owner_alert_sent("zillow", False)
    assert not store.owner_alert_sent("zillow")

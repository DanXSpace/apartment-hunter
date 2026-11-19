from collections import defaultdict
from datetime import timedelta
from itertools import count
from types import SimpleNamespace

import pytest

from apartment_hunter.areas import AreaNotFound
from apartment_hunter.criteria import Area, Pets
from apartment_hunter.runner import SearchRunner
from apartment_hunter.scheduler import Scheduler
from apartment_hunter.service import Limits, NewSearch, SearchError, SearchService
from apartment_hunter.sources import ScrapeError, SourcePage
from apartment_hunter.storage import Store
from conftest import AT, CHICAGO, make_listing, make_search
from fakes import FakeNotifier, FakeSource


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += minutes * 60


class FakeAreas:
    """Knows only "Chicago, IL"; anything else isn't a place. `down` makes every lookup fail."""

    def __init__(self):
        self.down: ScrapeError | None = None

    def suggest(self, text: str) -> list[str]:
        return ["Chicago, IL"] if "chi" in text.casefold() else []

    def resolve(self, text: str) -> Area:
        if self.down:
            raise self.down
        if text == "Chicago, IL":
            return CHICAGO
        raise AreaNotFound(f'I couldn\'t find a place called "{text}".')


@pytest.fixture
def env():
    store = Store(":memory:")
    zillow = FakeSource("zillow")
    notifiers: defaultdict[int, FakeNotifier] = defaultdict(FakeNotifier)
    runner = SearchRunner({"zillow": zillow}, store, lambda search: notifiers[search.channel_id])
    clock = FakeClock()
    scheduler = Scheduler(lambda sid: service.run(sid), clock=clock, next_delay=lambda m: m * 60)
    scheduler.start()
    ids = count(1)
    areas = FakeAreas()
    dms: list[tuple[int, str]] = []
    service = SearchService(
        store=store, runner=runner, scheduler=scheduler, areas=areas, source_keys=("zillow",),
        clock=lambda: AT + timedelta(seconds=clock()), new_id=lambda: f"s{next(ids)}",
        direct_message=lambda user_id, text: dms.append((user_id, text)),
    )
    return SimpleNamespace(store=store, zillow=zillow, notifiers=notifiers, clock=clock,
                           scheduler=scheduler, service=service, dms=dms, areas=areas)


def test_create_seeds_right_away_and_reports_the_count(env):
    env.zillow.pages = [SourcePage([make_listing("a"), make_listing("b")], 214, False)]
    search, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL", max_price=2400, min_beds=2,
                                                             pets=Pets.DOGS), can_post=True)
    env.scheduler.run_pending()
    outcome = pending.wait(0)
    assert search.name == "Chicago, IL · 2+ bd · up to $2,400 · dogs"
    assert outcome.results[0].total == 214 and outcome.results[0].seeded
    assert env.notifiers[10].texts == [] and env.notifiers[10].listings == []


def test_create_refuses_without_permission_and_saves_nothing(env):
    with pytest.raises(SearchError, match="can't post in this channel"):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=False)
    assert env.store.searches() == []


def test_create_refuses_an_unknown_area_and_saves_nothing(env):
    with pytest.raises(SearchError, match="couldn't find a place called"):
        env.service.create(1, 10, 99, NewSearch("Illinois"), can_post=True)
    assert env.store.searches() == []


def test_limits_per_server_and_in_total(env):
    env.service.limits = Limits(per_server=2, total=3)
    for name in ("a", "b"):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL", name=name), can_post=True)
    with pytest.raises(SearchError, match="already has 2 searches"):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="c"), can_post=True)
    env.service.create(2, 20, 99, NewSearch("Chicago, IL", name="c"), can_post=True)
    with pytest.raises(SearchError, match="as many searches as it can"):
        env.service.create(3, 30, 99, NewSearch("Chicago, IL", name="d"), can_post=True)


def test_names_are_unique_per_server_only(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    with pytest.raises(SearchError, match='already has a search called "Home"'):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.service.create(2, 20, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)


def test_servers_only_see_their_own_searches(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Mine"), can_post=True)
    env.service.create(2, 20, 99, NewSearch("Chicago, IL", name="Theirs"), can_post=True)
    assert [s.search.name for s in env.service.statuses(2)] == ["Theirs"]
    assert env.service.names(2, "") == ["Theirs"]


def test_status_needs_attention_with_the_reason_after_one_failure(env):
    env.zillow.pages = [SourcePage([], 0, False), ScrapeError("Zillow is blocking requests.")]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=True)
    env.scheduler.run_pending()
    assert env.service.statuses(1)[0].state == "Healthy"
    env.clock.advance(15)
    env.scheduler.run_pending()
    status = env.service.statuses(1)[0]
    assert (status.state, status.reason) == ("Needs attention", "Zillow: Zillow is blocking requests.")


def test_loaded_searches_are_spread_across_the_first_interval(env):
    env.store.add_search(make_search("a", name="A"))
    env.store.add_search(make_search("b", name="B"))
    offsets = iter([100.0, 700.0])
    env.service.rand = lambda lo, hi: next(offsets)
    env.service.load()
    assert env.scheduler.next_run("a") == env.clock() + 100
    assert env.scheduler.next_run("b") == env.clock() + 700


def test_channel_gone_pauses_the_search_and_tells_its_creator(env):
    env.zillow.pages = [[], [make_listing("a")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.notifiers[10].gone = True
    env.clock.advance(15)
    env.scheduler.run_pending()
    status = env.service.statuses(1)[0]
    assert status.state == "Needs attention" and "can't post" in status.reason
    assert env.scheduler.is_paused(search.id)
    assert env.dms == [(99, 'I paused your search "Home": I can\'t post in its channel anymore (it was '
                            "deleted or I lost permission). Fix the channel and use /search resume, or "
                            "delete it with /search delete.")]


def test_a_zero_minimum_price_or_baths_means_no_minimum(env):
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", min_price=0, max_price=2400,
                                                       min_beds=0, min_baths=0), can_post=True)
    assert search.name == "Chicago, IL · studio+ · up to $2,400"


def test_create_explains_when_the_place_lookup_is_down_and_saves_nothing(env):
    env.areas.down = ScrapeError("Zillow's place lookup isn't answering right now.")
    with pytest.raises(SearchError) as refused:
        env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=True)
    assert str(refused.value) == ("I couldn't look up places right now: Zillow's place lookup isn't "
                                  "answering right now. Try again in a minute.")
    assert env.store.searches() == []


class StatusCheckingSource(FakeSource):
    """A FakeSource that records the Search's status each time its Run reads a page."""

    def __init__(self, key, look):
        super().__init__(key)
        self.look = look
        self.seen: list[str] = []

    def fetch(self, criteria, page):
        self.seen.append(self.look())
        return super().fetch(criteria, page)


def test_statuses_show_not_run_yet_running_paused_and_healthy(env):
    zillow = StatusCheckingSource("zillow", lambda: env.service.statuses(1)[1].state)
    zillow.pages = [[make_listing("a")]]
    env.service.runner = SearchRunner({"zillow": zillow}, env.store, lambda search: env.notifiers[10])
    env.store.add_search(make_search("p", name="Paused", paused=True))
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    assert [(s.search.name, s.state) for s in env.service.statuses(1)] == [
        ("Paused", "Paused"), ("Home", "Not run yet")]
    env.scheduler.run_pending()
    assert zillow.seen == ["Running"]
    home = env.service.statuses(1)[1]
    assert (home.state, home.reason, home.listings) == ("Healthy", None, 1)
    assert home.last_run is not None


def test_an_unexpected_error_resolves_the_pending_with_a_plain_failure(env, caplog):
    env.zillow.pages = [RuntimeError("parser exploded")]
    _, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Other"), can_post=True)
    env.zillow.pages.append([make_listing("a")])
    assert env.scheduler.run_pending() == 2
    outcome = pending.wait(0)
    assert outcome.error == ("Something went wrong checking this Search. "
                             "The owner can find details in the log.")
    assert "parser exploded" in caplog.text and "Traceback" in caplog.text
    assert env.service.statuses(1)[1].state == "Healthy"


def test_create_refuses_sources_the_bot_does_not_have_and_saves_nothing(env):
    with pytest.raises(SearchError) as refused:
        env.service.create(1, 10, 99, NewSearch("Chicago, IL", sources=("zillow", "craigslist")),
                           can_post=True)
    assert str(refused.value) == "I can't check Craigslist yet. Pick from: Zillow."
    with pytest.raises(SearchError, match="Pick at least one"):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL", sources=()), can_post=True)
    assert env.store.searches() == []

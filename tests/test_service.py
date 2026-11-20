import json
from datetime import timedelta

import pytest

from apartment_hunter.countries import COUNTRIES, Country
from apartment_hunter.criteria import Area, HomeType, Pets
from apartment_hunter.runner import SearchRunner
from apartment_hunter.service import Limits, NewSearch, SearchChanges, SearchError
from apartment_hunter.sources import SOURCE_NAMES, Filter, ScrapeError, SourcePage
from apartment_hunter.sources.bienici import BieniciSource
from apartment_hunter.sources.kleinanzeigen import KleinanzeigenSource
from conftest import AT, CAMDEN, CHICAGO, FakeAreas, add_source, make_listing, make_search
from fakes import MINUTE, FakeHttp, FakeSource


def test_create_seeds_right_away_and_reports_the_count(env):
    env.zillow.pages = [SourcePage([make_listing("a"), make_listing("b")], 214, False)]
    search, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL", max_price=2400, min_beds=2,
                                                             pets=Pets.SMALL_DOGS), can_post=True)
    env.scheduler.run_pending()
    outcome = pending.wait(0)
    assert search.name == "Chicago, IL · 2+ bd · up to $2,400 · small dogs"
    assert outcome.results[0].total == 214 and outcome.results[0].seeded
    assert env.notifiers[10].texts == [] and env.notifiers[10].listings == []


def test_create_places_the_area_once_and_runs_search_by_the_saved_location(env):
    env.zillow.pages = [[make_listing("a")], [make_listing("a")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=True)
    env.scheduler.run_pending()
    env.clock.advance(15 * MINUTE)
    env.scheduler.run_pending()
    assert search.criteria.area.locations == {"zillow": {"at": "Chicago, IL"}}
    assert env.zillow.placed == ["Chicago, IL"]
    assert env.zillow.locations == [{"at": "Chicago, IL"}, {"at": "Chicago, IL"}]
    assert env.store.get_search(search.id) == search


def test_create_refuses_without_permission_and_saves_nothing(env):
    with pytest.raises(SearchError, match="can't post in this channel"):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=False)
    assert env.service.statuses(1) == []


def test_create_refuses_an_unknown_area_and_saves_nothing(env):
    with pytest.raises(SearchError, match="couldn't find a place called"):
        env.service.create(1, 10, 99, NewSearch("Illinois"), can_post=True)
    assert env.service.statuses(1) == []


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
    env.clock.advance(15 * MINUTE)
    env.scheduler.run_pending()
    status = env.service.statuses(1)[0]
    assert (status.state, status.reason) == ("Needs attention", "Zillow: Zillow is blocking requests.")


def test_loaded_searches_are_spread_across_the_first_interval(env):
    env.store.add_search(make_search("a", name="A"))  # saved by an earlier start of the bot
    env.store.add_search(make_search("b", name="B"))
    env.zillow.pages = [[], []]
    offsets = iter([100.0, 700.0])
    env.service.rand = lambda lo, hi: next(offsets)
    env.service.load()

    def states():
        return [(s.search.name, s.state) for s in env.service.statuses(1)]

    env.clock.advance(99)
    env.scheduler.run_pending()
    assert states() == [("A", "Not run yet"), ("B", "Not run yet")]
    env.clock.advance(1)
    env.scheduler.run_pending()
    assert states() == [("A", "Healthy"), ("B", "Not run yet")]
    env.clock.advance(600)
    env.scheduler.run_pending()
    assert states() == [("A", "Healthy"), ("B", "Healthy")]


def test_channel_gone_pauses_the_search_and_tells_its_creator(env):
    env.zillow.pages = [[], [make_listing("a")]]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.notifiers[10].gone = True
    env.clock.advance(15 * MINUTE)
    env.scheduler.run_pending()
    status = env.service.statuses(1)[0]
    assert status.state == "Needs attention" and "can't post" in status.reason
    assert status.search.paused
    env.clock.advance(15 * MINUTE)
    assert env.scheduler.run_pending() == 0
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
    assert env.service.statuses(1) == []


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
    assert env.service.statuses(1) == []


def test_editing_criteria_reseeds_without_alerting(env):
    env.zillow.pages = [[make_listing("a")], [make_listing("a"), make_listing("b"), make_listing("c")]]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    _, pending = env.service.edit(1, "Home", SearchChanges(max_price=1800))
    env.scheduler.run_pending()
    assert pending.wait(0).results[0].seeded
    assert env.notifiers[10].listings == []


def test_renaming_keeps_state_and_does_not_reseed(env):
    env.zillow.pages = [[make_listing("a")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    renamed, pending = env.service.edit(1, "home", SearchChanges(name="Flat"))
    assert pending is None and renamed.name == "Flat"
    assert env.service.statuses(1)[0].listings == 1


def test_editing_a_minimum_to_zero_removes_it(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", min_price=1000, min_baths=1, name="Home"),
                       can_post=True)
    edited, _ = env.service.edit(1, "Home", SearchChanges(min_price=0, min_baths=0))
    assert (edited.criteria.min_price, edited.criteria.min_baths) == (None, None)


def test_editing_refuses_an_unknown_area_or_source_and_changes_nothing(env):
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    with pytest.raises(SearchError, match="couldn't find a place called"):
        env.service.edit(1, "Home", SearchChanges(area="Illinois", max_price=1800))
    with pytest.raises(SearchError, match="can't check Craigslist yet"):
        env.service.edit(1, "Home", SearchChanges(sources=("craigslist",)))
    assert [s.search for s in env.service.statuses(1)] == [search]


def test_renaming_to_a_name_already_used_in_the_server_is_refused(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Flat"), can_post=True)
    with pytest.raises(SearchError, match='already has a search called "Flat"'):
        env.service.edit(1, "Home", SearchChanges(name="Flat"))


def test_pause_skips_scheduled_runs_and_resume_restarts_them(env):
    env.zillow.pages = [[], [], []]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.service.pause(1, "Home")
    env.clock.advance(15 * MINUTE)
    assert env.scheduler.run_pending() == 0
    assert env.service.statuses(1)[0].state == "Paused"
    env.service.resume(1, "Home")
    assert env.scheduler.run_pending() == 1


def test_delete_removes_the_search_and_its_listings(env):
    env.zillow.pages = [[make_listing("a")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.service.delete(1, "Home")
    assert env.service.statuses(1) == [] and env.store.listing_count(search.id) == 0


def test_deleting_before_the_first_run_ends_the_wait_with_no_outcome(env):
    _, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.service.delete(1, "Home")
    assert pending.cancelled and pending.wait(0) is None
    assert env.scheduler.run_pending() == 0


def test_check_runs_now_and_is_rate_limited(env):
    env.zillow.pages = [[], [], []]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    _, pending = env.service.check(1, "Home")
    env.scheduler.run_pending()
    assert pending.wait(0) is not None
    with pytest.raises(SearchError, match='check "Home" again in 5 minutes'):
        env.service.check(1, "Home")
    env.clock.advance(5 * MINUTE)
    env.service.check(1, "Home")


def test_check_too_soon_rounds_the_minutes_left_up(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.service.check(1, "Home")
    env.clock.advance(1.5 * MINUTE)
    with pytest.raises(SearchError) as refused:
        env.service.check(1, "Home")
    assert str(refused.value) == 'You can check "Home" again in 4 minutes.'


def test_check_too_soon_says_one_minute_in_the_singular(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.service.check(1, "Home")
    env.clock.advance(4.5 * MINUTE)
    with pytest.raises(SearchError) as refused:
        env.service.check(1, "Home")
    assert str(refused.value) == 'You can check "Home" again in 1 minute.'


def test_other_servers_cannot_touch_a_search(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    with pytest.raises(SearchError) as refused:
        env.service.pause(2, "Home")
    assert str(refused.value) == ('There\'s no search called "Home" in this server. '
                                  "/search list shows them.")
    for act in (env.service.resume, env.service.delete, env.service.check):
        with pytest.raises(SearchError, match="no search called \"Home\" in this server"):
            act(2, "Home")
    with pytest.raises(SearchError, match="no search called \"Home\" in this server"):
        env.service.edit(2, "Home", SearchChanges(name="Mine"))
    assert env.service.statuses(1)[0].state == "Not run yet"


def test_bot_removed_from_a_server_deletes_its_searches(env):
    _, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="A"), can_post=True)
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="B"), can_post=True)
    env.service.create(2, 20, 99, NewSearch("Chicago, IL", name="C"), can_post=True)
    assert env.service.server_removed(1) == 2
    assert env.service.statuses(1) == []
    assert env.service.names(2, "") == ["C"]
    assert pending.cancelled
    env.zillow.pages = [[]]
    assert env.scheduler.run_pending() == 1  # only C's first Run is left


def test_owner_hears_once_when_a_source_fails_everywhere_and_once_on_recovery(env):
    env.service.owner_id = 7
    env.zillow.pages = [[], []] + [ScrapeError("blocked.")] * 8 + [[], []]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="A"), can_post=True)
    env.service.create(2, 20, 98, NewSearch("Chicago, IL", name="B"), can_post=True)
    for _ in range(5):
        env.scheduler.run_pending()
        env.clock.advance(15 * MINUTE)
    assert [d for d in env.dms if d[0] == 7] == [
        (7, "⚠️ Zillow has failed in every search for 3 checks in a row: blocked. "
            "The host may be blocked, or Zillow changed its pages.")]
    env.scheduler.run_pending()
    assert env.dms[-1] == (7, "✅ Zillow is working again.")


def test_owner_hears_of_recovery_only_once_a_search_that_ran_the_source_succeeds(env):
    env.service.owner_id = 7
    env.zillow.pages = [ScrapeError("blocked.")] * 8 + [[]]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="A"), can_post=True)
    env.service.create(2, 20, 98, NewSearch("Chicago, IL", name="B"), can_post=True)
    for _ in range(3):
        env.scheduler.run_pending()
        env.clock.advance(15 * MINUTE)
    owner_dms = [text for user, text in env.dms if user == 7]
    assert len(owner_dms) == 1 and owner_dms[0].startswith("⚠️ Zillow has failed in every search")
    late = make_search("late", server_id=3, name="Not run yet", created_at=AT + timedelta(days=1))
    env.store.add_search(late)  # a Search that hasn't run Zillow yet
    env.scheduler.run_pending()  # A and B fail again
    assert [text for user, text in env.dms if user == 7] == owner_dms
    env.service.run(late.id)  # its first Run works
    assert env.dms[-1] == (7, "✅ Zillow is working again.")


def test_resume_after_the_channel_came_back_clears_the_reason(env):
    env.zillow.pages = [[], [make_listing("a")], [make_listing("a")]]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.notifiers[10].gone = True
    env.clock.advance(15 * MINUTE)
    env.scheduler.run_pending()
    env.notifiers[10].gone = False
    env.service.resume(1, "Home")
    assert env.service.statuses(1)[0].state == "Healthy"
    env.clock.advance(15 * MINUTE)
    env.scheduler.run_pending()
    assert env.notifiers[10].listings == [("a", None)]


def test_on_connect_searches_of_servers_the_bot_left_while_offline_are_deleted(env):
    _, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="A"), can_post=True)
    env.service.create(2, 20, 99, NewSearch("Chicago, IL", name="B"), can_post=True)
    env.service.create(3, 30, 99, NewSearch("Chicago, IL", name="C"), can_post=True)
    assert env.service.keep_servers({2, 3, 4}) == 1
    assert [env.service.names(server, "") for server in (1, 2, 3)] == [[], ["B"], ["C"]]
    assert pending.cancelled


def test_a_criteria_edit_renames_a_search_still_named_after_its_criteria(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", max_price=2400), can_post=True)
    edited, _ = env.service.edit(1, "Chicago, IL · up to $2,400", SearchChanges(max_price=1800))
    assert edited.name == "Chicago, IL · up to $1,800"
    assert env.service.names(1, "") == ["Chicago, IL · up to $1,800"]


def test_a_criteria_edit_keeps_a_chosen_name_or_one_whose_new_summary_is_taken(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", max_price=2400, name="Home"), can_post=True)
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", max_price=2400), can_post=True)
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", max_price=1800, name="Chicago, IL · up to $1,500"),
                       can_post=True)
    kept, _ = env.service.edit(1, "Home", SearchChanges(max_price=1800))
    assert kept.name == "Home"
    clash, pending = env.service.edit(1, "Chicago, IL · up to $2,400", SearchChanges(max_price=1500))
    assert clash.name == "Chicago, IL · up to $2,400" and pending is not None
    assert env.service.find(1, clash.name).criteria.max_price == 1500


class MidRunSource(FakeSource):
    """A FakeSource that does `during_fetch` (once) while a Run is reading it, as the bot's thread
    might while the scheduler's thread is mid-Run."""

    def __init__(self, key):
        super().__init__(key)
        self.during_fetch = None

    def fetch(self, criteria, page):
        if self.during_fetch is not None:
            act, self.during_fetch = self.during_fetch, None
            act()
        return super().fetch(criteria, page)


def use_mid_run_source(env) -> MidRunSource:
    zillow = MidRunSource("zillow")
    env.service.runner = SearchRunner({"zillow": zillow}, env.store, lambda search: env.notifiers[10])
    return zillow


def test_an_edit_during_a_seeding_run_still_gets_its_own_quiet_seeding_run(env):
    zillow = use_mid_run_source(env)
    zillow.pages = [[make_listing("a")], [make_listing("a"), make_listing("b"), make_listing("c")],
                    [make_listing("a"), make_listing("b"), make_listing("c"), make_listing("d")]]
    _, created = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    edits = []
    zillow.during_fetch = lambda: edits.append(env.service.edit(1, "Home", SearchChanges(max_price=1800)))
    assert env.scheduler.run_pending() == 2  # the interrupted Run, then the edit's Seeding run
    _, edited = edits[0]
    assert edited.wait(0).results[0].seeded and created.wait(0).results[0].seeded
    assert env.notifiers[10].listings == []
    env.clock.advance(15 * MINUTE)
    env.scheduler.run_pending()
    assert env.notifiers[10].listings == [("d", None)]


def test_a_delete_during_a_run_sends_no_alerts_and_keeps_nothing(env):
    zillow = use_mid_run_source(env)
    zillow.pages = [[make_listing("a")], [make_listing("a"), make_listing("b")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    zillow.during_fetch = lambda: env.service.delete(1, "Home")
    env.clock.advance(15 * MINUTE)
    env.scheduler.run_pending()
    assert env.notifiers[10].listings == [] and env.notifiers[10].texts == []
    assert env.service.statuses(1) == []
    assert env.store.listing_count(search.id) == 0  # the service shows nothing of a deleted Search


def test_a_rename_during_a_run_lets_the_run_finish(env):
    zillow = use_mid_run_source(env)
    zillow.pages = [[make_listing("a")], [make_listing("a"), make_listing("b")]]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    zillow.during_fetch = lambda: env.service.edit(1, "Home", SearchChanges(name="Flat"))
    _, pending = env.service.check(1, "Home")
    env.scheduler.run_pending()
    assert pending.wait(0).alerts_sent == 1
    assert env.notifiers[10].listings == [("b", None)]


@pytest.fixture
def uk(env, monkeypatch) -> FakeSource:
    """Adds the UK to the countries table with one (fake) Source, Rightmove, and returns it."""
    monkeypatch.setitem(COUNTRIES, "GB", Country("GB", "United Kingdom", "£", ("rightmove",)))
    monkeypatch.setitem(SOURCE_NAMES, "rightmove", "Rightmove")
    return add_source(env, "rightmove")


def test_a_source_that_cannot_place_the_area_is_left_out_with_its_reason(env, monkeypatch):
    monkeypatch.setitem(COUNTRIES, "US", Country("US", "United States", "$", ("zillow", "craigslist")))
    craigslist = add_source(env, "craigslist")
    craigslist.cant_place = "Craigslist has no site near Chicago, IL."
    search, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=True)
    assert search.sources == ("zillow",)
    assert pending.notes == ("Craigslist was left out: Craigslist has no site near Chicago, IL.",)


def test_create_is_refused_when_no_source_can_place_the_area(env):
    env.zillow.cant_place = "Zillow doesn't know Chicago, IL."
    with pytest.raises(SearchError) as refused:
        env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=True)
    assert str(refused.value) == "I can't search Chicago, IL on any site: Zillow was left out: " \
                                 "Zillow doesn't know Chicago, IL."
    assert env.service.statuses(1) == []


def test_create_in_another_country_uses_its_sources_and_currency(env, uk):
    uk.pages = [[make_listing("a", source="rightmove")]]
    search, _ = env.service.create(1, 10, 99, NewSearch(CAMDEN.display, max_price=2000), can_post=True)
    env.scheduler.run_pending()
    assert search.sources == ("rightmove",)
    assert search.name == "Camden, London, United Kingdom · up to £2,000"
    assert uk.locations == [{"at": CAMDEN.display}] and env.zillow.placed == []


def test_a_uk_search_reads_rightmove_and_zoopla_by_default(env):
    add_source(env, "rightmove")
    add_source(env, "zoopla")
    search, _ = env.service.create(1, 10, 99, NewSearch(CAMDEN.display), can_post=True)
    assert search.sources == ("rightmove", "zoopla")
    with pytest.raises(SearchError) as refused:
        env.service.create(1, 10, 99, NewSearch(CAMDEN.display, sources=("zillow",)), can_post=True)
    assert str(refused.value) == ("Zillow can't search Camden, London, United Kingdom. "
                                  "Pick from: Rightmove, Zoopla.")


def test_a_source_from_another_country_is_refused_with_the_valid_ones(env, uk):
    with pytest.raises(SearchError) as refused:
        env.service.create(1, 10, 99, NewSearch(CAMDEN.display, sources=("zillow",)), can_post=True)
    assert str(refused.value) == "Zillow can't search Camden, London, United Kingdom. Pick from: Rightmove."
    assert env.service.statuses(1) == []


def test_an_area_edit_to_another_country_switches_sources_and_reseeds_quietly(env, uk):
    env.zillow.pages = [[make_listing("a")]]
    uk.pages = [[make_listing("b", source="rightmove")]]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    edited, pending = env.service.edit(1, "Home", SearchChanges(area=CAMDEN.display))
    env.scheduler.run_pending()
    assert edited.sources == ("rightmove",)
    assert [(r.source, r.seeded) for r in pending.wait(0).results] == [("rightmove", True)]
    assert env.notifiers[10].listings == []


def test_the_reply_notes_filters_a_source_cannot_apply(env, uk):
    uk.filters = frozenset({Filter.PRICE, Filter.BEDS})
    _, pending = env.service.create(1, 10, 99, NewSearch(CAMDEN.display, max_price=2000, min_beds=2,
                                                         home_type=HomeType.HOUSE, pets=Pets.CATS),
                                    can_post=True)
    assert pending.notes == (
        "Rightmove can't filter by home type or pets, so you'll see all of its matches.",)
    _, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL", pets=Pets.CATS), can_post=True)
    assert pending.notes == ()


OLD_CRITERIA = json.dumps({
    "area": {"display": "Chicago, IL", "kind": "city", "zillow_region_id": 17426, "zillow_region_type": 6,
             "lat": 41.8781, "lon": -87.6298, "craigslist_site": "chicago", "craigslist_radius_miles": 10},
    "min_price": None, "max_price": 2400, "min_beds": None, "min_baths": None,
    "home_type": "any", "pets": "none",
})


def test_a_search_saved_before_countries_loads_as_us_and_does_not_reseed(env):
    env.store.add_search(make_search("old", name="Old"))
    env.store.save_listing("old", make_listing("a"))
    env.store.mark_seeded("old", "zillow")
    with env.store.db:  # as the previous release saved it
        env.store.db.execute("UPDATE searches SET criteria = ?", (OLD_CRITERIA,))
    env.zillow.pages = [[make_listing("a"), make_listing("b")]]
    env.service.rand = lambda lo, hi: 0
    env.service.load()
    env.scheduler.run_pending()
    old = env.service.find(1, "Old")
    assert old.criteria.area == CHICAGO and old.criteria.max_price == 2400
    assert env.zillow.locations == [{"region_id": 17426, "region_type": 6}]
    assert env.notifiers[10].listings == [("b", None)]
    assert env.service.statuses(1)[0].listings == 2


def test_the_sources_choices_follow_the_country_of_the_area_being_typed(env, uk):
    add_source(env, "craigslist")
    assert env.service.source_choices(1, area=CAMDEN.display) == [
        ("All sites", "all"), ("Rightmove", "rightmove")]
    assert env.service.source_choices(1, area="Lincoln Park, IL, United States") == [
        ("All sites", "all"), ("Zillow", "zillow"), ("Craigslist", "craigslist")]


def test_with_no_area_typed_yet_the_sources_choices_offer_every_site(env, uk):
    add_source(env, "craigslist")
    assert env.service.source_choices(1, area=None) == [
        ("All sites", "all"), ("Zillow", "zillow"), ("Craigslist", "craigslist"), ("Rightmove", "rightmove")]
    assert env.service.source_choices(1, area="Lon") == env.service.source_choices(1, area=None)


def test_an_area_typed_without_its_country_finds_it_from_earlier_suggestions(env, uk):
    add_source(env, "craigslist")
    env.areas.cache["Camden"] = CAMDEN  # the area autocomplete already looked "Camden" up
    assert env.service.source_choices(1, area="Camden") == [("All sites", "all"), ("Rightmove", "rightmove")]
    assert env.service.source_choices(1, area="Camde") == [
        ("All sites", "all"), ("Zillow", "zillow"), ("Craigslist", "craigslist"), ("Rightmove", "rightmove")]


def test_editing_without_a_new_area_offers_the_sources_of_the_searchs_area(env, uk):
    env.service.create(1, 10, 99, NewSearch(CAMDEN.display, name="Flat"), can_post=True)
    assert env.service.source_choices(1, area=None, search="flat") == [
        ("All sites", "all"), ("Rightmove", "rightmove")]
    assert env.service.source_choices(1, area="Chicago, IL, United States", search="Flat") == [
        ("All sites", "all"), ("Zillow", "zillow")]
    assert env.service.source_choices(1, area=None, search="nope") == [
        ("All sites", "all"), ("Zillow", "zillow"), ("Rightmove", "rightmove")]


def test_the_sources_choices_narrow_to_what_is_typed_in_the_option(env, uk):
    assert env.service.source_choices(1, area=None, current="RIGHT") == [("Rightmove", "rightmove")]


def test_a_site_typed_by_name_instead_of_picked_works(env, uk):
    search, _ = env.service.create(1, 10, 99, NewSearch(CAMDEN.display, sources=("Rightmove",)),
                                   can_post=True)
    assert search.sources == ("rightmove",)
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", sources=("all sites",)),
                                   can_post=True)
    assert search.sources == ("zillow",)


def test_check_notes_the_filters_a_source_cannot_apply(env, uk):
    uk.filters = frozenset({Filter.PRICE})
    env.service.create(1, 10, 99, NewSearch(CAMDEN.display, name="Flat", pets=Pets.CATS), can_post=True)
    _, pending = env.service.check(1, "Flat")
    assert pending.notes == ("Rightmove can't filter by pets, so you'll see all of its matches.",)


PARIS = Area("Paris, Ile-de-France, France", "city", "FR", 48.8589, 2.3470)


class FrenchAreas(FakeAreas):
    """Also knows Paris, France (not placed on any Source yet)."""

    def resolve(self, text: str) -> Area:
        return PARIS if text == PARIS.display else super().resolve(text)


def test_a_search_in_france_reads_bienici_with_prices_in_euros(env, fixture_html):
    bienici = BieniciSource(FakeHttp(fixture_html("bienici/paris_flats_rent.json")), now=lambda: AT,
                            lookup_http=FakeHttp(fixture_html("bienici/suggest_paris.json")))
    env.service.sources = {**env.service.sources, "bienici": bienici}
    env.service.runner = SearchRunner(env.service.sources, env.store, lambda s: env.notifiers[s.channel_id])
    env.service.areas = FrenchAreas()
    search, pending = env.service.create(1, 10, 99, NewSearch(PARIS.display, max_price=1500, min_beds=2,
                                                              pets=Pets.CATS), can_post=True)
    env.scheduler.run_pending()
    assert search.sources == ("bienici",)
    assert search.name == "Paris, Ile-de-France, France · 2+ bd · up to €1,500 · cats"
    assert search.criteria.area.locations == {"bienici": {"zone_ids": "-7444", "slug": "paris-75000"}}
    assert pending.notes == ("Bien'ici can't filter by pets, so you'll see all of its matches.",)
    outcome = pending.wait(0)
    assert [(r.source, r.total, r.seeded) for r in outcome.results] == [("bienici", 1365, True)]
    assert env.notifiers[10].listings == []


BERLIN = Area("Berlin, Berlin, Germany", "city", "DE", 52.5170, 13.3889)


class GermanAreas(FakeAreas):
    """Also knows Berlin, Germany (not placed on any Source yet)."""

    def resolve(self, text: str) -> Area:
        return BERLIN if text == BERLIN.display else super().resolve(text)


def test_a_search_in_germany_reads_kleinanzeigen_with_beds_as_rooms_and_prices_in_euros(env, fixture_html):
    run_http = FakeHttp(fixture_html("kleinanzeigen/berlin_3_rooms_newest.html"))
    kleinanzeigen = KleinanzeigenSource(run_http, now=lambda: AT,
                                        lookup_http=FakeHttp(fixture_html("kleinanzeigen/suggest_berlin.json")))
    env.service.sources = {**env.service.sources, "kleinanzeigen": kleinanzeigen}
    env.service.runner = SearchRunner(env.service.sources, env.store, lambda s: env.notifiers[s.channel_id])
    env.service.areas = GermanAreas()
    search, pending = env.service.create(1, 10, 99, NewSearch(BERLIN.display, max_price=1500, min_beds=2,
                                                              min_baths=1), can_post=True)
    env.scheduler.run_pending()
    assert search.sources == ("kleinanzeigen",)
    assert search.name == "Berlin, Berlin, Germany · 2+ bd · 1+ ba · up to €1,500"
    assert search.criteria.area.locations == {"kleinanzeigen": {"location_id": 3331, "slug": "berlin"}}
    assert pending.notes == ("Kleinanzeigen searches flats unless you pick House.",
                             "Kleinanzeigen can't filter by baths, so you'll see all of its matches.")
    outcome = pending.wait(0)
    assert [(r.source, r.total, r.seeded) for r in outcome.results] == [("kleinanzeigen", 1732, True)]
    assert run_http.requested[0] == (
        "https://www.kleinanzeigen.de/s-wohnung-mieten/berlin/sortierung:neuste/anzeige:angebote/preis::1500/"
        "c203l3331+wohnung_mieten.zimmer_d:3%2C")
    assert env.notifiers[10].listings == []


def test_a_source_that_searches_a_broader_area_says_so_in_every_reply(env, uk):
    uk.place_note = "Rightmove doesn't know Camden, so it searches North London."
    _, pending = env.service.create(1, 10, 99, NewSearch(CAMDEN.display, name="Flat"), can_post=True)
    assert pending.notes == ("Rightmove doesn't know Camden, so it searches North London.",)
    _, pending = env.service.edit(1, "Flat", SearchChanges(max_price=2000))
    assert pending.notes == ("Rightmove doesn't know Camden, so it searches North London.",)
    _, pending = env.service.check(1, "Flat")
    assert pending.notes == ("Rightmove doesn't know Camden, so it searches North London.",)
    assert uk.placed == [CAMDEN.display]


def test_a_home_type_a_source_cannot_match_exactly_gets_its_note(env, uk):
    uk.home_type_notes = {
        HomeType.TOWNHOME: "Rightmove can't tell townhomes from houses, so you'll see all houses."}
    _, pending = env.service.create(1, 10, 99, NewSearch(CAMDEN.display, home_type=HomeType.TOWNHOME,
                                                         name="Flat"), can_post=True)
    assert pending.notes == ("Rightmove can't tell townhomes from houses, so you'll see all houses.",)
    _, pending = env.service.edit(1, "Flat", SearchChanges(home_type=HomeType.HOUSE))
    assert pending.notes == ()

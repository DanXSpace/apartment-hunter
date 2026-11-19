from datetime import timedelta

from apartment_hunter.bot.replies import (
    checked_reply,
    checking_reply,
    confirm_delete_reply,
    created_reply,
    deleted_reply,
    edited_reply,
    first_check_cancelled_reply,
    help_reply,
    invite_url,
    paused_reply,
    resumed_reply,
    statuses_reply,
)
from apartment_hunter.runner import RunOutcome, SourceResult
from apartment_hunter.service import Limits, SearchStatus
from conftest import AT, make_search

SEARCH = make_search(name="Chicago 2BR", sources=("zillow", "craigslist"))


def ok(source: str, total: int, more: bool = False) -> SourceResult:
    return SourceResult(source, True, True, min(total, 40), total, more, None)


def failed(source: str, error: str) -> SourceResult:
    return SourceResult(source, False, False, 0, None, False, error)


def test_checking_reply_names_the_sources():
    assert checking_reply(SEARCH) == 'Checking Zillow and Craigslist for "Chicago 2BR"…'


def test_created_reply_when_everything_worked():
    outcome = RunOutcome("s1", (ok("zillow", 200), ok("craigslist", 14)), 0)
    assert created_reply(SEARCH, outcome, 15) == (
        '✅ Watching "Chicago 2BR". 214 places match right now. '
        "I'll post new ones and price drops in this channel.")


def test_created_reply_suggests_narrowing_a_broad_search():
    outcome = RunOutcome("s1", (ok("zillow", 900, more=True),), 0)
    assert created_reply(SEARCH, outcome, 15).endswith(
        "price drops are only caught among the newest ones. Narrow it with /search edit to catch them all.")


def test_created_reply_when_one_source_failed():
    error = "Craigslist answered with an error (HTTP 500)."
    outcome = RunOutcome("s1", (ok("zillow", 1), failed("craigslist", error)), 0)
    assert created_reply(SEARCH, outcome, 15) == (
        '✅ Watching "Chicago 2BR". 1 place matches on Zillow right now. Craigslist didn\'t work yet '
        "(Craigslist answered with an error (HTTP 500).); I'll keep trying.")


def test_created_reply_when_everything_failed():
    outcome = RunOutcome("s1", (failed("zillow", "Zillow is blocking requests."),), 0)
    assert created_reply(SEARCH, outcome, 15) == (
        '⚠️ I saved "Chicago 2BR", but couldn\'t check it yet: Zillow is blocking requests. '
        "I'll keep trying every 15 minutes and post here once it works.")


def test_created_reply_while_the_first_check_is_still_going():
    assert created_reply(SEARCH, None, 15) == (
        '⏳ Saved "Chicago 2BR". The first check is taking a while; I\'ll post here once it\'s done.')


def test_statuses_reply_lines_and_empty_server():
    status = SearchStatus(SEARCH, "Needs attention", "Zillow: blocked", AT - timedelta(minutes=12), 214)
    assert statuses_reply([status], AT) == (
        "**Chicago 2BR** · Needs attention (Zillow: blocked) · last checked 12 min ago · 214 seen · <#10>")
    assert statuses_reply([], AT) == "No searches in this server yet. Start one with /search new."


def test_invite_url():
    assert invite_url(42) == ("https://discord.com/oauth2/authorize?client_id=42"
                              "&scope=bot+applications.commands&permissions=19456")


def test_help_reply_states_the_owner_s_limits():
    text = help_reply(Limits(per_server=3, total=12), 20)
    assert "Up to 3 searches per server, and 12 in total" in text
    assert "about every 20 minutes" in text


def test_edited_reply_says_when_it_takes_a_fresh_look():
    assert edited_reply(SEARCH, True) == (
        'Updated "Chicago 2BR". Its criteria changed, so it\'s taking a fresh look; '
        "no alerts for what's already listed.")
    assert edited_reply(SEARCH, False) == 'Updated "Chicago 2BR".'


def test_paused_resumed_and_deleted_replies():
    assert paused_reply(SEARCH) == 'Paused "Chicago 2BR". Use /search resume to start it again.'
    assert resumed_reply(SEARCH) == 'Resumed "Chicago 2BR".'
    assert deleted_reply("Chicago 2BR") == 'Deleted "Chicago 2BR".'


def test_confirm_delete_reply_warns_it_cannot_be_undone():
    assert confirm_delete_reply(SEARCH) == (
        'Delete "Chicago 2BR" and everything it has seen? This can\'t be undone.')


def test_first_check_cancelled_reply_says_the_search_was_deleted():
    assert first_check_cancelled_reply(SEARCH) == (
        '"Chicago 2BR" was deleted before its first check finished.')


def test_checked_reply_counts_alerts_and_names_failed_sources():
    assert checked_reply(SEARCH, RunOutcome("s1", (ok("zillow", 3), ok("craigslist", 1)), 1)) == (
        'Checked "Chicago 2BR": 1 new alert posted.')
    outcome = RunOutcome("s1", (ok("zillow", 3), failed("craigslist", "Craigslist is down.")), 2)
    assert checked_reply(SEARCH, outcome) == (
        'Checked "Chicago 2BR": 2 new alerts posted. Craigslist didn\'t work (Craigslist is down.)')
    assert checked_reply(SEARCH, None) == (
        'Still checking "Chicago 2BR"; any alerts will show up in the channel.')

from datetime import timedelta

from apartment_hunter.bot.replies import checking_reply, created_reply, help_reply, invite_url, statuses_reply
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

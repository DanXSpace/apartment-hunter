"""What the bot says in reply to commands: pure functions with no Discord import, so tests pin the copy."""

from __future__ import annotations

from datetime import datetime

from apartment_hunter.models import Search
from apartment_hunter.runner import RunOutcome
from apartment_hunter.service import Limits, SearchError, SearchStatus
from apartment_hunter.sources import SOURCE_NAMES
from apartment_hunter.text import join_and, places_match, time_ago

PERMISSIONS = 19456  # View Channel + Send Messages + Embed Links

BROAD_SEARCH = (
    " That's more than one page of results, so price drops are only caught among the newest ones."
    " Narrow it with /search edit to catch them all."
)


def _names(keys: tuple[str, ...] | list[str]) -> str:
    return join_and([SOURCE_NAMES.get(key, key) for key in keys])


def checking_reply(search: Search) -> str:
    return f'Checking {_names(search.sources)} for "{search.name}"…'


def created_reply(search: Search, outcome: RunOutcome | None, interval_minutes: float) -> str:
    """The reply to /search new once its Seeding run finished (or None: it's still going)."""
    if outcome is None:
        return (f'⏳ Saved "{search.name}". '
                "The first check is taking a while; I'll post here once it's done.")
    worked = [r for r in outcome.results if r.ok]
    failed = [r for r in outcome.results if not r.ok]
    matches = sum(r.total if r.total is not None else r.found for r in worked)
    errors = "; ".join([r.error or "" for r in failed] + ([outcome.error] if outcome.error else []))
    if not worked:
        return (f'⚠️ I saved "{search.name}", but couldn\'t check it yet: {errors} '
                f"I'll keep trying every {interval_minutes:g} minutes and post here once it works.")
    if failed:
        return (f'✅ Watching "{search.name}". {places_match(matches)} on '
                f"{_names([r.source for r in worked])} right now. "
                f"{_names([r.source for r in failed])} didn't work yet ({errors}); I'll keep trying.")
    text = (f'✅ Watching "{search.name}". {places_match(matches)} right now. '
            "I'll post new ones and price drops in this channel.")
    if any(r.more_pages for r in outcome.results):
        text += BROAD_SEARCH
    return text


def edited_reply(search: Search, reseeding: bool) -> str:
    if reseeding:
        return (f'Updated "{search.name}". Its criteria changed, so it\'s taking a fresh look; '
                "no alerts for what's already listed.")
    return f'Updated "{search.name}".'


def paused_reply(search: Search) -> str:
    return f'Paused "{search.name}". Use /search resume to start it again.'


def resumed_reply(search: Search) -> str:
    return f'Resumed "{search.name}".'


def confirm_delete_reply(search: Search) -> str:
    return f'Delete "{search.name}" and everything it has seen? This can\'t be undone.'


def deleted_reply(name: str) -> str:
    return f'Deleted "{name}".'


def checked_reply(search: Search, outcome: RunOutcome | None) -> str:
    """The reply to /search check once its Run finished (or None: it's still going)."""
    if outcome is None:
        return f'Still checking "{search.name}"; any alerts will show up in the channel.'
    n = outcome.alerts_sent
    text = f'Checked "{search.name}": {n} new {"alert" if n == 1 else "alerts"} posted.'
    failed = [r for r in outcome.results if not r.ok]
    errors = [r.error or "" for r in failed] + ([outcome.error] if outcome.error else [])
    if failed:
        text += f" {_names([r.source for r in failed])} didn't work ({'; '.join(errors)})"
    elif outcome.error:
        text += f" {outcome.error}"
    return text


def first_check_cancelled_reply(search: Search) -> str:
    """The end of /search new's reply when the Search was deleted before its Seeding run finished."""
    return f'"{search.name}" was deleted before its first check finished.'


def statuses_reply(statuses: list[SearchStatus], now: datetime) -> str:
    """The reply to /search list: one line per Search in this server."""
    if not statuses:
        return "No searches in this server yet. Start one with /search new."
    return "\n".join(_status_line(status, now) for status in statuses)


def _status_line(status: SearchStatus, now: datetime) -> str:
    search = status.search
    reason = f" ({status.reason})" if status.reason else ""
    checked = time_ago((now - status.last_run).total_seconds()) if status.last_run else "never"
    return (f"**{search.name}** · {status.state}{reason} · last checked {checked} · "
            f"{status.listings} seen · <#{search.channel_id}>")


def help_reply(limits: Limits, interval_minutes: float) -> str:
    """The reply to /search help."""
    return "\n".join([
        "**Apartment Hunter** watches Zillow for rentals that match what you ask for and posts new "
        "listings and price drops in the channel where you started the search.",
        "",
        "**Commands**",
        "• `/search new` starts a search here. Give it an area (a city, neighborhood or ZIP code) and, "
        "if you like, a price range, bedrooms, bathrooms, home type, pets and a name. "
        "The first check only takes note of what's already listed; after that, new places get posted.",
        "• `/search list` shows this server's searches and how they're doing.",
        "• `/search edit` changes a search. Changing what it looks for starts it fresh, quietly; "
        "renaming it doesn't.",
        "• `/search pause` and `/search resume` stop and restart a search's checks.",
        "• `/search delete` deletes a search (you'll be asked to confirm).",
        "• `/search check` checks a search right now.",
        "• `/search help` shows this message.",
        "",
        "**Limits**",
        f"• Up to {limits.per_server} searches per server, and {limits.total} in total for the whole bot.",
        f"• Each search is checked about every {interval_minutes:g} minutes.",
        f"• `/search check` works once every {limits.check_gap_minutes:g} minutes per search.",
        "",
        "**What the status means**",
        "• **Healthy**: the last check worked.",
        "• **Running**: it's being checked right now.",
        "• **Paused**: it isn't being checked until someone resumes it.",
        "• **Needs attention**: something went wrong, like a site not answering or me losing access to "
        "its channel. The reason is shown next to it.",
        "• **Not run yet**: its first check hasn't happened yet.",
    ])


def invite_url(application_id: int) -> str:
    """The link that adds the bot to a server, with only the permissions it needs."""
    return (f"https://discord.com/oauth2/authorize?client_id={application_id}"
            f"&scope=bot+applications.commands&permissions={PERMISSIONS}")


def refusal(e: SearchError) -> str:
    return str(e)

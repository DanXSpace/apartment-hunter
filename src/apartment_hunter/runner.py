from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial

from apartment_hunter.models import Listing, Search
from apartment_hunter.notifiers import ChannelGone, Notifier, NotifyError
from apartment_hunter.sources import SOURCE_NAMES, ScrapeError, Source, SourcePage
from apartment_hunter.storage import SourceHealth, Store
from apartment_hunter.text import places_match

log = logging.getLogger(__name__)


def summary(new: int, drops: int, source: str, results_url: str) -> str:
    """The message for the Alerts past the burst cap: `new` new listings and `drops` price drops."""
    count = new + drops
    if not drops:
        what = "new place" if count == 1 else "new places"
    elif not new:
        what = "price drop" if count == 1 else "price drops"
    else:
        what = "new places and price drops"
    return f"…and {count} more {what} on {SOURCE_NAMES[source]}: {results_url}"


def broken_text(source: str, search_name: str, streak: int, reason: str, working: list[str]) -> str:
    name = SOURCE_NAMES[source]
    text = f"⚠️ {name} hasn't worked for **{search_name}** in {streak} checks in a row: {reason}"
    if working:
        names = " and ".join(SOURCE_NAMES[key] for key in working)
        text += f" {names} {'is' if len(working) == 1 else 'are'} still being checked."
    return text


def recovered_text(source: str, search_name: str) -> str:
    return f"✅ {SOURCE_NAMES[source]} is working again for **{search_name}**."


def working_now_text(source: str, search_name: str, matches: int) -> str:
    return (f"✅ {SOURCE_NAMES[source]} is working for **{search_name}** now: {places_match(matches)}. "
            "I'll post new ones here.")


@dataclass(frozen=True)
class SourceResult:
    """How one Source did in a Run. `seeded` is True when this Run was its (successful) Seeding run."""

    source: str
    ok: bool
    seeded: bool
    found: int
    total: int | None
    more_pages: bool
    error: str | None


@dataclass(frozen=True)
class RunOutcome:
    """How a Run went. `error` is a plain-language reason when the Run failed unexpectedly.
    `superseded` is True when the Search was edited or deleted during the Run, which then kept nothing."""

    search_id: str
    results: tuple[SourceResult, ...]
    alerts_sent: int
    channel_gone: bool = False
    error: str | None = None
    superseded: bool = False


class _Superseded(Exception):
    """The Search's Criteria or Sources changed, or it was deleted, while it was being run."""


class SearchRunner:
    """Runs a Search over each of its Sources: seeds new ones silently, alerts on the rest."""

    def __init__(
        self,
        sources: Mapping[str, Source],
        store: Store,
        notifier_for: Callable[[Search], Notifier],
        *,
        failure_alert_threshold: int = 3,
        burst_cap: int = 10,
        seeding_pages: int = 3,
    ):
        self._sources = sources
        self._store = store
        self._notifier_for = notifier_for
        self._threshold = failure_alert_threshold
        self._burst_cap = burst_cap
        self._seeding_pages = seeding_pages

    def run(self, search: Search) -> RunOutcome:
        """Run `search`. If it is edited (Criteria or Sources) or deleted meanwhile, the Run stops
        without keeping anything and its outcome is `superseded`: the edit wins."""
        try:
            return self._run(search)
        except _Superseded:
            log.info("[%s] Edited or deleted during its Run; nothing from the Run was kept", search.name)
            return RunOutcome(search.id, (), 0, superseded=True)

    def _run(self, search: Search) -> RunOutcome:
        results: list[SourceResult] = []
        candidates: list[tuple[Listing, int | None]] = []
        before: dict[str, SourceHealth] = {}
        for key in search.sources:
            before[key] = self._store.source_health(search.id, key)
            seeding = not before[key].seeded
            try:
                listings, first = self._fetch(search, key, seeding=seeding)
            except ScrapeError as error:
                with self._unchanged(search):
                    self._store.record_failure(search.id, key, str(error))
                log.warning("[%s] %s failed: %s", search.name, key, error)
                results.append(SourceResult(key, False, False, 0, None, False, str(error)))
                continue
            with self._unchanged(search):
                self._store.record_success(search.id, key)
                if seeding:
                    for listing in listings:
                        self._store.save_listing(search.id, listing)
                    self._store.mark_seeded(search.id, key)
                else:
                    candidates += self._compare(search.id, listings)
            results.append(SourceResult(key, True, seeding, len(listings), first.total, first.has_more, None))

        notifier = self._notifier_for(search)
        sent = 0
        try:
            for listing, previous_price in candidates[: self._burst_cap]:
                alert = partial(notifier.send_listing, listing, search.name, previous_price)
                if self._deliver(search, alert):
                    with self._unchanged(search):
                        self._store.save_listing(search.id, listing)
                    sent += 1
            for key in search.sources:
                rest = [(listing, previous) for listing, previous in candidates[self._burst_cap:]
                        if listing.source == key]
                drops = sum(previous is not None for _, previous in rest)
                url = self._sources[key].results_url(search.criteria)
                message = summary(len(rest) - drops, drops, key, url)
                if rest and self._deliver(search, partial(notifier.send_text, message)):
                    with self._unchanged(search):
                        for listing, _ in rest:
                            self._store.save_listing(search.id, listing)
            for result in results:
                self._send_health(search, notifier, result, before[result.source], results)
        except ChannelGone:
            log.warning("[%s] Its channel is gone; stopping the Run", search.name)
            return RunOutcome(search.id, tuple(results), sent, channel_gone=True)
        finally:
            with self._store.unchanged(search) as same:
                if same:
                    self._store.record_run(search.id)
        return RunOutcome(search.id, tuple(results), sent)

    @contextmanager
    def _unchanged(self, search: Search) -> Iterator[None]:
        """Make the writes inside the block only while `search` is unchanged; else stop the Run."""
        with self._store.unchanged(search) as same:
            if not same:
                raise _Superseded
            yield

    def _fetch(self, search: Search, key: str, *, seeding: bool) -> tuple[list[Listing], SourcePage]:
        """Read one Source's newest page, or up to `seeding_pages` pages for a Seeding run.
        Returns every Listing read and the first page."""
        source = self._sources[key]
        first = source.fetch(search.criteria, 1)
        listings = list(first.listings)
        page, more = 1, first.has_more
        while seeding and more and page < self._seeding_pages:
            page += 1
            next_page = source.fetch(search.criteria, page)
            listings += next_page.listings
            more = next_page.has_more
        return listings, first

    def _send_health(self, search: Search, notifier: Notifier, result: SourceResult,
                     before: SourceHealth, results: list[SourceResult]) -> None:
        """Send this Source's Health alert, recovery or "working now" message, if one is due."""
        key = result.source
        if not result.ok:
            streak = before.consecutive_failures + 1
            if streak < self._threshold or before.broken_alert_sent:
                return
            working = [r.source for r in results if r.ok]
            message = broken_text(key, search.name, streak, result.error or "", working)
            if self._deliver(search, partial(notifier.send_text, message)):
                with self._unchanged(search):
                    self._store.mark_broken_alert_sent(search.id, key)
        elif before.broken_alert_sent:
            self._deliver(search, partial(notifier.send_text, recovered_text(key, search.name)))
        elif result.seeded and before.consecutive_failures:
            matches = result.total if result.total is not None else result.found
            self._deliver(search, partial(notifier.send_text, working_now_text(key, search.name, matches)))

    def _deliver(self, search: Search, send: Callable[[], None]) -> bool:
        """Send one message; False when Discord didn't accept it. ChannelGone propagates, and nothing
        is sent once the Search was edited or deleted."""
        with self._unchanged(search):
            pass
        try:
            send()
        except ChannelGone:
            raise
        except NotifyError as error:
            log.warning("[%s] Message not delivered: %s", search.name, error)
            return False
        return True

    def _compare(self, search_id: str, listings: list[Listing]) -> list[tuple[Listing, int | None]]:
        """Alert candidates as (listing, previous price for a price drop); saves the unchanged ones."""
        candidates: list[tuple[Listing, int | None]] = []
        for listing in listings:
            seen = self._store.get_listing(search_id, listing.source, listing.listing_id)
            if seen is None:
                candidates.append((listing, None))
            elif (seen.price_min is not None and listing.price_min is not None
                  and listing.price_min < seen.price_min):
                candidates.append((listing, seen.price_min))
            else:
                self._store.save_listing(search_id, listing)
        return candidates

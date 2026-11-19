from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial

from apartment_hunter.models import Listing, Search
from apartment_hunter.notifiers import ChannelGone, Notifier, NotifyError
from apartment_hunter.sources import SOURCE_NAMES, ScrapeError, Source
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
    """How a Run went. `error` is a plain-language reason when the Run failed unexpectedly."""

    search_id: str
    results: tuple[SourceResult, ...]
    alerts_sent: int
    channel_gone: bool = False
    error: str | None = None


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
        results: list[SourceResult] = []
        candidates: list[tuple[Listing, int | None]] = []
        before: dict[str, SourceHealth] = {}
        for key in search.sources:
            before[key] = self._store.source_health(search.id, key)
            try:
                result, found = self._read(search, key, seeding=not before[key].seeded)
            except ScrapeError as error:
                self._store.record_failure(search.id, key, str(error))
                log.warning("[%s] %s failed: %s", search.name, key, error)
                results.append(SourceResult(key, False, False, 0, None, False, str(error)))
                continue
            self._store.record_success(search.id, key)
            candidates += found
            results.append(result)

        notifier = self._notifier_for(search)
        sent = 0
        try:
            for listing, previous_price in candidates[: self._burst_cap]:
                alert = partial(notifier.send_listing, listing, search.name, previous_price)
                if self._deliver(search, alert):
                    self._store.save_listing(search.id, listing)
                    sent += 1
            for key in search.sources:
                rest = [(listing, previous) for listing, previous in candidates[self._burst_cap:]
                        if listing.source == key]
                drops = sum(previous is not None for _, previous in rest)
                url = self._sources[key].results_url(search.criteria)
                message = summary(len(rest) - drops, drops, key, url)
                if rest and self._deliver(search, partial(notifier.send_text, message)):
                    for listing, _ in rest:
                        self._store.save_listing(search.id, listing)
            for result in results:
                self._send_health(search, notifier, result, before[result.source], results)
        except ChannelGone:
            log.warning("[%s] Its channel is gone; stopping the Run", search.name)
            return RunOutcome(search.id, tuple(results), sent, channel_gone=True)
        finally:
            self._store.record_run(search.id)
        return RunOutcome(search.id, tuple(results), sent)

    def _read(
        self, search: Search, key: str, *, seeding: bool
    ) -> tuple[SourceResult, list[tuple[Listing, int | None]]]:
        """Read one Source: seed it silently, or return its Alert candidates."""
        source = self._sources[key]
        first = source.fetch(search.criteria, 1)
        listings = list(first.listings)
        candidates: list[tuple[Listing, int | None]] = []
        if seeding:
            page, more = 1, first.has_more
            while more and page < self._seeding_pages:
                page += 1
                next_page = source.fetch(search.criteria, page)
                listings += next_page.listings
                more = next_page.has_more
            for listing in listings:
                self._store.save_listing(search.id, listing)
            self._store.mark_seeded(search.id, key)
        else:
            candidates = self._compare(search.id, listings)
        return SourceResult(key, True, seeding, len(listings), first.total, first.has_more, None), candidates

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
                self._store.mark_broken_alert_sent(search.id, key)
        elif before.broken_alert_sent:
            self._deliver(search, partial(notifier.send_text, recovered_text(key, search.name)))
        elif result.seeded and before.consecutive_failures:
            matches = result.total if result.total is not None else result.found
            self._deliver(search, partial(notifier.send_text, working_now_text(key, search.name, matches)))

    @staticmethod
    def _deliver(search: Search, send: Callable[[], None]) -> bool:
        """Send one message; False when Discord didn't accept it. ChannelGone propagates."""
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

from __future__ import annotations

import logging
from collections.abc import Callable

from apartment_hunter.config import Search
from apartment_hunter.notifiers import Notifier, NotifyError
from apartment_hunter.scrapers import ScrapeError, Scraper
from apartment_hunter.storage import Store

log = logging.getLogger(__name__)


class SearchRunner:
    def __init__(
        self,
        scraper: Scraper,
        store: Store,
        notifier_for: Callable[[Search], Notifier],
        failure_alert_threshold: int = 3,
        alert_existing: bool = False,
    ):
        self.scraper = scraper
        self.store = store
        self.notifier_for = notifier_for
        self.failure_alert_threshold = failure_alert_threshold
        self.alert_existing = alert_existing

    def run(self, search: Search) -> None:
        notifier = self.notifier_for(search)
        log.info("[%s] Scraping", search.name)
        try:
            result = self.scraper.scrape(search.url)
        except ScrapeError as e:
            self._on_failure(search, notifier, str(e))
            return
        self._on_success(search, notifier)
        log.info("[%s] %d listings across %d page(s)",
                 search.name, len(result.listings), result.pages_fetched)

        state = self.store.search_state(search.name)
        if not state.seeded and not self.alert_existing:
            for listing in result.listings:
                self.store.save_listing(search.name, listing)
            self.store.mark_seeded(search.name)
            self._safe_text(notifier, search,
                            f"👀 Now watching **{search.name}**: {len(result.listings)} current "
                            "listings recorded. You'll be alerted about new listings and price drops.")
            return

        sent = 0
        for listing in result.listings:
            seen = self.store.get_listing(search.name, listing.listing_id)
            dropped = (seen is not None and seen.price_min is not None
                       and listing.price_min is not None and listing.price_min < seen.price_min)
            if seen is not None and not dropped:
                self.store.save_listing(search.name, listing)  # refresh last_seen / price
                continue
            try:
                notifier.send_listing(listing, search.name,
                                      previous_price=seen.price_min if seen and dropped else None)
            except NotifyError as e:
                log.error("[%s] Alert for %s not delivered (will retry next run): %s",
                          search.name, listing.listing_id, e)
                continue
            self.store.save_listing(search.name, listing)
            sent += 1
        self.store.mark_seeded(search.name)
        log.info("[%s] Sent %d alert(s)", search.name, sent)

    def _on_failure(self, search: Search, notifier: Notifier, reason: str) -> None:
        failures = self.store.record_failure(search.name)
        log.error("[%s] Run failed (%d in a row): %s", search.name, failures, reason)
        state = self.store.search_state(search.name)
        if failures >= self.failure_alert_threshold and not state.broken_alert_sent:
            if self._safe_text(notifier, search,
                               f"⚠️ **{search.name}** scraper looks broken: {failures} failed runs "
                               f"in a row. Last error: {reason}"):
                self.store.mark_broken_alert_sent(search.name)

    def _on_success(self, search: Search, notifier: Notifier) -> None:
        was_broken = self.store.search_state(search.name).broken_alert_sent
        self.store.record_success(search.name)
        if was_broken:
            self._safe_text(notifier, search, f"✅ **{search.name}** scraper recovered.")

    @staticmethod
    def _safe_text(notifier: Notifier, search: Search, message: str) -> bool:
        try:
            notifier.send_text(message)
            return True
        except NotifyError as e:
            log.error("[%s] Could not send status message: %s", search.name, e)
            return False

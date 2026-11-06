from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from apartment_hunter.config import ConfigError, load_settings
from apartment_hunter.fetchers import FetchError, make_fetcher
from apartment_hunter.notifiers import NotifyError
from apartment_hunter.notifiers.discord import DiscordNotifier
from apartment_hunter.runner import SearchRunner, run_forever
from apartment_hunter.scrapers.apartments_com import ApartmentsComScraper
from apartment_hunter.storage import Store

log = logging.getLogger("apartment_hunter")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="apartment-hunter",
        description="Watch apartments.com searches and send Discord alerts.",
    )
    parser.add_argument("url", nargs="?",
                        help="Quick start: a single apartments.com search URL (ignores [[search]])")
    parser.add_argument("-c", "--config", type=Path,
                        default=Path(os.getenv("APARTMENT_HUNTER_CONFIG", "config.toml")))
    parser.add_argument("--once", action="store_true", help="Run every search once and exit")
    parser.add_argument("--alert-existing", action="store_true",
                        help="On a search's first run, alert on every current listing")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        settings = load_settings(args.config, quick_url=args.url)
        b = settings.browser
        fetcher = make_fetcher(b.mode, b.executable, b.profile_dir, b.headless, b.extra_args)
    except (ConfigError, FetchError) as e:
        log.error("%s", e)
        return 2

    try:
        for webhook in {s.webhook_url for s in settings.searches}:
            DiscordNotifier(webhook).check()
    except NotifyError as e:
        log.error("%s", e)
        return 2

    store = Store(settings.database)
    imported = store.import_legacy_seen_file(Path("seen_listings.txt"),
                                             [s.name for s in settings.searches])
    if imported:
        log.info("Imported %d IDs from legacy seen_listings.txt", imported)

    notifiers: dict[str, DiscordNotifier] = {}
    runner = SearchRunner(
        scraper=ApartmentsComScraper(fetcher, settings.max_pages, settings.page_delay_seconds),
        store=store,
        notifier_for=lambda s: notifiers.setdefault(s.webhook_url, DiscordNotifier(s.webhook_url)),
        failure_alert_threshold=settings.failure_alert_threshold,
        alert_existing=args.alert_existing,
    )
    log.info("Watching %d search(es) with %s browser mode", len(settings.searches), b.mode)
    try:
        if args.once:
            for search in settings.searches:
                runner.run(search)
        else:
            run_forever(runner, settings.searches)
    except KeyboardInterrupt:
        log.info("Stopped")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

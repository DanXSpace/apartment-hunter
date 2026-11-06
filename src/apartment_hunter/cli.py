from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from apartment_hunter.appdata import LOG_FORMAT, AlreadyRunning, DataDir
from apartment_hunter.config import ConfigError, Settings, load_settings
from apartment_hunter.fetchers import FetchError, make_fetcher
from apartment_hunter.notifiers import NotifyError
from apartment_hunter.notifiers.discord import DiscordNotifier
from apartment_hunter.runner import SearchRunner
from apartment_hunter.scheduler import Scheduler
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
                        help="Config file (default: config.toml, or the app-data one with --app-data)")
    parser.add_argument("--app-data", action="store_true",
                        help="Use the desktop app's data directory for config, database, "
                             "browser profile and logs (-c still picks the config file)")
    parser.add_argument("--once", action="store_true", help="Run every search once and exit")
    parser.add_argument("--alert-existing", action="store_true",
                        help="On a search's first run, alert on every current listing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format=LOG_FORMAT,
                        stream=sys.stdout)

    app_data = DataDir.app_data() if args.app_data else None
    if args.config:
        config_path = args.config
    elif app_data:
        config_path = app_data.config
    else:
        config_path = Path(os.getenv("APARTMENT_HUNTER_CONFIG", "config.toml"))
    try:
        settings = load_settings(config_path, quick_url=args.url, data_dir=app_data)
    except ConfigError as e:
        log.error("%s", e)
        return 2

    # Without --app-data, the CLI's data directory is wherever its database lives.
    data = app_data or DataDir(settings.database.parent)
    try:
        with data.lock():
            handler = data.log_handler()
            logging.getLogger().addHandler(handler)
            try:
                return _run(settings, once=args.once, alert_existing=args.alert_existing)
            finally:
                logging.getLogger().removeHandler(handler)
                handler.close()
    except AlreadyRunning as e:
        log.error("%s", e)
        return 2


def _run(settings: Settings, once: bool, alert_existing: bool) -> int:
    try:
        b = settings.browser
        fetcher = make_fetcher(b.mode, b.executable, b.profile_dir, b.headless, b.extra_args)
    except FetchError as e:
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
        alert_existing=alert_existing,
    )
    log.info("Watching %d search(es) with %s browser mode", len(settings.searches), b.mode)
    crashed: list[str] = []

    def run(search):
        try:
            runner.run(search)
        except Exception:
            crashed.append(search.name)
            raise  # the scheduler logs it and carries on

    scheduler = Scheduler(run, settings.searches)
    scheduler.start()
    try:
        if once:
            scheduler.run_pending()  # every search is due at start
        else:
            scheduler.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped")
    finally:
        store.close()
    return 1 if crashed else 0


if __name__ == "__main__":
    sys.exit(main())

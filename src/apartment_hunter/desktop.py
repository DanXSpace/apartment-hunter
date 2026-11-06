"""`apartment-hunter-app`: the desktop app. Wires the real core into the web UI and serves it."""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import webbrowser

import uvicorn

from apartment_hunter.appdata import LOG_FORMAT, AlreadyRunning, DataDir
from apartment_hunter.config import BrowserSettings, Settings
from apartment_hunter.fetchers import FetchError, make_fetcher
from apartment_hunter.models import ScrapeResult
from apartment_hunter.notifiers import Notifier
from apartment_hunter.notifiers.discord import DiscordNotifier
from apartment_hunter.scrapers import ScrapeError, Scraper
from apartment_hunter.scrapers.apartments_com import ApartmentsComScraper
from apartment_hunter.storage import Store
from apartment_hunter.web import create_app, read_config

log = logging.getLogger("apartment_hunter")

HOST = "127.0.0.1"  # never reachable from other machines: the UI has no authentication
DEFAULT_PORT = 8765


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format=LOG_FORMAT,
                        stream=sys.stdout)
    data = DataDir.app_data()
    try:
        with data.lock():
            handler = data.log_handler()
            logging.getLogger().addHandler(handler)
            try:
                return _serve(data)
            finally:
                logging.getLogger().removeHandler(handler)
                handler.close()
    except AlreadyRunning as e:
        log.error("%s", e)
        return 2


def _serve(data: DataDir) -> int:
    settings = read_config(data).settings  # if unusable, the dashboard explains why
    store = Store(settings.database if settings else data.database)
    notifiers: dict[str, Notifier] = {}
    app = create_app(
        scraper=_scraper(settings, data),
        notifier_for=lambda url: notifiers.setdefault(url, DiscordNotifier(url)),
        store=store,
        data_dir=data,
    )
    scheduler = app.state.scheduler

    sock = bind(HOST, DEFAULT_PORT)
    port = sock.getsockname()[1]
    data.port_file.write_text(str(port), encoding="utf-8")
    url = f"http://{HOST}:{port}/"
    log.info("Apartment Hunter is running at %s", url)

    # uvicorn re-raises the signal that stopped it; make SIGTERM unwind like Ctrl+C so the
    # cleanup below runs instead of the process dying on the spot.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    scheduler.start_in_background()
    try:
        webbrowser.open(url)  # connections wait in the socket's backlog until uvicorn serves
        uvicorn.Server(uvicorn.Config(app, log_config=None)).run(sockets=[sock])
    finally:
        scheduler.shutdown(timeout=30)
        store.close()
        sock.close()
        data.port_file.unlink(missing_ok=True)
    return 0


def bind(host: str, port: int) -> socket.socket:
    """A listening socket on `port`, or on a free port if that one is taken."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name != "nt":  # rebind straight after a restart; on Windows this would share the port
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        log.info("Port %d is taken; using a free one", port)
        sock.bind((host, 0))
    sock.listen()
    return sock


def _scraper(settings: Settings | None, data: DataDir) -> Scraper:
    settings = settings or Settings(data.database, [], BrowserSettings(profile_dir=data.browser_profile))
    browser = settings.browser
    try:
        fetcher = make_fetcher(browser.mode, browser.executable, browser.profile_dir,
                               browser.headless, browser.extra_args)
    except FetchError as e:  # e.g. no browser installed: every run reports it instead
        return _FailingScraper(str(e))
    return ApartmentsComScraper(fetcher, settings.max_pages, settings.page_delay_seconds)


class _FailingScraper:
    def __init__(self, reason: str):
        self.reason = reason

    def scrape(self, url: str) -> ScrapeResult:
        raise ScrapeError(self.reason)


if __name__ == "__main__":
    sys.exit(main())

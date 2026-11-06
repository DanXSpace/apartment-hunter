import html
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apartment_hunter.appdata import DataDir
from apartment_hunter.models import ScrapeResult
from apartment_hunter.scrapers import ScrapeError
from apartment_hunter.storage import Store
from apartment_hunter.web import create_app
from conftest import make_listing

CONFIG = """
webhook_url = "https://discord.com/api/webhooks/1/abc"
interval_minutes = 15

[[search]]
name = "Downtown"
url = "https://www.apartments.com/downtown/"

[[search]]
name = "Uptown"
url = "https://www.apartments.com/uptown/"
interval_minutes = 30
"""


class FakeScraper:
    """Scripted results per URL: a list of listings, an exception, or a callable run mid-scrape."""

    def __init__(self):
        self.next: dict[str, list] = {}

    def scrape(self, url):
        item = self.next[url].pop(0)
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return ScrapeResult(item, 1, False)


class FakeNotifier:
    def __init__(self):
        self.listings, self.texts = [], []

    def send_listing(self, listing, search_name, previous_price=None):
        self.listings.append(listing.listing_id)

    def send_text(self, message):
        self.texts.append(message)


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 11, 5, 12, 0)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


class Env:
    def __init__(self, tmp_path, config=CONFIG):
        self.data = DataDir(tmp_path / "data")
        if config is not None:
            self.data.root.mkdir(parents=True)
            self.data.config.write_text(config, encoding="utf-8")
        self.scraper, self.notifier, self.clock = FakeScraper(), FakeNotifier(), FakeClock()
        self.store = Store(":memory:", now=self.clock)
        self.app = create_app(scraper=self.scraper, notifier_for=lambda url: self.notifier,
                              store=self.store, data_dir=self.data, clock=self.clock,
                              next_delay=lambda minutes: minutes * 60)
        self.scheduler = self.app.state.scheduler
        self.client = TestClient(self.app)

    def run_pending(self):
        self.scheduler.start()
        self.scheduler.run_pending()

    def rows(self) -> dict[str, str]:
        """The status table's rows as visible text, keyed by search name."""
        html = self.client.get("/status").text
        rows = {}
        for name in ("Downtown", "Uptown"):
            start = html.find(f">{name}<")
            if start != -1:
                end = html.find("</tr>", start)
                rows[name] = " ".join(_strip_tags(html[start:end]).split())
        return rows


def _strip_tags(html: str) -> str:
    out, inside = [], False
    for ch in html:
        if ch == "<":
            inside = True
            out.append(" ")
        elif ch == ">":
            inside = False
        elif not inside:
            out.append(ch)
    return "".join(out)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_dashboard_lists_the_configured_searches(env):
    page = env.client.get("/")
    assert page.status_code == 200
    assert "Downtown" in page.text and "Uptown" in page.text


def test_a_successful_run_shows_healthy_with_last_run_next_run_and_listing_count(env):
    env.scraper.next = {"https://www.apartments.com/downtown/": [[make_listing("a"), make_listing("b")]],
                        "https://www.apartments.com/uptown/": [[make_listing("c")]]}
    env.run_pending()
    env.clock.advance(5)
    assert env.rows() == {
        "Downtown": "Downtown Healthy 5 min ago in 10 min 2",
        "Uptown": "Uptown Healthy 5 min ago in 25 min 1",
    }


def test_a_failed_run_shows_needs_attention(env):
    env.scraper.next = {"https://www.apartments.com/downtown/": [ScrapeError("Access denied")],
                        "https://www.apartments.com/uptown/": [[make_listing("c")]]}
    env.run_pending()
    rows = env.rows()
    assert rows["Downtown"] == "Downtown Needs attention just now in 15 min 0"
    assert rows["Uptown"].startswith("Uptown Healthy")


def test_a_search_shows_running_while_its_run_is_in_progress(env):
    seen_mid_run = {}

    def scrape_downtown():
        seen_mid_run.update(env.rows())
        return [make_listing("a")]

    env.scraper.next = {"https://www.apartments.com/downtown/": [scrape_downtown],
                        "https://www.apartments.com/uptown/": [[make_listing("c")]]}
    env.run_pending()
    assert seen_mid_run["Downtown"] == "Downtown Running Never running now 0"
    assert seen_mid_run["Uptown"] == "Uptown Not run yet Never now 0"
    assert env.rows()["Downtown"].startswith("Downtown Healthy")


def test_a_paused_search_shows_paused(env):
    env.scheduler.pause("Uptown")
    assert env.rows()["Uptown"] == "Uptown Paused Never — 0"


def test_the_dashboard_polls_the_status_table_with_htmx_shipped_in_the_package(env):
    page = env.client.get("/").text
    assert 'hx-get="/status"' in page and 'hx-trigger="every' in page
    assert "/static/htmx.min.js" in page
    script = env.client.get("/static/htmx.min.js")
    assert script.status_code == 200 and "htmx" in script.text


def test_without_a_config_file_the_dashboard_says_there_are_no_searches_yet(tmp_path):
    env = Env(tmp_path, config=None)
    page = env.client.get("/")
    assert page.status_code == 200
    assert "No searches yet" in page.text


def test_a_broken_config_file_is_explained_on_the_dashboard(tmp_path):
    env = Env(tmp_path, config='[[search]]\nname = "Downtown"\n')
    page = html.unescape(env.client.get("/").text)
    assert "has a problem" in page and "both 'name' and 'url' are required" in page
    assert "No searches yet" in page


def test_a_config_with_a_webhook_but_no_searches_means_no_searches_yet(tmp_path):
    env = Env(tmp_path, config='webhook_url = "https://discord.com/api/webhooks/1/abc"\n')
    page = env.client.get("/").text
    assert "No searches yet" in page and "has a problem" not in page

"""The desktop app's local web UI: an application factory over the core."""
from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from apartment_hunter.appdata import DataDir
from apartment_hunter.config import ConfigError, Search, Settings, load_settings
from apartment_hunter.notifiers import Notifier
from apartment_hunter.runner import SearchRunner
from apartment_hunter.scheduler import Scheduler, next_delay_seconds
from apartment_hunter.scrapers import Scraper
from apartment_hunter.storage import Store

HERE = Path(__file__).parent


@dataclass(frozen=True)
class ConfigReading:
    """What the app made of the data directory's config file."""

    settings: Settings | None
    problem: str | None = None  # plain-language reason the config couldn't be used

    @property
    def searches(self) -> list[Search]:
        return self.settings.searches if self.settings else []


def read_config(data_dir: DataDir) -> ConfigReading:
    """Read the config file in the data directory. No file, or no searches in it, is not a
    problem: it means no searches yet."""
    path = data_dir.config
    try:
        if not path.exists() or not tomllib.loads(path.read_text(encoding="utf-8")).get("search"):
            return ConfigReading(None)
        return ConfigReading(load_settings(path, data_dir=data_dir))
    except (ConfigError, tomllib.TOMLDecodeError, OSError) as e:
        return ConfigReading(None, f"The config file {path} has a problem: {e}")


@dataclass(frozen=True)
class SearchStatus:
    """One row of the dashboard's status table, as the user reads it."""

    name: str
    state: str  # Healthy / Running / Paused / Needs attention / Not run yet
    last_run: str
    next_run: str
    listings: int


def _time_ago(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = round(minutes / 60)
    return f"{hours} h ago" if hours < 24 else f"{round(hours / 24)} days ago"


def _time_until(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return "now"
    return f"in {minutes} min" if minutes < 60 else f"in {round(minutes / 60)} h"


def search_status(search: Search, store: Store, scheduler: Scheduler, now: datetime) -> SearchStatus:
    state = store.search_state(search.name)
    if scheduler.current == search.name:
        label = "Running"
    elif scheduler.is_paused(search.name):
        label = "Paused"
    elif state.consecutive_failures:
        label = "Needs attention"
    elif state.last_run is None:
        label = "Not run yet"
    else:
        label = "Healthy"
    last_run = _time_ago((now - state.last_run).total_seconds()) if state.last_run else "Never"
    if label == "Running":
        next_run = "running now"
    elif label == "Paused":
        next_run = "—"
    else:
        next_run = _time_until(scheduler.next_run(search.name) - now.timestamp())
    return SearchStatus(search.name, label, last_run, next_run, store.listing_count(search.name))


def create_app(
    *,
    scraper: Scraper,
    notifier_for: Callable[[str], Notifier],
    store: Store,
    data_dir: DataDir,
    clock: Callable[[], datetime] = datetime.now,
    next_delay: Callable[[float], float] = next_delay_seconds,
) -> FastAPI:
    """Build the web app over the core.

    `notifier_for` maps a webhook URL to its Notifier. The app's Scheduler is created stopped
    and is exposed as `app.state.scheduler`: the caller starts it (in the background for the
    real app; tests drive `run_pending` directly).
    """
    config = read_config(data_dir)
    threshold = config.settings.failure_alert_threshold if config.settings else 3
    runner = SearchRunner(scraper, store, lambda s: notifier_for(s.webhook_url),
                          failure_alert_threshold=threshold)
    scheduler = Scheduler(runner.run, config.searches, clock=lambda: clock().timestamp(),
                          next_delay=next_delay)

    app = FastAPI(title="Apartment Hunter", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.scheduler = scheduler
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")

    def statuses() -> list[SearchStatus]:
        now = clock()
        return [search_status(s, store, scheduler, now) for s in config.searches]

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "dashboard.html",
                                          {"statuses": statuses(), "problem": config.problem})

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request) -> HTMLResponse:
        """The status table alone; the dashboard polls it."""
        return templates.TemplateResponse(request, "status.html", {"statuses": statuses()})

    return app

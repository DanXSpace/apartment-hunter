from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

log = logging.getLogger(__name__)

MIN_INTERVAL_MINUTES = 10


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Search:
    name: str
    url: str
    interval_minutes: float
    webhook_url: str


@dataclass(frozen=True)
class BrowserSettings:
    mode: str = "chromium"  # "chromium" or "stealth"
    executable: str | None = None
    headless: bool = False
    profile_dir: Path = Path("data/browser-profile")
    extra_args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Settings:
    database: Path
    searches: list[Search]
    browser: BrowserSettings
    max_pages: int = 10
    page_delay_seconds: tuple[float, float] = (3.0, 8.0)
    failure_alert_threshold: int = 3


def _expand(value: str, where: str) -> str:
    expanded = os.path.expandvars(value)
    if missing := re.findall(r"\$\{?(\w+)\}?", expanded):
        raise ConfigError(f"{where}: environment variable(s) not set: {', '.join(missing)}")
    return expanded


def _bool_env(name: str) -> bool | None:
    v = os.getenv(name)
    return None if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def load_settings(config_path: Path | None, quick_url: str | None = None) -> Settings:
    """Load config.toml (if present), then apply env overrides.

    `quick_url` replaces the configured searches with a single search named "default",
    so `apartment-hunter "<url>"` works with nothing but DISCORD_WEBHOOK_URL set.
    """
    raw: dict = {}
    if config_path and config_path.exists():
        with config_path.open("rb") as f:
            raw = tomllib.load(f)
    elif config_path and quick_url is None:
        raise ConfigError(f"Config file not found: {config_path} (see config.example.toml)")

    default_webhook = raw.get("webhook_url") or os.getenv("DISCORD_WEBHOOK_URL")
    default_interval = float(raw.get("interval_minutes", 15))

    if quick_url:
        raw_searches = [{"name": "default", "url": quick_url}]
    else:
        raw_searches = raw.get("search", [])
    if not raw_searches:
        raise ConfigError("No searches configured. Add a [[search]] block or pass a URL.")

    searches, names = [], set()
    for i, s in enumerate(raw_searches):
        where = f"search #{i + 1}"
        name, url = s.get("name"), s.get("url")
        if not name or not url:
            raise ConfigError(f"{where}: both 'name' and 'url' are required")
        if name in names:
            raise ConfigError(f"{where}: duplicate search name {name!r}")
        if "apartments.com" not in url:
            raise ConfigError(f"{where}: only apartments.com search URLs are supported for now")
        names.add(name)
        webhook = s.get("webhook_url") or default_webhook
        if not webhook:
            raise ConfigError(f"{where} ({name}): no webhook_url and DISCORD_WEBHOOK_URL is unset")
        interval = float(s.get("interval_minutes", default_interval))
        if interval < MIN_INTERVAL_MINUTES:
            log.warning("%s: interval %.0f min is too aggressive; using %d min",
                        name, interval, MIN_INTERVAL_MINUTES)
            interval = MIN_INTERVAL_MINUTES
        webhook = _expand(webhook, f"{where} webhook_url").strip()
        if not webhook.startswith("https://"):
            raise ConfigError(f"{where} ({name}): webhook_url is empty or not an https:// URL")
        searches.append(Search(name, url, interval, webhook))

    b = raw.get("browser", {})
    browser = BrowserSettings(
        mode=b.get("mode", "chromium"),
        executable=b.get("executable") or None,
        headless=bool(b.get("headless", False)),
        profile_dir=Path(b.get("profile_dir", "data/browser-profile")),
        extra_args=list(b.get("extra_args", [])),
    )
    if (env_headless := _bool_env("BROWSER_HEADLESS")) is not None:
        browser = replace(browser, headless=env_headless)
    if env_exe := os.getenv("BROWSER_EXECUTABLE"):
        browser = replace(browser, executable=env_exe)
    if env_args := os.getenv("BROWSER_EXTRA_ARGS"):
        browser = replace(browser, extra_args=[*browser.extra_args, *env_args.split()])
    if env_mode := os.getenv("BROWSER_MODE"):
        browser = replace(browser, mode=env_mode)
    if browser.mode not in ("chromium", "stealth"):
        raise ConfigError(f"[browser] mode must be 'chromium' or 'stealth', got {browser.mode!r}")

    sc = raw.get("scraper", {})
    delay = sc.get("page_delay_seconds", [3, 8])
    return Settings(
        database=Path(os.getenv("DATABASE_PATH") or raw.get("database", "data/apartment_hunter.db")),
        searches=searches,
        browser=browser,
        max_pages=int(sc.get("max_pages", 10)),
        page_delay_seconds=(float(delay[0]), float(delay[1])),
        failure_alert_threshold=int(raw.get("failure_alert_threshold", 3)),
    )

"""How we get HTML out of a site.

`chromium` mode launches a real, installed Chromium-family browser with the target URL as a
launch argument, so the browser navigates natively, then attaches over CDP only to *read* the
rendered page. apartments.com's bot protection rejects automation-driven navigation (Playwright
or Scrapling fetchers) but accepts this.

`stealth` mode uses Scrapling's StealthyFetcher with all of its stealth options enabled.
"""

from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

BROWSER_CANDIDATES = (
    "chromium-browser",
    "chromium",
    "google-chrome-stable",
    "google-chrome",
    "brave-browser",
)


class FetchError(Exception):
    """The page could not be fetched at all (browser crash, timeout, network error)."""


@dataclass(frozen=True)
class FetchResult:
    html: str
    status: int | None  # None when the fetcher can't observe the HTTP status


class Fetcher(Protocol):
    def fetch(self, url: str, ready_selector: str | None = None) -> FetchResult: ...


def find_browser(configured: str | None) -> str:
    if configured:
        path = shutil.which(configured) or configured
        if not Path(path).exists():
            raise FetchError(f"Configured browser not found: {configured}")
        return path
    for name in BROWSER_CANDIDATES:
        if path := shutil.which(name):
            return path
    raise FetchError(
        "No Chromium-family browser found. Install chromium or set [browser] executable in config."
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ChromiumFetcher:
    def __init__(
        self,
        executable: str | None,
        profile_dir: Path,
        headless: bool,
        extra_args: list[str] | None = None,
        load_timeout: float = 45.0,
        settle_seconds: float = 3.0,
    ):
        self.executable = find_browser(executable)
        self.profile_dir = profile_dir
        self.headless = headless
        self.extra_args = extra_args or []
        self.load_timeout = load_timeout
        self.settle_seconds = settle_seconds

    def fetch(self, url: str, ready_selector: str | None = None) -> FetchResult:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        args = [
            self.executable,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1366,900",
            *(["--headless=new"] if self.headless else []),
            *self.extra_args,
            url,
        ]
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        host = url.split("/")[2]
        try:
            ws_url = self._wait_for_devtools(port, proc)
            self._wait_for_target(port, host)
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(ws_url)
                page = self._find_page(browser, host)
                try:
                    page.wait_for_load_state("load", timeout=self.load_timeout * 1000)
                    if ready_selector:
                        page.wait_for_selector(ready_selector, timeout=self.load_timeout * 1000)
                except PlaywrightError:
                    log.debug("Timed out waiting for %s; reading what loaded", ready_selector)
                page.wait_for_timeout(self.settle_seconds * 1000)
                return FetchResult(html=page.content(), status=None)
        except PlaywrightError as e:
            raise FetchError(f"Browser error: {e}") from e
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _wait_for_devtools(self, port: int, proc: subprocess.Popen) -> str:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise FetchError(f"Browser exited immediately (code {proc.returncode})")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                    return json.load(r)["webSocketDebuggerUrl"]
            except OSError:
                time.sleep(0.5)
        raise FetchError("Browser DevTools endpoint never came up")

    def _wait_for_target(self, port: int, host: str) -> None:
        """Poll DevTools (outside Playwright) until the natively opened tab is on `host`."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                    if any(t.get("type") == "page" and host in t.get("url", "")
                           for t in json.load(r)):
                        return
            except OSError:
                pass
            time.sleep(0.5)
        raise FetchError("Browser never opened the target URL")

    def _find_page(self, browser, host: str):
        # Sync Playwright only processes browser events inside its own calls, so wait with
        # page.wait_for_timeout rather than time.sleep while the page list catches up.
        for _ in range(40):
            pages = [p for ctx in browser.contexts for p in ctx.pages]
            for page in pages:
                if host in page.url:
                    return page
            if pages:
                pages[0].wait_for_timeout(500)
            else:
                time.sleep(0.5)
        raise FetchError("Connected to the browser but could not find the target tab")


class StealthFetcher:
    """Scrapling's StealthySession driving the user's installed browser.

    A session (not the one-shot StealthyFetcher) is required for `user_data_dir` to take
    effect, so bot-protection cookies persist in the profile between runs.
    """

    def __init__(self, executable: str | None, profile_dir: Path, headless: bool,
                 extra_args: list[str] | None = None, load_timeout: float = 60.0):
        self.executable = find_browser(executable)
        self.profile_dir = profile_dir
        self.headless = headless
        self.extra_args = extra_args or []
        self.load_timeout = load_timeout

    def fetch(self, url: str, ready_selector: str | None = None) -> FetchResult:
        from scrapling.fetchers import StealthySession

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            with StealthySession(
                executable_path=self.executable,
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                extra_flags=self.extra_args or None,
                hide_canvas=True,
                block_webrtc=True,
                block_ads=True,
                google_search=True,
                network_idle=True,
                timeout=self.load_timeout * 1000,
            ) as session:
                page = session.fetch(url, wait_selector=ready_selector)
        except Exception as e:  # Scrapling surfaces several error types
            raise FetchError(f"Stealth fetch failed: {e}") from e
        return FetchResult(html=page.html_content, status=page.status)


def make_fetcher(mode: str, executable: str | None, profile_dir: Path, headless: bool,
                 extra_args: list[str]) -> Fetcher:
    if mode == "chromium":
        return ChromiumFetcher(executable, profile_dir, headless, extra_args)
    if mode == "stealth":
        # Separate profile: the two modes shouldn't share (or poison) each other's cookies.
        return StealthFetcher(executable, profile_dir.with_name(profile_dir.name + "-stealth"),
                              headless, extra_args)
    raise ValueError(f"Unknown browser mode {mode!r} (expected 'chromium' or 'stealth')")

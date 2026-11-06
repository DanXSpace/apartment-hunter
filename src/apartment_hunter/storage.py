from __future__ import annotations

import functools
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Concatenate

from apartment_hunter.models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    name TEXT PRIMARY KEY,
    seeded INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    broken_alert_sent INTEGER NOT NULL DEFAULT 0,
    last_success TEXT,
    last_run TEXT
);
CREATE TABLE IF NOT EXISTS listings (
    search_name TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    url TEXT,
    address TEXT,
    price_min INTEGER,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (search_name, listing_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass(frozen=True)
class SeenListing:
    listing_id: str
    price_min: int | None


@dataclass(frozen=True)
class SearchState:
    seeded: bool
    consecutive_failures: int
    broken_alert_sent: bool
    last_run: datetime | None = None  # when the latest run finished, successful or not


def _synchronized[**P, R](method: Callable[Concatenate[Store, P], R]) -> Callable[Concatenate[Store, P], R]:
    """Run a Store method under the Store's lock: one connection, shared across threads."""
    @functools.wraps(method)
    def wrapper(self: Store, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Store:
    """SQLite state. Safe to share between threads (the scheduler's and the web UI's)."""

    def __init__(self, path: Path | str, now: Callable[[], datetime] = datetime.now):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(searches)")}
        if "last_run" not in columns:  # databases from v0.2
            with self.db:
                self.db.execute("ALTER TABLE searches ADD COLUMN last_run TEXT")
                self.db.execute("UPDATE searches SET last_run = last_success")

    @_synchronized
    def close(self) -> None:
        self.db.close()

    # --- searches -------------------------------------------------------
    def _ensure_search(self, name: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO searches (name) VALUES (?)", (name,))

    @_synchronized
    def search_state(self, name: str) -> SearchState:
        self._ensure_search(name)
        row = self.db.execute(
            "SELECT seeded, consecutive_failures, broken_alert_sent, last_run FROM searches "
            "WHERE name = ?",
            (name,),
        ).fetchone()
        last_run = datetime.fromisoformat(row[3]) if row[3] else None
        return SearchState(bool(row[0]), row[1], bool(row[2]), last_run)

    @_synchronized
    def mark_seeded(self, name: str) -> None:
        with self.db:
            self._ensure_search(name)
            self.db.execute("UPDATE searches SET seeded = 1 WHERE name = ?", (name,))

    @_synchronized
    def record_failure(self, name: str) -> int:
        with self.db:
            self._ensure_search(name)
            self.db.execute(
                "UPDATE searches SET consecutive_failures = consecutive_failures + 1, last_run = ? "
                "WHERE name = ?",
                (self._now().isoformat(), name),
            )
        return self.search_state(name).consecutive_failures

    @_synchronized
    def record_success(self, name: str) -> None:
        now = self._now().isoformat()
        with self.db:
            self._ensure_search(name)
            self.db.execute(
                "UPDATE searches SET consecutive_failures = 0, broken_alert_sent = 0, "
                "last_success = ?, last_run = ? WHERE name = ?",
                (now, now, name),
            )

    @_synchronized
    def mark_broken_alert_sent(self, name: str) -> None:
        with self.db:
            self.db.execute("UPDATE searches SET broken_alert_sent = 1 WHERE name = ?", (name,))

    # --- listings -------------------------------------------------------
    @_synchronized
    def listing_count(self, search: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM listings WHERE search_name = ?",
                               (search,)).fetchone()[0]

    @_synchronized
    def get_listing(self, search: str, listing_id: str) -> SeenListing | None:
        row = self.db.execute(
            "SELECT listing_id, price_min FROM listings WHERE search_name = ? AND listing_id = ?",
            (search, listing_id),
        ).fetchone()
        return SeenListing(row[0], row[1]) if row else None

    @_synchronized
    def save_listing(self, search: str, listing: Listing) -> None:
        now = self._now().isoformat()
        with self.db:
            self.db.execute(
                """INSERT INTO listings (search_name, listing_id, url, address, price_min,
                                         first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (search_name, listing_id) DO UPDATE SET
                       url = excluded.url, address = excluded.address,
                       price_min = COALESCE(excluded.price_min, listings.price_min),
                       last_seen = excluded.last_seen""",
                (search, listing.listing_id, listing.url, listing.address, listing.price_min,
                 now, now),
            )

    # --- one-time import from v0.1 -------------------------------------
    @_synchronized
    def import_legacy_seen_file(self, path: Path, search_names: list[str]) -> int:
        """Imports v0.1's seen_listings.txt once. Returns the number of IDs imported."""
        if not path.exists():
            return 0
        if self.db.execute("SELECT 1 FROM meta WHERE key = 'legacy_imported'").fetchone():
            return 0
        ids = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()} - {""}
        now = self._now().isoformat()
        with self.db:
            for name in search_names:
                self._ensure_search(name)
                self.db.executemany(
                    "INSERT OR IGNORE INTO listings (search_name, listing_id, first_seen, last_seen)"
                    " VALUES (?, ?, ?, ?)",
                    [(name, i, now, now) for i in ids],
                )
            self.db.execute("INSERT INTO meta (key, value) VALUES ('legacy_imported', ?)", (now,))
        return len(ids)

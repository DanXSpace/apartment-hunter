from __future__ import annotations

import functools
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Concatenate

from apartment_hunter.criteria import criteria_from_json, criteria_to_json
from apartment_hunter.models import Listing, Search

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id TEXT PRIMARY KEY,
    server_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL,
    name TEXT NOT NULL,
    criteria TEXT NOT NULL,
    sources TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0,
    attention TEXT,
    last_run TEXT,
    UNIQUE (server_id, name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS source_health (
    search_id TEXT NOT NULL,
    source TEXT NOT NULL,
    seeded INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    broken_alert_sent INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (search_id, source)
);
CREATE TABLE IF NOT EXISTS listings (
    search_id TEXT NOT NULL,
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    url TEXT,
    address TEXT,
    price_min INTEGER,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (search_id, source, listing_id)
);
CREATE TABLE IF NOT EXISTS owner_alerts (source TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_SEARCH_COLUMNS = (
    "id, server_id, channel_id, created_by, name, criteria, sources, created_at, paused, attention"
)


@dataclass(frozen=True)
class SeenListing:
    listing_id: str
    price_min: int | None


@dataclass(frozen=True)
class SourceHealth:
    seeded: bool
    consecutive_failures: int
    broken_alert_sent: bool
    last_error: str | None


class DuplicateName(Exception):
    """Another Search in the same Discord server already has this name."""


def _synchronized[**P, R](method: Callable[Concatenate[Store, P], R]) -> Callable[Concatenate[Store, P], R]:
    """Run a Store method under the Store's lock: one connection, shared across threads."""
    @functools.wraps(method)
    def wrapper(self: Store, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def _search_from_row(row: tuple) -> Search:
    return Search(
        id=row[0], server_id=row[1], channel_id=row[2], created_by=row[3], name=row[4],
        criteria=criteria_from_json(row[5]), sources=tuple(row[6].split(",")),
        created_at=datetime.fromisoformat(row[7]), paused=bool(row[8]), attention=row[9],
    )


class Store:
    """SQLite state for every Discord server. Safe to share between threads."""

    def __init__(self, path: Path | str, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._now = now
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)

    @_synchronized
    def close(self) -> None:
        self.db.close()

    # --- searches -------------------------------------------------------
    @_synchronized
    def add_search(self, search: Search) -> None:
        try:
            with self.db:
                self.db.execute(
                    f"INSERT INTO searches ({_SEARCH_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (search.id, search.server_id, search.channel_id, search.created_by, search.name,
                     criteria_to_json(search.criteria), ",".join(search.sources),
                     search.created_at.isoformat(), int(search.paused), search.attention),
                )
        except sqlite3.IntegrityError as error:
            raise DuplicateName(search.name) from error

    @_synchronized
    def get_search(self, search_id: str) -> Search | None:
        row = self.db.execute(
            f"SELECT {_SEARCH_COLUMNS} FROM searches WHERE id = ?", (search_id,)
        ).fetchone()
        return _search_from_row(row) if row else None

    @_synchronized
    def find_search(self, server_id: int, name: str) -> Search | None:
        row = self.db.execute(
            f"SELECT {_SEARCH_COLUMNS} FROM searches WHERE server_id = ? AND name = ? COLLATE NOCASE",
            (server_id, name),
        ).fetchone()
        return _search_from_row(row) if row else None

    @_synchronized
    def searches(self, server_id: int | None = None) -> list[Search]:
        query = f"SELECT {_SEARCH_COLUMNS} FROM searches"
        params: tuple = ()
        if server_id is not None:
            query += " WHERE server_id = ?"
            params = (server_id,)
        rows = self.db.execute(query + " ORDER BY created_at, rowid", params).fetchall()
        return [_search_from_row(row) for row in rows]

    @_synchronized
    def update_search(self, search: Search, reseed: bool) -> None:
        try:
            with self.db:
                self.db.execute(
                    "UPDATE searches SET server_id = ?, channel_id = ?, created_by = ?, name = ?, "
                    "criteria = ?, sources = ?, created_at = ?, paused = ?, attention = ? WHERE id = ?",
                    (search.server_id, search.channel_id, search.created_by, search.name,
                     criteria_to_json(search.criteria), ",".join(search.sources),
                     search.created_at.isoformat(), int(search.paused), search.attention, search.id),
                )
                if reseed:
                    self._forget(search.id)
        except sqlite3.IntegrityError as error:
            raise DuplicateName(search.name) from error

    @contextmanager
    def unchanged(self, search: Search) -> Iterator[bool]:
        """Yield whether `search` is still stored with the same Criteria and Sources, holding the lock
        so an edit or delete from another thread can't land between that check and the writes made
        inside the block."""
        with self._lock:
            current = self.get_search(search.id)
            yield (current is not None and current.criteria == search.criteria
                   and current.sources == search.sources)

    def _forget(self, search_id: str) -> None:
        self.db.execute("DELETE FROM listings WHERE search_id = ?", (search_id,))
        self.db.execute("DELETE FROM source_health WHERE search_id = ?", (search_id,))

    @_synchronized
    def delete_search(self, search_id: str) -> None:
        with self.db:
            self._forget(search_id)
            self.db.execute("DELETE FROM searches WHERE id = ?", (search_id,))

    @_synchronized
    def record_run(self, search_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE searches SET last_run = ? WHERE id = ?", (self._now().isoformat(), search_id)
            )

    @_synchronized
    def last_run(self, search_id: str) -> datetime | None:
        row = self.db.execute("SELECT last_run FROM searches WHERE id = ?", (search_id,)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    # --- per-Source health ----------------------------------------------
    def _ensure_health(self, search_id: str, source: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO source_health (search_id, source) VALUES (?, ?)", (search_id, source)
        )

    @_synchronized
    def source_health(self, search_id: str, source: str) -> SourceHealth:
        row = self.db.execute(
            "SELECT seeded, consecutive_failures, broken_alert_sent, last_error FROM source_health "
            "WHERE search_id = ? AND source = ?",
            (search_id, source),
        ).fetchone()
        return SourceHealth(bool(row[0]), row[1], bool(row[2]), row[3]) if row else SourceHealth(
            False, 0, False, None
        )

    @_synchronized
    def mark_seeded(self, search_id: str, source: str) -> None:
        with self.db:
            self._ensure_health(search_id, source)
            self.db.execute(
                "UPDATE source_health SET seeded = 1 WHERE search_id = ? AND source = ?",
                (search_id, source),
            )

    @_synchronized
    def record_failure(self, search_id: str, source: str, error: str) -> int:
        with self.db:
            self._ensure_health(search_id, source)
            self.db.execute(
                "UPDATE source_health SET consecutive_failures = consecutive_failures + 1, "
                "last_error = ? WHERE search_id = ? AND source = ?",
                (error, search_id, source),
            )
        return self.source_health(search_id, source).consecutive_failures

    @_synchronized
    def record_success(self, search_id: str, source: str) -> None:
        with self.db:
            self._ensure_health(search_id, source)
            self.db.execute(
                "UPDATE source_health SET consecutive_failures = 0, broken_alert_sent = 0, "
                "last_error = NULL WHERE search_id = ? AND source = ?",
                (search_id, source),
            )

    @_synchronized
    def mark_broken_alert_sent(self, search_id: str, source: str) -> None:
        with self.db:
            self._ensure_health(search_id, source)
            self.db.execute(
                "UPDATE source_health SET broken_alert_sent = 1 WHERE search_id = ? AND source = ?",
                (search_id, source),
            )

    # --- listings -------------------------------------------------------
    @_synchronized
    def listing_count(self, search_id: str) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM listings WHERE search_id = ?", (search_id,)).fetchone()
        return row[0]

    @_synchronized
    def get_listing(self, search_id: str, source: str, listing_id: str) -> SeenListing | None:
        row = self.db.execute(
            "SELECT listing_id, price_min FROM listings "
            "WHERE search_id = ? AND source = ? AND listing_id = ?",
            (search_id, source, listing_id),
        ).fetchone()
        return SeenListing(row[0], row[1]) if row else None

    @_synchronized
    def save_listing(self, search_id: str, listing: Listing) -> None:
        now = self._now().isoformat()
        with self.db:
            self.db.execute(
                """INSERT INTO listings (search_id, source, listing_id, url, address, price_min,
                                         first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (search_id, source, listing_id) DO UPDATE SET
                       url = excluded.url, address = excluded.address,
                       price_min = COALESCE(excluded.price_min, listings.price_min),
                       last_seen = excluded.last_seen""",
                (search_id, listing.source, listing.listing_id, listing.url, listing.address,
                 listing.price_min, now, now),
            )

    # --- owner alerts ---------------------------------------------------
    @_synchronized
    def owner_alert_sent(self, source: str) -> bool:
        row = self.db.execute("SELECT 1 FROM owner_alerts WHERE source = ?", (source,)).fetchone()
        return row is not None

    @_synchronized
    def set_owner_alert_sent(self, source: str, sent: bool) -> None:
        with self.db:
            if sent:
                self.db.execute("INSERT OR IGNORE INTO owner_alerts (source) VALUES (?)", (source,))
            else:
                self.db.execute("DELETE FROM owner_alerts WHERE source = ?", (source,))

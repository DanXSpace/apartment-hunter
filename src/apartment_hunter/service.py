"""The Search service: the one seam the bot (and the tests) call to create, list and run Searches."""

from __future__ import annotations

import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from apartment_hunter.areas import AreaNotFound, Areas
from apartment_hunter.criteria import Criteria, HomeType, Pets, summarize
from apartment_hunter.models import Search
from apartment_hunter.runner import RunOutcome, SearchRunner
from apartment_hunter.scheduler import Scheduler
from apartment_hunter.sources import SOURCE_NAMES, ScrapeError
from apartment_hunter.storage import DuplicateName, Store

NO_PERMISSION = (
    "I can't post in this channel. Give me the View Channel, Send Messages and Embed Links "
    "permissions here, or run the command in another channel."
)
CHANNEL_GONE = "I can't post in its channel anymore (it was deleted or I lost permission)."
AT_TOTAL_LIMIT = (
    "The bot is watching as many searches as it can right now. Try again later, or ask whoever runs it."
)


@dataclass(frozen=True)
class Limits:
    per_server: int = 5
    total: int = 25
    check_gap_minutes: float = 5.0


DEFAULT_LIMITS = Limits()

class SearchError(Exception):
    """A request was refused; `str(error)` is the plain-language reason shown to the user."""


@dataclass(frozen=True)
class NewSearch:
    """What someone asked for in `/search new`. `sources=None` means every Source the bot has."""

    area: str
    min_price: int | None = None
    max_price: int | None = None
    min_beds: float | None = None
    min_baths: float | None = None
    home_type: HomeType = HomeType.ANY
    pets: Pets = Pets.NONE
    sources: tuple[str, ...] | None = None
    name: str | None = None


@dataclass(frozen=True)
class SearchStatus:
    """How a Search is doing, for `/search list`. `state` is one of the statuses `/search help` explains."""

    search: Search
    state: str
    reason: str | None
    last_run: datetime | None
    listings: int


class Pending:
    """Waits for the outcome of the first Run of a Search to finish after this Pending was created."""

    def __init__(self) -> None:
        self._done = threading.Event()
        self._outcome: RunOutcome | None = None

    def wait(self, timeout: float | None = None) -> RunOutcome | None:
        """The Run's outcome, or None if it didn't finish within `timeout` seconds."""
        self._done.wait(timeout)
        return self._outcome

    def resolve(self, outcome: RunOutcome) -> None:
        """Called by the service when the Run finishes."""
        self._outcome = outcome
        self._done.set()


class SearchService:
    """Creates, lists and runs Searches for every Discord server. Runs happen on the Scheduler's thread."""

    def __init__(
        self,
        *,
        store: Store,
        runner: SearchRunner,
        scheduler: Scheduler,
        areas: Areas,
        source_keys: tuple[str, ...],
        limits: Limits = DEFAULT_LIMITS,
        interval_minutes: float = 15,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], str] = lambda: uuid4().hex[:12],
        rand: Callable[[float, float], float] = random.uniform,
        direct_message: Callable[[int, str], None] = lambda user_id, text: None,
        owner_id: int | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.scheduler = scheduler
        self.areas = areas
        self.source_keys = source_keys
        self.limits = limits
        self.interval_minutes = interval_minutes
        self.clock = clock
        self.new_id = new_id
        self.rand = rand
        self.direct_message = direct_message
        self.owner_id = owner_id
        self._pending: dict[str, list[Pending]] = {}
        self._pending_lock = threading.Lock()

    def load(self) -> None:
        """Schedule every stored Search, spreading their first Runs across the first interval."""
        for search in self.store.searches():
            self.scheduler.add(
                search.id, first_delay=self.rand(0, self.interval_minutes * 60), paused=search.paused
            )

    def run(self, search_id: str) -> None:
        """Run one Search now; the Scheduler's callback."""
        search = self.store.get_search(search_id)
        if search is None:
            return
        outcome = self.runner.run(search)
        if outcome.channel_gone:
            self._pause_for_missing_channel(search_id)
        with self._pending_lock:
            waiting = self._pending.pop(search_id, [])
        for pending in waiting:
            pending.resolve(outcome)

    def _pause_for_missing_channel(self, search_id: str) -> None:
        search = self.store.get_search(search_id)  # re-read: it may have changed during the Run
        if search is None:
            return
        self.store.update_search(replace(search, paused=True, attention=CHANNEL_GONE), reseed=False)
        self.scheduler.pause(search_id)
        self.direct_message(
            search.created_by,
            f'I paused your search "{search.name}": {CHANNEL_GONE} '
            "Fix the channel and use /search resume, or delete it with /search delete.",
        )

    def statuses(self, server_id: int) -> list[SearchStatus]:
        """This server's Searches with their status, oldest first."""
        return [self._status(search) for search in self.store.searches(server_id)]

    def names(self, server_id: int, current: str) -> list[str]:
        """This server's Search names containing `current` (any case), for autocomplete."""
        needle = current.casefold()
        return [s.name for s in self.store.searches(server_id) if needle in s.name.casefold()][:25]

    def suggest_areas(self, text: str) -> list[str]:
        return self.areas.suggest(text)

    def _status(self, search: Search) -> SearchStatus:
        last_run = self.store.last_run(search.id)
        state, reason = self._state(search, last_run)
        return SearchStatus(search, state, reason, last_run, self.store.listing_count(search.id))

    def _state(self, search: Search, last_run: datetime | None) -> tuple[str, str | None]:
        if self.scheduler.current == search.id:
            return "Running", None
        if search.attention:
            return "Needs attention", search.attention
        if search.paused:
            return "Paused", None
        health = {key: self.store.source_health(search.id, key) for key in search.sources}
        failing = [
            f"{SOURCE_NAMES[key]}: {h.last_error}" for key, h in health.items() if h.consecutive_failures
        ]
        if failing:
            return "Needs attention", "; ".join(failing)
        if last_run is None and not any(h.seeded for h in health.values()):
            return "Not run yet", None
        return "Healthy", None

    def create(
        self, server_id: int, channel_id: int, user_id: int, new: NewSearch, *, can_post: bool
    ) -> tuple[Search, Pending]:
        """Save a new Search and queue its Seeding run ahead of the schedule.

        Checks, in order: posting permission, limits, area, name. Any refusal raises SearchError
        and saves nothing.
        """
        if not can_post:
            raise SearchError(NO_PERMISSION)
        on_server = len(self.store.searches(server_id))
        if on_server >= self.limits.per_server:
            raise SearchError(
                f"This server already has {on_server} searches, the most allowed. "
                "Delete one with /search delete first."
            )
        if len(self.store.searches()) >= self.limits.total:
            raise SearchError(AT_TOTAL_LIMIT)
        try:
            area = self.areas.resolve(new.area)
        except AreaNotFound as error:
            raise SearchError(str(error)) from error
        except ScrapeError as error:
            raise SearchError(
                f"I couldn't look up places right now: {error} Try again in a minute."
            ) from error
        # A minimum of 0 means no minimum, except beds, where 0 means "studio or bigger".
        criteria = Criteria(
            area, new.min_price or None, new.max_price, new.min_beds, new.min_baths or None,
            new.home_type, new.pets,
        )
        name = (new.name or "").strip() or summarize(criteria)
        search = Search(
            id=self.new_id(), server_id=server_id, channel_id=channel_id, created_by=user_id, name=name,
            criteria=criteria, sources=new.sources if new.sources is not None else self.source_keys,
            created_at=self.clock(),
        )
        try:
            self.store.add_search(search)
        except DuplicateName as error:
            raise SearchError(
                f'This server already has a search called "{name}". Pick another name with name:.'
            ) from error
        pending = Pending()
        with self._pending_lock:
            self._pending.setdefault(search.id, []).append(pending)
        self.scheduler.add(search.id, first_delay=self.interval_minutes * 60)
        self.scheduler.run_now(search.id)
        return search, pending

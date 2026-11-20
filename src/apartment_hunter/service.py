"""The Search service: the one seam the bot (and the tests) call to create, list, change and run Searches."""

from __future__ import annotations

import logging
import math
import random
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from apartment_hunter.areas import AreaNotFound, Areas
from apartment_hunter.countries import COUNTRIES, Country
from apartment_hunter.criteria import Area, Criteria, HomeType, Pets, summarize
from apartment_hunter.models import Search
from apartment_hunter.runner import RunOutcome, SearchRunner
from apartment_hunter.scheduler import Scheduler
from apartment_hunter.sources import PLACE_NOTE, SOURCE_NAMES, CantPlace, ScrapeError, Source, filters_used
from apartment_hunter.storage import DuplicateName, Store
from apartment_hunter.text import join_and

log = logging.getLogger(__name__)

RUN_FAILED = "Something went wrong checking this Search. The owner can find details in the log."
NO_PERMISSION = (
    "I can't post in this channel. Give me the View Channel, Send Messages and Embed Links "
    "permissions here, or run the command in another channel."
)
CHANNEL_GONE = "I can't post in its channel anymore (it was deleted or I lost permission)."
OWNER_ALERT_STREAK = 3
EVERY_SOURCE = "all"  # in `sources`: every Source of the Area's country
ALL_SITES = "All sites"  # how the `sources` autocomplete names EVERY_SOURCE
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
    """What someone asked for in `/search new`. `sources=None` (or EVERY_SOURCE) means every Source of
    the Area's country."""

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
class SearchChanges:
    """What someone asked to change in `/search edit`; None leaves that part as it is.

    A minimum or maximum price, or a minimum of baths, of 0 removes it (beds 0 means "studio or bigger").
    `sources=(EVERY_SOURCE,)` picks every Source of the Area's country; an Area in another country with
    `sources=None` switches to that country's Sources.
    """

    area: str | None = None
    min_price: int | None = None
    max_price: int | None = None
    min_beds: float | None = None
    min_baths: float | None = None
    home_type: HomeType | None = None
    pets: Pets | None = None
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
    """Waits for the outcome of the first Run of a Search to finish after this Pending was created.

    If the Search is deleted first, the Pending is cancelled: waiting ends at once, with no outcome.
    `notes` are plain-language sentences for the reply about the request itself: Sources left out
    because they couldn't place the Area, and filters a Source can't apply.
    """

    def __init__(self, notes: tuple[str, ...] = ()) -> None:
        self.notes = notes
        self._done = threading.Event()
        self._outcome: RunOutcome | None = None
        self._cancelled = False

    def wait(self, timeout: float | None = None) -> RunOutcome | None:
        """The Run's outcome, or None if it didn't finish within `timeout` seconds or was cancelled."""
        self._done.wait(timeout)
        return self._outcome

    @property
    def done(self) -> bool:
        """True once the Run finished or the Pending was cancelled."""
        return self._done.is_set()

    @property
    def cancelled(self) -> bool:
        """True when the Search was deleted before the Run finished."""
        return self._cancelled

    def resolve(self, outcome: RunOutcome) -> None:
        """Called by the service when the Run finishes."""
        self._outcome = outcome
        self._done.set()

    def cancel(self) -> None:
        """Called by the service when the Search is deleted before the Run finished."""
        self._cancelled = True
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
        sources: Mapping[str, Source],
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
        self.sources = sources
        self.limits = limits
        self.interval_minutes = interval_minutes
        self.clock = clock
        self.new_id = new_id
        self.rand = rand
        self.direct_message = direct_message
        self.owner_id = owner_id
        self._pending: dict[str, list[Pending]] = {}
        self._pending_lock = threading.Lock()
        self._last_check: dict[str, datetime] = {}

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
        try:
            outcome = self.runner.run(search)
        except Exception:  # tell whoever waits now; the Scheduler carries on with other Searches
            log.exception("[%s] Unexpected error in a Run", search.name)
            outcome = RunOutcome(search_id, (), 0, error=RUN_FAILED)
        if outcome.superseded:
            # Edited or deleted mid-Run: the edit queued a fresh Run, which the waiters now wait for;
            # a delete has already let them go.
            return
        if outcome.channel_gone:
            self._pause_for_missing_channel(search_id)
        if self.owner_id is not None:
            for key in search.sources:
                self._tell_owner_about(key, self.owner_id)
        with self._pending_lock:
            waiting = self._pending.pop(search_id, [])
        for pending in waiting:
            pending.resolve(outcome)

    def _tell_owner_about(self, source: str, owner_id: int) -> None:
        """DM the owner when `source` has failed 3 Runs in a row in every active Search using it, and
        again once it works in any of them."""
        using = [s for s in self.store.searches() if source in s.sources and not s.paused]
        health = [self.store.source_health(s.id, source) for s in using]
        name = SOURCE_NAMES.get(source, source)
        alerted = self.store.owner_alert_sent(source)
        if health and not alerted and all(h.consecutive_failures >= OWNER_ALERT_STREAK for h in health):
            reason = health[-1].last_error or ""
            self.direct_message(
                owner_id,
                f"⚠️ {name} has failed in every search for {OWNER_ALERT_STREAK} checks in a row: {reason} "
                f"The host may be blocked, or {name} changed its pages.",
            )
            self.store.set_owner_alert_sent(source, True)
        # Recovered only when a Search has read the Source successfully since: a successful Run always
        # leaves it seeded, while a Search that hasn't run it yet is unseeded with no failures.
        elif alerted and any(h.seeded and h.consecutive_failures == 0 for h in health):
            self.direct_message(owner_id, f"✅ {name} is working again.")
            self.store.set_owner_alert_sent(source, False)

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

    def _country_sources(self, country: Country) -> tuple[str, ...]:
        """The country's Sources that the bot has, in the countries table's order."""
        return tuple(key for key in country.sources if key in self.sources)

    def source_choices(
        self, server_id: int, area: str | None, search: str | None = None, current: str = ""
    ) -> list[tuple[str, str]]:
        """(label, value) pairs for the `sources` autocomplete: "All sites", then the Sources of the
        country of the Area being typed (a suggestion ends with its country's name), else of the
        Search's Area when editing `search`, else of every country. Narrowed to labels containing
        `current`. Makes no place lookups."""
        country = _country_named_in(area or "")
        if country is None and search is not None:
            found = self.store.find_search(server_id, search)
            country = COUNTRIES.get(found.criteria.area.country) if found is not None else None
        countries = [country] if country is not None else list(COUNTRIES.values())
        keys = dict.fromkeys(key for c in countries for key in self._country_sources(c))
        choices = [(ALL_SITES, EVERY_SOURCE)] + [(SOURCE_NAMES.get(key, key), key) for key in keys]
        return [choice for choice in choices if current.casefold() in choice[0].casefold()]

    def _check_sources(self, sources: tuple[str, ...] | None, area: Area) -> tuple[str, ...]:
        """The Sources to use for `area` (None or EVERY_SOURCE: all of its country's); raises SearchError
        for Sources the bot lacks or that belong to another country. A Source may be given by its
        name ("Rightmove") as well as its key, since the option takes typed text."""
        country = COUNTRIES[area.country]
        valid = self._country_sources(country)
        if sources is not None:
            sources = tuple(_source_key(text) for text in sources)
        if sources is None or sources == (EVERY_SOURCE,):
            return valid
        available = ", ".join(SOURCE_NAMES.get(key, key) for key in valid)
        if not sources:
            raise SearchError(f"Pick at least one site to check: {available}.")
        foreign = [key for key in sources if key in self.sources and key not in valid]
        if foreign:
            names = join_and([SOURCE_NAMES.get(key, key) for key in foreign])
            raise SearchError(f"{names} can't search {area.display}. Pick from: {available}.")
        unknown = " or ".join(SOURCE_NAMES.get(key, key) for key in sources if key not in valid)
        if unknown:
            raise SearchError(f"I can't check {unknown} yet. Pick from: {available}.")
        return sources

    def _place(self, area: Area, sources: tuple[str, ...]) -> tuple[Area, tuple[str, ...], list[str]]:
        """Place `area` on each of `sources` that hasn't placed it yet. Returns the Area with their
        source locations, the Sources that can search it, and a note for each one left out. Raises
        SearchError when none can, or when a place lookup didn't answer."""
        locations = dict(area.locations)
        left_out: list[str] = []
        for key in sources:
            if key in locations:
                continue
            try:
                locations[key] = self.sources[key].place(area)
            except CantPlace as error:
                left_out.append(f"{SOURCE_NAMES.get(key, key)} was left out: {error}")
            except ScrapeError as error:
                raise SearchError(
                    f"I couldn't look up places right now: {error} Try again in a minute."
                ) from error
        placed = tuple(key for key in sources if key in locations)
        if not placed:
            raise SearchError(f"I can't search {area.display} on any site: {' '.join(left_out)}")
        return replace(area, locations=locations), placed, left_out

    def _source_notes(self, criteria: Criteria, sources: tuple[str, ...]) -> list[str]:
        """What each Source searches that differs from what was asked: a broader area than the one
        picked, a home type it can't match exactly, and the filters it can't apply."""
        notes = []
        for key in sources:
            if key not in self.sources:
                continue
            source = self.sources[key]
            place_note = criteria.area.locations.get(key, {}).get(PLACE_NOTE)
            if place_note:
                notes.append(str(place_note))
            if criteria.home_type in source.home_type_notes:
                notes.append(source.home_type_notes[criteria.home_type])
            missing = [f.value for f in filters_used(criteria) if f not in source.filters]
            if missing:
                notes.append(f"{SOURCE_NAMES.get(key, key)} can't filter by {' or '.join(missing)}, "
                             "so you'll see all of its matches.")
        return notes

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

        Checks, in order: posting permission, limits, area, Sources (and placing the area on each), name.
        Any refusal raises SearchError and saves nothing.
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
        area = self._resolve_area(new.area)
        area, sources, left_out = self._place(area, self._check_sources(new.sources, area))
        # A minimum of 0 means no minimum, except beds, where 0 means "studio or bigger".
        criteria = Criteria(
            area, new.min_price or None, new.max_price, new.min_beds, new.min_baths or None,
            new.home_type, new.pets,
        )
        name = (new.name or "").strip() or summarize(criteria)
        search = Search(
            id=self.new_id(), server_id=server_id, channel_id=channel_id, created_by=user_id, name=name,
            criteria=criteria, sources=sources,
            created_at=self.clock(),
        )
        try:
            self.store.add_search(search)
        except DuplicateName as error:
            raise SearchError(
                f'This server already has a search called "{name}". Pick another name with name:.'
            ) from error
        self.scheduler.add(search.id, first_delay=self.interval_minutes * 60)
        notes = (*left_out, *self._source_notes(criteria, sources))
        return search, self._run_now(search.id, notes)

    def find(self, server_id: int, name: str) -> Search:
        """This server's Search called `name` (any case); raises SearchError when there's none."""
        search = self.store.find_search(server_id, name)
        if search is None:
            raise SearchError(f'There\'s no search called "{name}" in this server. /search list shows them.')
        return search

    def edit(self, server_id: int, name: str, changes: SearchChanges) -> tuple[Search, Pending | None]:
        """Change a Search. A Criteria or Sources change starts it over with a quiet Seeding run (the
        Pending waits for it); a rename alone keeps everything it has seen. Refusals change nothing."""
        search = self.find(server_id, name)
        old = search.criteria
        area = self._resolve_area(changes.area) if changes.area is not None else old.area
        requested = changes.sources
        if requested is None:
            requested = None if area.country != old.area.country else search.sources
        area, sources, left_out = self._place(area, self._check_sources(requested, area))
        criteria = Criteria(
            area,
            _limit(changes.min_price, old.min_price),
            _limit(changes.max_price, old.max_price),
            old.min_beds if changes.min_beds is None else changes.min_beds,
            _limit(changes.min_baths, old.min_baths),
            changes.home_type or old.home_type,
            changes.pets or old.pets,
        )
        reseed = criteria != old or sources != search.sources
        chosen = (changes.name or "").strip()
        # A Search still named after its criteria follows them, unless that name is taken here.
        follows = not chosen and criteria != old and search.name == summarize(old)
        new_name = chosen or (summarize(criteria) if follows else search.name)
        edited = replace(search, name=new_name, criteria=criteria, sources=sources)
        try:
            self.store.update_search(edited, reseed=reseed)
        except DuplicateName as error:
            if not follows:
                raise SearchError(
                    f'This server already has a search called "{new_name}". Pick another name with name:.'
                ) from error
            edited = replace(edited, name=search.name)
            self.store.update_search(edited, reseed=reseed)
        if not reseed:
            return edited, None
        return edited, self._run_now(search.id, (*left_out, *self._source_notes(criteria, sources)))

    def pause(self, server_id: int, name: str) -> Search:
        """Stop a Search's scheduled Runs until it's resumed."""
        search = replace(self.find(server_id, name), paused=True)
        self.store.update_search(search, reseed=False)
        self.scheduler.pause(search.id)
        return search

    def resume(self, server_id: int, name: str) -> Search:
        """Start a paused Search's scheduled Runs again, clearing any reason it needed attention."""
        search = replace(self.find(server_id, name), paused=False, attention=None)
        self.store.update_search(search, reseed=False)
        self.scheduler.resume(search.id)
        return search

    def delete(self, server_id: int, name: str) -> None:
        """Delete a Search and everything it has seen."""
        self._delete(self.find(server_id, name).id)

    def check(self, server_id: int, name: str) -> tuple[Search, Pending]:
        """Run a Search now, at most once per `limits.check_gap_minutes`."""
        search = self.find(server_id, name)
        now = self.clock()
        last = self._last_check.get(search.id)
        if last is not None:
            left = self.limits.check_gap_minutes - (now - last).total_seconds() / 60
            if left > 0:
                minutes = math.ceil(left)
                raise SearchError(
                    f'You can check "{search.name}" again in {minutes} minute{"" if minutes == 1 else "s"}.'
                )
        self._last_check[search.id] = now
        return search, self._run_now(search.id, tuple(self._source_notes(search.criteria, search.sources)))

    def server_removed(self, server_id: int) -> int:
        """The bot left a server: delete its Searches. Returns how many there were."""
        searches = self.store.searches(server_id)
        for search in searches:
            self._delete(search.id)
        return len(searches)

    def keep_servers(self, server_ids: set[int]) -> int:
        """The bot is in exactly these servers: delete the Searches of any other server (it was removed
        while offline). Returns how many Searches were deleted."""
        gone = {search.server_id for search in self.store.searches()} - server_ids
        return sum(self.server_removed(server_id) for server_id in gone)

    def _delete(self, search_id: str) -> None:
        """Forget a Search everywhere; anyone waiting on its next Run stops waiting."""
        self.scheduler.remove(search_id)
        self.store.delete_search(search_id)
        self._last_check.pop(search_id, None)
        with self._pending_lock:
            waiting = self._pending.pop(search_id, [])
        for pending in waiting:
            pending.cancel()

    def _run_now(self, search_id: str, notes: tuple[str, ...] = ()) -> Pending:
        """Queue a Search ahead of the schedule; the Pending waits for that Run."""
        pending = Pending(notes)
        with self._pending_lock:
            self._pending.setdefault(search_id, []).append(pending)
        self.scheduler.run_now(search_id)
        return pending

    def _resolve_area(self, text: str) -> Area:
        try:
            return self.areas.resolve(text)
        except AreaNotFound as error:
            raise SearchError(str(error)) from error
        except ScrapeError as error:
            raise SearchError(
                f"I couldn't look up places right now: {error} Try again in a minute."
            ) from error


def _country_named_in(area: str) -> Country | None:
    """The country whose name ends `area` ("Camden Town, London, United Kingdom"), if any."""
    last = area.rsplit(",", 1)[-1].strip().casefold()
    return next((c for c in COUNTRIES.values() if c.name.casefold() == last), None)


def _source_key(text: str) -> str:
    """A Source key for a key or a name typed in any case; "All sites" is EVERY_SOURCE."""
    typed = text.strip().casefold()
    if typed == ALL_SITES.casefold():
        return EVERY_SOURCE
    return next((key for key, name in SOURCE_NAMES.items() if typed in (key, name.casefold())), text)


def _limit[N: (int, float)](change: N | None, current: N | None) -> N | None:
    """A price or baths limit after an edit: None keeps `current`, 0 removes the limit."""
    if change is None:
        return current
    return change or None

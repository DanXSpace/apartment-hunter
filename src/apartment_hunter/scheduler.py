from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable

from apartment_hunter.config import Search

log = logging.getLogger(__name__)

JITTER = 0.2


def next_delay_seconds(interval_minutes: float) -> float:
    return interval_minutes * 60 * random.uniform(1 - JITTER, 1 + JITTER)


class Scheduler:
    """Runs searches one at a time (never in parallel), each on its own jittered interval.

    Every search is due as soon as the scheduler starts. Paused searches are skipped by the
    schedule; `run_now` queues a search ahead of the schedule (even a paused one). Nothing runs
    while stopped; queued work waits for `start`.

    `run_pending` does all due work synchronously in the caller's thread, which is what tests
    drive. `serve_forever` (blocking) and `start_in_background` (thread) loop over it, sleeping
    until the next search is due or a control call wakes them.
    """

    def __init__(
        self,
        run: Callable[[Search], None],
        searches: list[Search],
        clock: Callable[[], float] = time.monotonic,
        next_delay: Callable[[float], float] = next_delay_seconds,
    ):
        self._run = run
        self._clock = clock
        self._next_delay = next_delay
        self._searches = {s.name: s for s in searches}
        now = clock()
        self._next_run = {s.name: now for s in searches}
        self._running = False
        self._paused: set[str] = set()
        self._queue: list[str] = []
        self._shutdown = False
        self._thread: threading.Thread | None = None
        self._cond = threading.Condition()
        self._run_lock = threading.Lock()  # held across runs: never two at once
        self._serving = False
        self._current: str | None = None

    # -- control (safe to call from any thread) --

    def start(self) -> None:
        with self._cond:
            self._running = True
            self._cond.notify_all()

    def stop(self) -> None:
        """Stop starting runs. A run already in progress finishes."""
        with self._cond:
            self._running = False
            self._cond.notify_all()

    @property
    def running(self) -> bool:
        return self._running

    def pause(self, name: str) -> None:
        with self._cond:
            self._paused.add(name)
            self._cond.notify_all()

    def resume(self, name: str) -> None:
        with self._cond:
            self._paused.discard(name)
            self._cond.notify_all()

    def is_paused(self, name: str) -> bool:
        return name in self._paused

    # -- reading state (safe from any thread) --

    @property
    def current(self) -> str | None:
        """The search being run right now, if any."""
        return self._current

    def next_run(self, name: str) -> float:
        """When the search is next due on its schedule, in the scheduler's clock."""
        with self._cond:
            return self._next_run[name]

    def run_now(self, name: str) -> None:
        """Queue a search to run next, even if paused. It still runs one at a time."""
        if name not in self._searches:
            raise KeyError(name)
        with self._cond:
            if name not in self._queue:
                self._queue.append(name)
            self._cond.notify_all()

    # -- doing the work --

    def run_pending(self) -> int:
        """Synchronously run every search that is due now, in order. Returns how many ran."""
        ran = 0
        with self._run_lock:
            while True:
                with self._cond:
                    if not self._running or (name := self._take_next_due()) is None:
                        return ran
                    search = self._searches[name]
                self._current = name
                try:
                    self._run(search)
                except Exception:  # never let one bad run kill the scheduler
                    log.exception("[%s] Unexpected error", name)
                finally:
                    self._current = None
                ran += 1
                with self._cond:
                    delay = self._next_delay(search.interval_minutes)
                    self._next_run[name] = self._clock() + delay
                if self._serving:
                    log.info("[%s] Next run in %.0f min", name, delay / 60)

    def serve_forever(self) -> None:
        """Run due work as it comes due, in the calling thread, until `shutdown`."""
        self._serving = True
        try:
            while True:
                self.run_pending()
                with self._cond:
                    if self._shutdown:
                        return
                    self._cond.wait(timeout=self._seconds_until_next_due())
                    if self._shutdown:
                        return
        finally:
            self._serving = False

    def start_in_background(self) -> None:
        """Start the scheduler and serve it from a daemon thread (one thread at most)."""
        if self._thread is not None and self._thread.is_alive():
            self.start()
            return
        with self._cond:
            self._shutdown = False
        self.start()
        self._thread = threading.Thread(target=self.serve_forever, name="scheduler", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float | None = None) -> None:
        """Stop for good; waits for a run in progress (up to `timeout`) if in the background."""
        with self._cond:
            self._shutdown = True
            self._running = False
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)

    # -- internals; callers hold self._cond --

    def _take_next_due(self) -> str | None:
        if self._queue:
            return self._queue.pop(0)
        now = self._clock()
        due = [n for n, t in self._next_run.items() if t <= now and n not in self._paused]
        return min(due, key=self._next_run.__getitem__) if due else None

    def _seconds_until_next_due(self) -> float | None:
        """None means nothing can come due on its own (stopped, or every search paused)."""
        if not self._running:
            return None
        if self._queue:
            return 0
        pending = [t for n, t in self._next_run.items() if n not in self._paused]
        return max(0.0, min(pending) - self._clock()) if pending else None

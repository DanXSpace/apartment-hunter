import pytest

from apartment_hunter.config import Search
from apartment_hunter.scheduler import Scheduler, next_delay_seconds

A = Search("A", "https://www.apartments.com/a/", 15, "https://discord/webhook")
B = Search("B", "https://www.apartments.com/b/", 30, "https://discord/webhook")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += minutes * 60


@pytest.fixture
def env():
    clock, ran = FakeClock(), []
    scheduler = Scheduler(lambda s: ran.append(s.name), [A, B], clock=clock,
                          next_delay=lambda minutes: minutes * 60)
    scheduler.start()
    return scheduler, clock, ran


def test_every_search_runs_once_at_start_then_on_its_interval(env):
    scheduler, clock, ran = env
    assert scheduler.run_pending() == 2
    assert ran == ["A", "B"]
    clock.advance(14)
    assert scheduler.run_pending() == 0
    clock.advance(1)
    scheduler.run_pending()
    assert ran == ["A", "B", "A"]
    clock.advance(15)
    scheduler.run_pending()
    assert ran == ["A", "B", "A", "A", "B"]


def test_default_jitter_is_within_twenty_percent_of_the_interval():
    delays = [next_delay_seconds(10) for _ in range(500)]
    assert all(480 <= d <= 720 for d in delays)
    assert max(delays) - min(delays) > 60  # actually varies


def test_stop_halts_further_runs_after_the_current_one():
    ran = []

    def run_then_stop(search):
        ran.append(search.name)
        scheduler.stop()

    scheduler = Scheduler(run_then_stop, [A, B], clock=FakeClock())
    scheduler.start()
    assert scheduler.run_pending() == 1
    assert ran == ["A"]
    assert scheduler.run_pending() == 0
    scheduler.start()
    scheduler.run_pending()
    assert ran == ["A", "B"]


def test_paused_search_is_skipped_until_resumed(env):
    scheduler, clock, ran = env
    scheduler.pause("A")
    assert scheduler.is_paused("A")
    scheduler.run_pending()
    clock.advance(30)
    scheduler.run_pending()
    assert ran == ["B", "B"]
    scheduler.resume("A")
    scheduler.run_pending()
    assert ran == ["B", "B", "A"]


def test_run_now_queues_a_search_ahead_of_its_interval(env):
    scheduler, clock, ran = env
    scheduler.run_pending()
    clock.advance(5)
    scheduler.run_now("B")
    scheduler.run_now("B")
    assert ran == ["A", "B"]  # queued, not run inline
    assert scheduler.run_pending() == 1
    assert ran == ["A", "B", "B"]
    ran.clear()
    clock.advance(29)  # t=34: B's interval restarted at t=5, so it isn't due until t=35
    scheduler.run_pending()
    assert "B" not in ran
    clock.advance(1)
    scheduler.run_pending()
    assert ran[-1] == "B"


def test_run_now_waits_while_stopped(env):
    scheduler, clock, ran = env
    scheduler.run_pending()
    scheduler.stop()
    scheduler.run_now("A")
    assert scheduler.run_pending() == 0
    scheduler.start()
    assert scheduler.run_pending() == 1
    assert ran == ["A", "B", "A"]


def test_one_bad_run_does_not_stop_the_scheduler():
    clock, ran = FakeClock(), []

    def flaky(search):
        if search.name == "A":
            raise RuntimeError("boom")
        ran.append(search.name)

    scheduler = Scheduler(flaky, [A, B], clock=clock, next_delay=lambda minutes: minutes * 60)
    scheduler.start()
    assert scheduler.run_pending() == 2
    assert ran == ["B"]
    clock.advance(30)
    scheduler.run_pending()
    assert ran == ["B", "B"]

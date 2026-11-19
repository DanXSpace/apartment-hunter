import pytest

from apartment_hunter.scheduler import Scheduler, next_delay_seconds


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
    scheduler = Scheduler(ran.append, interval_minutes=15, clock=clock, next_delay=lambda m: m * 60)
    scheduler.add("A")
    scheduler.add("B")
    scheduler.start()
    return scheduler, clock, ran


def test_every_search_runs_once_at_start_then_on_the_interval(env):
    scheduler, clock, ran = env
    assert scheduler.run_pending() == 2
    assert ran == ["A", "B"]
    clock.advance(14)
    assert scheduler.run_pending() == 0
    clock.advance(1)
    scheduler.run_pending()
    assert ran == ["A", "B", "A", "B"]


def test_default_jitter_is_within_twenty_percent_of_the_interval():
    delays = [next_delay_seconds(10) for _ in range(500)]
    assert all(480 <= d <= 720 for d in delays)
    assert max(delays) - min(delays) > 60  # actually varies


def test_stop_halts_further_runs_after_the_current_one():
    ran = []

    def run_then_stop(search_id):
        ran.append(search_id)
        scheduler.stop()

    scheduler = Scheduler(run_then_stop, clock=FakeClock())
    scheduler.add("A")
    scheduler.add("B")
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
    clock.advance(15)
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
    clock.advance(14)  # t=19: B's interval restarted at t=5, so it isn't due until t=20
    scheduler.run_pending()
    assert ran == ["A"]
    clock.advance(1)
    scheduler.run_pending()
    assert ran == ["A", "B"]


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

    def flaky(search_id):
        if search_id == "A":
            raise RuntimeError("boom")
        ran.append(search_id)

    scheduler = Scheduler(flaky, interval_minutes=15, clock=clock, next_delay=lambda m: m * 60)
    scheduler.add("A")
    scheduler.add("B")
    scheduler.start()
    assert scheduler.run_pending() == 2
    assert ran == ["B"]
    clock.advance(15)
    scheduler.run_pending()
    assert ran == ["B", "B"]


def test_added_search_runs_after_its_first_delay_then_on_the_interval():
    clock, ran = FakeClock(), []
    s = Scheduler(ran.append, interval_minutes=15, clock=clock, next_delay=lambda m: m * 60)
    s.start()
    s.add("a", first_delay=300)
    assert s.run_pending() == 0
    clock.advance(5)
    assert s.run_pending() == 1
    clock.advance(15)
    s.run_pending()
    assert ran == ["a", "a"]


def test_removed_search_never_runs_even_if_queued():
    clock, ran = FakeClock(), []
    s = Scheduler(ran.append, clock=clock, next_delay=lambda m: m * 60)
    s.start()
    s.add("a", first_delay=900)
    s.run_now("a")
    s.remove("a")
    assert s.run_pending() == 0
    with pytest.raises(KeyError):
        s.run_now("a")


def test_added_paused_search_waits_for_resume():
    clock, ran = FakeClock(), []
    s = Scheduler(ran.append, clock=clock, next_delay=lambda m: m * 60)
    s.start()
    s.add("a", paused=True)
    assert s.run_pending() == 0
    s.resume("a")
    assert s.run_pending() == 1

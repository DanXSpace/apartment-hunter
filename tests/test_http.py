from apartment_hunter.sources import BlockedError, ScrapeError
from apartment_hunter.sources.http import PoliteHttpClient
from fakes import FakeHttp


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_waits_out_the_gap_between_requests_to_the_same_host():
    inner, clock, slept = FakeHttp(), FakeClock(), []

    def sleep(s):
        slept.append(s)
        clock.advance(s)

    polite = PoliteHttpClient(inner, clock=clock, sleep=sleep, rand=lambda lo, hi: 5.0)
    polite.get("https://www.zillow.com/a")
    clock.advance(1.0)
    polite.get("https://www.zillow.com/b")
    assert slept == [4.0]
    assert inner.requested == ["https://www.zillow.com/a", "https://www.zillow.com/b"]


def test_no_wait_for_another_host_or_once_the_gap_has_passed():
    inner, clock, slept = FakeHttp(), FakeClock(), []
    polite = PoliteHttpClient(inner, clock=clock, sleep=slept.append, rand=lambda lo, hi: 5.0)
    polite.get("https://www.zillow.com/a")
    polite.get("https://chicago.craigslist.org/search/apa")
    clock.advance(6.0)
    polite.get("https://www.zillow.com/b")
    assert slept == []


def test_a_blocked_error_is_a_scrape_error_with_a_plain_reason():
    error = BlockedError("Zillow asked for a CAPTCHA")
    assert isinstance(error, ScrapeError)
    assert str(error) == "Zillow asked for a CAPTCHA"

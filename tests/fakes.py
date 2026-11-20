from apartment_hunter.criteria import Area, Criteria, SourceLocation
from apartment_hunter.models import Listing
from apartment_hunter.notifiers import ChannelGone, NotifyError
from apartment_hunter.sources import ALL_FILTERS, CantPlace, SourcePage
from apartment_hunter.sources.http import HttpResponse

_OK = HttpResponse(200, "ok")
MINUTE = 60.0


class FakeClock:
    """A clock in seconds that moves only when told to. Tests that think in minutes multiply by MINUTE."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeHttp:
    """Returns one response for every URL and records the URLs it was asked for."""

    def __init__(self, response: HttpResponse | str = _OK) -> None:
        self.response = HttpResponse(200, response) if isinstance(response, str) else response
        self.requested: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.requested.append(url)
        return self.response


class FakeSource:
    """Serves scripted pages, one per `fetch`, and records the page numbers it was asked for.

    Each item in `pages` is a `SourcePage`, a list of Listings (one page holding all of them, with
    nothing more), or an exception to raise.

    `place` gives every Area the location {"at": <area display>} and records the Areas it placed;
    set `cant_place` to a reason to refuse instead. `filters` is what it can filter by (all of it
    unless a test narrows it). `locations` records the source location each `fetch` was given.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        self.filters = ALL_FILTERS
        self.pages: list[SourcePage | list[Listing] | Exception] = []
        self.requested: list[int] = []
        self.placed: list[str] = []
        self.cant_place: str | None = None
        self.locations: list[SourceLocation | None] = []

    def place(self, area: Area) -> SourceLocation:
        self.placed.append(area.display)
        if self.cant_place is not None:
            raise CantPlace(self.cant_place)
        return {"at": area.display}

    def fetch(self, criteria: Criteria, page: int) -> SourcePage:
        self.requested.append(page)
        self.locations.append(criteria.area.locations.get(self.key))
        item = self.pages.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):
            return SourcePage(item, len(item), False)
        return item

    def results_url(self, criteria: Criteria) -> str:
        return f"https://{self.key}.example/results"


class FakeNotifier:
    """Records what it delivered. `fail` refuses delivery; `gone` reports the channel missing."""

    def __init__(self) -> None:
        self.listings: list[tuple[str, int | None]] = []
        self.texts: list[str] = []
        self.fail = False
        self.gone = False

    def _deliver(self) -> None:
        if self.gone:
            raise ChannelGone("Unknown Channel")
        if self.fail:
            raise NotifyError("Discord is down")

    def send_listing(self, listing: Listing, search_name: str, previous_price: int | None = None) -> None:
        self._deliver()
        self.listings.append((listing.listing_id, previous_price))

    def send_text(self, message: str) -> None:
        self._deliver()
        self.texts.append(message)

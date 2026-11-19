from apartment_hunter.sources.http import HttpResponse

_OK = HttpResponse(200, "ok")


class FakeHttp:
    """Returns one response for every URL and records the URLs it was asked for."""

    def __init__(self, response: HttpResponse | str = _OK) -> None:
        self.response = HttpResponse(200, response) if isinstance(response, str) else response
        self.requested: list[str] = []

    def get(self, url: str) -> HttpResponse:
        self.requested.append(url)
        return self.response

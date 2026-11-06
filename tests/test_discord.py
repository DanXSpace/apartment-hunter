import pytest
import requests

from apartment_hunter.notifiers import NotifyError
from apartment_hunter.notifiers.discord import DiscordNotifier, build_embed
from conftest import make_listing


class Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text, self.headers = status, body or {}, text, {}

    def json(self):
        return self._body


def _patch(monkeypatch, responses):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return responses.pop(0)
    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def test_retries_on_rate_limit(monkeypatch):
    calls = _patch(monkeypatch, [Resp(429, {"retry_after": 0.5}), Resp(204)])
    slept = []
    DiscordNotifier("https://hook", sleep=slept.append).send_text("hi")
    assert len(calls) == 2 and slept == [0.5]


def test_client_error_raises_without_retry(monkeypatch):
    calls = _patch(monkeypatch, [Resp(400, text="bad embed")])
    with pytest.raises(NotifyError, match="400"):
        DiscordNotifier("https://hook", sleep=lambda s: None).send_text("hi")
    assert len(calls) == 1


def test_gives_up_after_max_attempts(monkeypatch):
    _patch(monkeypatch, [Resp(500)] * 3)
    with pytest.raises(NotifyError, match="3 attempts"):
        DiscordNotifier("https://hook", max_attempts=3, sleep=lambda s: None).send_text("hi")


@pytest.mark.parametrize("status,ok", [(200, True), (400, False), (401, False), (404, False)])
def test_webhook_check(monkeypatch, status, ok):
    monkeypatch.setattr(requests, "get", lambda url, timeout: Resp(status))
    notifier = DiscordNotifier("https://hook")
    if ok:
        notifier.check()
    else:
        with pytest.raises(NotifyError, match="invalid"):
            notifier.check()


def test_embed_formats_ranges_and_studio():
    listing = make_listing(price_min=1515, price_max=2410, beds_min=0, beds_max=2,
                           baths_min=1, baths_max=1.5)
    fields = {f["name"]: f["value"] for f in build_embed(listing, "Downtown")["fields"]}
    assert fields["💰 Price"] == "**$1,515 – $2,410/mo**"
    assert fields["🛏 Beds"] == "Studio – 2"
    assert fields["🛁 Baths"] == "1 – 1.5"


def test_embed_price_drop():
    embed = build_embed(make_listing(price=1900), "Downtown", previous_price=2100)
    assert "$2,100 → $1,900" in embed["description"]


def test_embed_unknown_price():
    embed = build_embed(make_listing(price=None), "Downtown")
    assert embed["fields"][0]["value"] == "Call for price"

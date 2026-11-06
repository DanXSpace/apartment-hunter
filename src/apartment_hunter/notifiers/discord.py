from __future__ import annotations

import logging
import time
from collections.abc import Callable

import requests

from apartment_hunter.models import Listing
from apartment_hunter.notifiers import NotifyError

log = logging.getLogger(__name__)

GREEN = 0x57F287
BLUE = 0x3498DB


def _fmt_range(lo, hi, fmt: Callable[[float], str]) -> str:
    if lo is None:
        return "—"
    return fmt(lo) if hi is None or hi == lo else f"{fmt(lo)} – {fmt(hi)}"


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _beds(v: float) -> str:
    return "Studio" if v == 0 else _num(v)


def _price(v: float) -> str:
    return f"${int(v):,}"


def build_embed(listing: Listing, search_name: str, previous_price: int | None = None) -> dict:
    if previous_price is not None and listing.price_min is not None:
        description = f"📉 **Price drop:** {_price(previous_price)} → {_price(listing.price_min)}"
        color = BLUE
    else:
        description = "New listing matches your search!"
        color = GREEN

    price = _fmt_range(listing.price_min, listing.price_max, _price)
    embed = {
        "title": f"🏠 {listing.name or listing.address}"[:256],
        "url": listing.url,
        "description": f"{listing.address}\n{description}" if listing.name else description,
        "color": color,
        "fields": [
            {"name": "💰 Price", "value": "Call for price" if price == "—" else f"**{price}/mo**",
             "inline": True},
            {"name": "🛏 Beds", "value": _fmt_range(listing.beds_min, listing.beds_max, _beds),
             "inline": True},
            {"name": "🛁 Baths", "value": _fmt_range(listing.baths_min, listing.baths_max, _num),
             "inline": True},
        ],
        "footer": {"text": f"{search_name} · {listing.scraped_at.strftime('%b %d, %Y %I:%M %p')}"},
    }
    if listing.image_url:
        embed["image"] = {"url": listing.image_url}
    return embed


class DiscordNotifier:
    def __init__(self, webhook_url: str, max_attempts: int = 5,
                 sleep: Callable[[float], None] = time.sleep):
        self.webhook_url = webhook_url
        self.max_attempts = max_attempts
        self.sleep = sleep

    def check(self) -> None:
        """Fail fast on a wrong webhook: a GET on a valid webhook returns its metadata."""
        try:
            resp = requests.get(self.webhook_url, timeout=15)
        except requests.RequestException as e:
            log.warning("Could not verify Discord webhook (network error: %s)", e)
            return
        if resp.status_code in (400, 401, 404):
            raise NotifyError(f"Discord webhook is invalid ({resp.status_code}). Check "
                              "DISCORD_WEBHOOK_URL / webhook_url; copy it from Discord's "
                              "channel settings → Integrations → Webhooks.")

    def send_listing(self, listing: Listing, search_name: str,
                     previous_price: int | None = None) -> None:
        self._post({"embeds": [build_embed(listing, search_name, previous_price)]})

    def send_text(self, message: str) -> None:
        self._post({"content": message[:2000]})

    def _post(self, payload: dict) -> None:
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = requests.post(self.webhook_url, json=payload, timeout=15)
            except requests.RequestException as e:
                wait, reason = 2.0 ** attempt, f"network error: {e}"
            else:
                if resp.status_code in (200, 204):
                    return
                if resp.status_code == 429:
                    try:
                        wait = float(resp.json().get("retry_after", 1.0))
                    except ValueError:
                        wait = float(resp.headers.get("Retry-After", 1.0))
                    reason = "rate limited"
                elif resp.status_code >= 500:
                    wait, reason = 2.0 ** attempt, f"Discord {resp.status_code}"
                else:
                    raise NotifyError(f"Discord rejected the message ({resp.status_code}): "
                                      f"{resp.text[:300]}")
            if attempt == self.max_attempts:
                raise NotifyError(f"Giving up after {attempt} attempts ({reason})")
            log.info("Discord %s; retrying in %.1fs", reason, wait)
            self.sleep(wait)

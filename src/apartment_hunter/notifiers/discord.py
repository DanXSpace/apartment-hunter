from __future__ import annotations

from collections.abc import Callable

from apartment_hunter.models import Listing
from apartment_hunter.sources import SOURCE_NAMES

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


def build_embed(listing: Listing, search_name: str, previous_price: int | None = None,
                currency: str = "$") -> dict:
    """The Alert embed. `currency` is the symbol of the Search's country ("$", "£", "€")."""

    def _price(v: float) -> str:
        return f"{currency}{int(v):,}"

    source = SOURCE_NAMES.get(listing.source, listing.source)
    if previous_price is not None and listing.price_min is not None:
        description = f"📉 Price drop on {source}: {_price(previous_price)} → {_price(listing.price_min)}"
        color = BLUE
    else:
        description = f"New listing on {source}"
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
        "footer": {"text": search_name},
        "timestamp": listing.scraped_at.isoformat(),
    }
    if listing.image_url:
        embed["image"] = {"url": listing.image_url}
    return embed

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from scrapling.parser import Selector

from apartment_hunter.fetchers import Fetcher, FetchError
from apartment_hunter.models import Listing, ScrapeResult
from apartment_hunter.scrapers import BlockedError, ScrapeError

log = logging.getLogger(__name__)

CARD_SELECTOR = "article.placard"
_NUM = r"\d+(?:\.\d+)?"


def page_url(url: str, page: int) -> str:
    """apartments.com paginates by path: /search/ -> /search/2/ (query string preserved)."""
    parts = urlsplit(url)
    path = re.sub(r"/\d+/?$", "/", parts.path)
    if not path.endswith("/"):
        path += "/"
    if page > 1:
        path += f"{page}/"
    return urlunsplit(parts._replace(path=path))


def parse_range(text: str | None) -> tuple[float | None, float | None]:
    """'2 - 3 bd' -> (2, 3); '1.5 ba' -> (1.5, 1.5); 'Studio - 2 bd' -> (0, 2)."""
    if not text:
        return None, None
    values = [0.0 if tok == "studio" else float(tok)
              for tok in re.findall(rf"studio|{_NUM}", text.lower())]
    if not values:
        return None, None
    return min(values), max(values)


def parse_prices(text: str | None) -> tuple[int | None, int | None]:
    """'Total Monthly Price: $1,454 - $2,216' -> (1454, 2216); 'Call for Rent' -> (None, None)."""
    if not text:
        return None, None
    values = [int(v.replace(",", "")) for v in re.findall(r"\$([\d,]+)", text)]
    if not values:
        return None, None
    return min(values), max(values)


def is_blocked(html: str, status: int | None) -> bool:
    if status == 403:
        return True
    head = html[:3000].lower()
    return "<title>access denied</title>" in head


def _first(card, selector: str, attr: str | None = None) -> str | None:
    found = card.css(selector)
    if not found:
        return None
    el = found[0]
    value = el.attrib.get(attr) if attr else el.get_all_text(strip=True)
    return value.strip() if value else None


def parse_card(card, scraped_at: datetime) -> Listing:
    listing_id = card.attrib.get("data-listingid")
    if not listing_id:
        raise ValueError("card has no data-listingid")

    btn = card.css(".listing-more-btn")
    attrs = btn[0].attrib if btn else {}
    try:
        share_lines = json.loads(attrs.get("data-listing-share-lines", "[]"))
    except json.JSONDecodeError:
        share_lines = []

    beds_line = next((s for s in share_lines if re.search(r"\b(bd|studio)\b", s, re.I)), None)
    baths_line = next((s for s in share_lines if re.search(rf"{_NUM}\s*ba\b", s)), None)
    price_line = next((s for s in share_lines if "$" in s), None)

    if beds_line is None:  # fall back to the visible rent rollup
        beds_line = " - ".join(t.get_all_text(strip=True) for t in card.css(".bedTextBox"))
    if price_line is None:
        price_line = " - ".join(t.get_all_text(strip=True) for t in card.css(".priceTextBox"))

    url = attrs.get("data-listing-url") or _first(card, "a.property-link", "href")
    if not url:
        raise ValueError(f"card {listing_id} has no URL")

    beds_min, beds_max = parse_range(beds_line)
    baths_min, baths_max = parse_range(baths_line)
    price_min, price_max = parse_prices(price_line)

    return Listing(
        listing_id=listing_id,
        url=url,
        name=attrs.get("data-listing-name") or _first(card, ".property-title") or "",
        address=attrs.get("data-listing-address") or _first(card, ".property-address") or "",
        price_min=price_min,
        price_max=price_max,
        beds_min=beds_min,
        beds_max=beds_max,
        baths_min=baths_min,
        baths_max=baths_max,
        image_url=attrs.get("data-listing-photo") or _first(card, "img.js-placard-photo", "src"),
        scraped_at=scraped_at,
    )


def parse_page(html: str, scraped_at: datetime | None = None) -> tuple[list[Listing], bool]:
    """Returns (listings, has_next_page). Cards that fail to parse are logged and skipped."""
    scraped_at = scraped_at or datetime.now()
    page = Selector(html)
    listings = []
    for card in page.css(CARD_SELECTOR):
        try:
            listings.append(parse_card(card, scraped_at))
        except ValueError as e:
            log.warning("Skipping card: %s", e)
    has_next = bool(page.css("#paging a.next"))
    return listings, has_next


class ApartmentsComScraper:
    def __init__(
        self,
        fetcher: Fetcher,
        max_pages: int = 10,
        page_delay: tuple[float, float] = (3.0, 8.0),
        fetch_attempts: int = 3,
        retry_backoff: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.fetcher = fetcher
        self.max_pages = max_pages
        self.page_delay = page_delay
        self.fetch_attempts = fetch_attempts
        self.retry_backoff = retry_backoff
        self.sleep = sleep

    def scrape(self, url: str) -> ScrapeResult:
        listings: dict[str, Listing] = {}
        page_num = 0
        has_next = True
        while has_next and page_num < self.max_pages:
            page_num += 1
            if page_num > 1:
                self.sleep(random.uniform(*self.page_delay))
            page_listings, has_next = parse_page(self._fetch_with_retries(page_url(url, page_num)))
            if page_num == 1 and not page_listings:
                raise ScrapeError("0 listings on page 1 (site layout may have changed)")
            if not page_listings:
                break
            for listing in page_listings:
                listings.setdefault(listing.listing_id, listing)

        hit_cap = has_next and page_num >= self.max_pages
        if hit_cap:
            log.warning("Stopped at the %d-page cap; this search is very broad", self.max_pages)
        return ScrapeResult(list(listings.values()), page_num, hit_cap)

    def _fetch_with_retries(self, url: str) -> str:
        last_error: ScrapeError | None = None
        for attempt in range(1, self.fetch_attempts + 1):
            try:
                result = self.fetcher.fetch(url, ready_selector=CARD_SELECTOR)
                if is_blocked(result.html, result.status):
                    raise BlockedError("Blocked by apartments.com bot protection (Access Denied)")
                return result.html
            except FetchError as e:
                last_error = ScrapeError(str(e))
            except BlockedError as e:
                last_error = e
            log.warning("Fetch attempt %d/%d failed: %s", attempt, self.fetch_attempts, last_error)
            if attempt < self.fetch_attempts:
                self.sleep(self.retry_backoff * 2 ** (attempt - 1))
        assert last_error is not None
        raise last_error

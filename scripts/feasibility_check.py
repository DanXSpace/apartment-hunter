"""Feasibility check: do Zillow and Craigslist keep answering plain requests (no browser)?

Polls six realistic searches every ~15 minutes, appends one CSV row per request and, at
the end, prints one verdict per Source. A Source passes unless one of its checks fails in
two rounds in a row (an occasional failure that clears on the next try is fine).

    uv run python scripts/feasibility_check.py [--rounds 16] [--gap-minutes 15] [--out data/feasibility.csv]

Standalone on purpose (no imports from apartment_hunter), so it can re-check any host.
"""

import argparse
import csv
import json
import random
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from curl_cffi import requests

SOURCES = ("zillow", "craigslist")
BASE = {
    "sortSelection": {"value": "days"},
    "isForRent": {"value": True},
    "isForSaleByAgent": {"value": False},
    "isForSaleByOwner": {"value": False},
    "isNewConstruction": {"value": False},
    "isComingSoon": {"value": False},
    "isAuction": {"value": False},
    "isForSaleForeclosure": {"value": False},
}
NOT_HOUSES = ("isApartment", "isCondo", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand")
BLOCK_MARKERS = ("px-captcha", "Press & Hold", "Access Denied", "captcha-delivery")
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)


def zillow_url(region_id: int, region_type: int, extra: dict[str, object]) -> str:
    state = {
        "pagination": {},
        "isMapVisible": False,
        "isListVisible": True,
        "regionSelection": [{"regionId": region_id, "regionType": region_type}],
        "filterState": BASE | extra,
    }
    return "https://www.zillow.com/homes/for_rent/?searchQueryState=" + urllib.parse.quote(
        json.dumps(state, separators=(",", ":"))
    )


@dataclass(frozen=True)
class Check:
    source: str
    name: str
    url: str


CHECKS = (
    Check(
        "zillow",
        "chicago-2bd",
        zillow_url(17426, 6, {"monthlyPayment": {"max": 2400}, "beds": {"min": 2}}),
    ),
    Check("zillow", "lincoln-park", zillow_url(269590, 8, {})),
    Check("zillow", "chicago-houses", zillow_url(17426, 6, {k: {"value": False} for k in NOT_HOUSES})),
    Check(
        "craigslist",
        "lincoln-park-2bd-dogs",
        "https://chicago.craigslist.org/search/apa?sort=date&lat=41.9237&lon=-87.6505"
        "&search_distance=2&max_price=2400&min_bedrooms=2&pets_dog=1",
    ),
    Check("craigslist", "chicago-all", "https://chicago.craigslist.org/search/apa?sort=date"),
    Check("craigslist", "chicago-houses", "https://chicago.craigslist.org/search/apa?sort=date&housing_type=6"),
)


@dataclass(frozen=True)
class Answer:
    status: int  # 0 when the request got no answer at all (timeout, connection error)
    blocked: bool
    listings: int | None  # None when the page doesn't carry the listing data
    failed: bool


def zillow_listings(body: str) -> int | None:
    match = NEXT_DATA.search(body)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
        return len(data["props"]["pageProps"]["searchPageState"]["cat1"]["searchResults"]["listResults"])
    except (KeyError, TypeError, ValueError):
        return None


def ask(check: Check) -> Answer:
    try:
        response = requests.get(check.url, impersonate="chrome", timeout=25)
    except Exception as error:  # any failure to get an answer counts as a failed request
        print(f"    no answer: {error.__class__.__name__}: {error}", flush=True)
        return Answer(0, False, None, True)
    body = response.text
    blocked = response.status_code in (403, 429) or any(marker in body for marker in BLOCK_MARKERS)
    if check.source == "zillow":
        listings = zillow_listings(body)
        layout_missing = NEXT_DATA.search(body) is None
    else:
        listings = body.count('<li class="cl-static-search-result"')
        layout_missing = False
    failed = blocked or response.status_code != 200 or layout_missing
    return Answer(response.status_code, blocked, listings, failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poll Zillow and Craigslist without a browser.")
    parser.add_argument("--rounds", type=int, default=16)
    parser.add_argument("--gap-minutes", type=float, default=15)
    parser.add_argument("--out", type=Path, default=Path("data/feasibility.csv"))
    args = parser.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not args.out.exists()
    streak = dict.fromkeys(CHECKS, 0)  # consecutive failed rounds, per check
    longest = dict.fromkeys(SOURCES, 0)  # longest such streak, per Source
    failed_rounds = dict.fromkeys(SOURCES, 0)  # rounds where any of the Source's checks failed

    with args.out.open("a", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        if new_file:
            writer.writerow(["time", "source", "check", "status", "blocked", "listings"])
        for round_number in range(1, args.rounds + 1):
            if round_number > 1:
                time.sleep(args.gap_minutes * 60 * random.uniform(0.8, 1.2))
            print(f"Round {round_number}/{args.rounds}", flush=True)
            failed_this_round = dict.fromkeys(SOURCES, False)
            for index, check in enumerate(CHECKS):
                if index:
                    time.sleep(random.uniform(3, 8))
                answer = ask(check)
                listings = "" if answer.listings is None else str(answer.listings)
                writer.writerow(
                    [
                        datetime.now(UTC).isoformat(timespec="seconds"),
                        check.source,
                        check.name,
                        answer.status,
                        str(answer.blocked).lower(),
                        listings,
                    ]
                )
                out.flush()
                verdict = "FAILED" if answer.failed else "ok"
                blocked = " blocked" if answer.blocked else ""
                print(
                    f"  {check.source:<10} {check.name:<22} HTTP {answer.status}{blocked} "
                    f"listings={listings or '-'} {verdict}",
                    flush=True,
                )
                if answer.failed:
                    streak[check] += 1
                    failed_this_round[check.source] = True
                    longest[check.source] = max(longest[check.source], streak[check])
                else:
                    streak[check] = 0
            for source in SOURCES:
                failed_rounds[source] += failed_this_round[source]

    for source in SOURCES:
        if longest[source] >= 2:
            print(f"{source} FAIL ({failed_rounds[source]} failed rounds, longest streak {longest[source]})")
        else:
            print(f"{source} PASS")
    return 0 if all(longest[source] < 2 for source in SOURCES) else 1


if __name__ == "__main__":
    raise SystemExit(main())

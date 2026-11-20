# Apartment Hunter Discord Bot Implementation Plan

> **For agentic workers:** the owner uses mattpocock-skills, not superpowers: build each task test-first with the `tdd` skill and review it with `code-review` (standards and spec) before starting the next. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the browser-based apartments.com watcher with a Discord bot that people add to their own server, set criteria with `/search`, and get new Zillow and Craigslist listings posted, with no browser anywhere.

**Architecture:** One process on one always-on host: a discord.py bot (thin) over a `SearchService` (the test seam) that owns a SQLite `Store`, a sequential `Scheduler` and a `SearchRunner`. Sources (Zillow, Craigslist) turn `Criteria` into plain HTTP requests (curl_cffi with Chrome's TLS fingerprint) and parse results into `Listing`s. Runs happen on the scheduler thread and post to Discord through the bot's event loop.

**Tech Stack:** Python 3.12, discord.py ≥ 2.7.1, curl_cffi ≥ 0.16.3, Scrapling (parser only), SQLite, uv, pytest (+ pytest-socket, pytest-timeout), ruff, mypy, Docker.

**Spec:** `docs/superpowers/specs/2026-11-17-discord-bot-design.md`

**Order note:** the old paths are retired first (Task 2), on the `discord-bot` branch. `main` keeps v0.2 until the branch merges, so nobody using `main` loses a working tool, and no task has to keep the old CLI compiling while shared modules are rewritten.

## Progress

Built on 2026-11-19 on the owner's computer with the mattpocock-skills flow: spec issue #22, tickets #23–#32, one `tdd` implementer per ticket, a whole-branch two-axis `code-review` and one fix wave (`data/build/final-fixes.md`, local). Commits use the owner's GitHub no-reply address; in a fresh clone, set it first with `git config user.name DanXSpace` and `git config user.email 17147033+DanXSpace@users.noreply.github.com`.

- **Task 1:** the home feasibility run passed on 2026-11-19 (16 rounds, Zillow and Craigslist both PASS); the cloud run was not tested (the owner said no). Step 7's gate is open.
- **Tasks 2–15:** done and merged into `discord-bot`; the owner's manual check in a test server passed. 150 tests, ruff and mypy green.

Decisions made along the way:
- The re-lock in Task 2 moved `w3lib` to 2.6.0, which brings in `frostwork` 0.2.2 (Zyte's, from the Scrapy project); accepted rather than pinning `w3lib`.
- `v0.2-apartments-com` stays a lightweight tag, matching Task 15's REST call.
- Task 2's test dataclass `S` and the `Schedulable` protocol stay as they are; Task 9 replaces both.
- Task 3 has one test beyond the four it names, for its "no trailing `.0`" rule.

Carry forward:
- Tasks 10 and 11: `summarize` shows any number it's given, so a minimum of 0 reads "from $0" or "0+ ba" (only `min_beds == 0` reads "studio+"). The commands pass None where they mean "no minimum".
- Task 15: `CODING_STANDARDS.md` still cites `tests/test_runner.py` (deleted) and apartments.com; `GLOSSARY.md`'s Alert, Blocked, Needs attention and Data directory entries still describe the webhook, apartments.com, the desktop app and the browser profile; the `Listing` docstring says "Call for Rent".

## Global Constraints

- No browser anywhere: no Playwright, Patchright, Chromium or Xvfb in dependencies, code or the Docker image. HTTP goes through curl_cffi with `impersonate="chrome"`; Scrapling stays only as the HTML parser (`scrapling` without the `fetchers` extra, which would pull Playwright back in).
- Regular Run: every **15 minutes ± 20%** per Search (`INTERVAL_MINUTES`, minimum **10**); users can't change it.
- Limits: **5** Searches per Discord server, **25** in total (`MAX_SEARCHES_PER_SERVER`, `MAX_SEARCHES_TOTAL`).
- Seeding run reads up to **3** pages per Source; a regular Run reads **page 1**, newest first.
- Burst cap: at most **10** Alerts per Search per Run; the rest are summarised in one message per Source.
- Health: **3** failed Runs in a row per (Search, Source) → Health alert; the owner gets a direct message when a Source has failed **3** Runs in a row in every Search that uses it.
- `/search check`: at most once per **5 minutes** per Search.
- Area lookups: start at **3** characters, cached, throttled to one request per **2 seconds** bot-wide, with a **2.5-second** HTTP timeout (Discord autocomplete must answer within 3 seconds).
- Polite requests for Runs: one at a time; a random **3–8 second** pause between requests to the same host.
- Discord: `guilds` intent only; permissions View Channel + Send Messages + Embed Links = **19456**; scopes `bot applications.commands`; acknowledge an interaction within **3 s**, edit its reply for up to **15 min** (wait at most **840 s** for a seeding outcome).
- Search names are unique within a server; a Search's identity is its id. Renaming never reseeds; changing Criteria or Sources does.
- All datetimes are timezone-aware UTC (`datetime.now(UTC)`).
- User-facing text is plain language, and exact copy is pinned in the tasks below. Code uses the glossary's terms (Search, Listing, Run, Seeding run, Alert, Health alert, Source, Criteria).
- Tests (from `CODING_STANDARDS.md`): test at the seam, hand-written fakes in the test file, no mocking libraries, injected clocks, no real threads or sleeps, never touch Zillow, Craigslist or Discord. pytest already blocks non-loopback sockets, with a 10 s timeout.
- Each task ends green on `uv run ruff check`, `uv run mypy` and `uv run pytest -q`.
- Every commit message ends with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The owner approved pushing `discord-bot` itself (2026-11-17); pushing anything else, tagging remotely, opening a PR or changing GitHub issues waits for their go-ahead.
- Change dependencies with `uvx uv@0.12.19 add …` / `remove …`, the uv the Dockerfile pins, so `uv.lock` stays at revision 3 for the image's `uv sync --frozen`; other uv versions rewrite the whole file. `uvx uv@0.12.19 lock --check` passes after each change.

## Review Focus

1. **A Search with zero matches** (narrow criteria, small town) is healthy and seeds normally. It's never "Needs attention" and never an error. Tests: Task 6 `test_zero_matches_is_an_empty_page_not_an_error`, Task 8 `test_zero_matches_is_healthy`.
2. **An area that is a street address, a state, or nonsense** is refused with the plain "couldn't find a place" message, and nothing is saved. Tests: Task 7 `test_only_cities_neighborhoods_and_zip_codes_are_offered`, `test_resolve_with_no_usable_place_raises`; Task 10 `test_create_refuses_an_unknown_area_and_saves_nothing`.
3. **A listing with no price** (Zillow "Contact for price", a Craigslist post without a price) parses with `price_min=None`, shows "Call for price", and is never a price drop. Tests: Task 6 and Task 13 `test_missing_price_is_none`, Task 8 `test_unknown_price_is_never_a_price_drop`.
4. **The alert channel is deleted, or the bot loses permission, during a Run.** The Run stops, nothing unsent is marked seen, the Search is paused with a reason, and its creator is told. Tests: Task 8 `test_channel_gone_stops_the_run_without_marking_seen`, Task 10 `test_channel_gone_pauses_the_search_and_tells_its_creator`.
5. **Place names with apostrophes, commas and spaces** ("Coeur d'Alene, ID") are URL-encoded in lookups. Test: Task 7 `test_suggestions_url_encodes_the_text`.

## Running in a cloud session

The owner set this plan up to run in a Claude Code cloud session on `discord-bot` (2026-11-17). A cloud machine reaches PyPI, GitHub and Docker Hub but not Zillow, Craigslist or Discord, so the steps that needed the owner's home connection were done from it beforehand and are checked off: Task 1 Steps 1–4 and the real-page captures in Tasks 6, 7 and 13. In a cloud session:

- **Skills:** the mattpocock-skills named at the top are installed on the owner's computer, not in cloud sessions. Without them, work through each task's checkbox steps in order.
- **Pushing:** commit to `discord-bot` and push it after every task, so a reclaimed machine loses nothing.
- **Feasibility (Task 1 Steps 5–7):** the session starts and reads the cloud run through GitHub Actions; the home run, if it's needed, is the owner's.
- **Owner checks:** the Discord test-server checks in Tasks 11, 12 and 13 need the bot running where it can reach Discord, with the owner's token, so they happen together at the end on the owner's computer (Task 15 Step 6b).
- **Docker (Task 14 Step 4):** Docker is installed; start the daemon if `docker info` says it isn't running. If the build stops at `apt-get` (Debian's mirrors are outside the cloud's network allowlist), note that the PR's CI docker job covers it.
- **GitHub (Task 15 Step 7):** use `gh api` (REST) or the built-in GitHub tools; the session's GitHub proxy refuses `gh pr` and `gh issue` (GraphQL), tag pushes and branch deletions.

---

### Task 1: Feasibility check (no browser, home and cloud)

**Files:**
- Create: `scripts/feasibility_check.py`, `.github/workflows/feasibility.yml` (Step 5)
- Modify: `pyproject.toml`, `uv.lock` (add curl-cffi)

**Interfaces:**
- Produces: `uv run python scripts/feasibility_check.py [--rounds N] [--gap-minutes M] [--out PATH]`; exit 0 when every Source passes. Throwaway diagnostic (kept for re-checking a host), so no unit tests.

- [x] **Step 1: Add curl-cffi** (done 2026-11-17)

Run: `uvx uv@0.12.19 add 'curl-cffi>=0.16.3'`
Expected: `pyproject.toml` lists `curl-cffi>=0.16.3`; `uv.lock` gains two lines (Scrapling already pulled curl-cffi in).

- [x] **Step 2: Write `scripts/feasibility_check.py`** (done 2026-11-17)

Standalone (no imports from `apartment_hunter`). For each round, GET these six URLs with `curl_cffi.requests.get(url, impersonate="chrome", timeout=25)`, with a random 3–8 s pause between requests:

- Zillow: `https://www.zillow.com/homes/for_rent/?searchQueryState=<urllib.parse.quote(json.dumps(state, separators=(",", ":")))>`, where `state = {"pagination": {}, "isMapVisible": False, "isListVisible": True, "regionSelection": [{"regionId": R, "regionType": T}], "filterState": BASE | extra}`, `BASE = {"sortSelection": {"value": "days"}, "isForRent": {"value": True}, "isForSaleByAgent": {"value": False}, "isForSaleByOwner": {"value": False}, "isNewConstruction": {"value": False}, "isComingSoon": {"value": False}, "isAuction": {"value": False}, "isForSaleForeclosure": {"value": False}}`, for:
  - `chicago-2bd`: R=17426, T=6, extra `{"monthlyPayment": {"max": 2400}, "beds": {"min": 2}}`
  - `lincoln-park`: R=269590, T=8, extra `{}`
  - `chicago-houses`: R=17426, T=6, extra `{k: {"value": False} for k in ("isApartment", "isCondo", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand")}`
- Craigslist:
  - `lincoln-park-2bd-dogs`: `https://chicago.craigslist.org/search/apa?sort=date&lat=41.9237&lon=-87.6505&search_distance=2&max_price=2400&min_bedrooms=2&pets_dog=1`
  - `chicago-all`: `https://chicago.craigslist.org/search/apa?sort=date`
  - `chicago-houses`: `https://chicago.craigslist.org/search/apa?sort=date&housing_type=6`

Per request, append a CSV row `time,source,check,status,blocked,listings` to `--out` (default `data/feasibility.csv`; create its folder if missing). `blocked` is true for HTTP 403/429 or when the body contains `px-captcha`, `Press & Hold`, `Access Denied` or `captcha-delivery`. `listings` is `len(listResults)` from the `<script id="__NEXT_DATA__">` JSON at `props.pageProps.searchPageState.cat1.searchResults.listResults` for Zillow (empty when absent), and the count of `<li class="cl-static-search-result"` for Craigslist. A request **fails** when it is blocked, not HTTP 200, or (Zillow only) has no `__NEXT_DATA__`; zero listings is not a failure.

Rounds: `--rounds` (default 16), separated by `--gap-minutes` (default 15) × `random.uniform(0.8, 1.2)`. At the end, print one line per Source: `zillow PASS` / `zillow FAIL (n failed rounds, longest streak k)`. A Source **passes** unless any check failed in two consecutive rounds (the spec's "an occasional failure that clears on the next try is fine"). Exit 0 when both pass, else 1.

- [x] **Step 3: Smoke-run one round** (done 2026-11-17 from the owner's home connection: all six HTTP 200; listings 41, 41, 41 on Zillow and 6, 319, 76 on Craigslist; both PASS)

Run: `uv run python scripts/feasibility_check.py --rounds 1 --gap-minutes 0`
Expected: six rows with status 200 and listings > 0 (for `chicago-2bd` around 41), then `zillow PASS` and `craigslist PASS`.

- [x] **Step 4: Commit** (done 2026-11-17)

```bash
git add scripts/feasibility_check.py pyproject.toml uv.lock
git commit -m "Feasibility check: poll Zillow and Craigslist without a browser"
```

- [ ] **Step 5: Cloud run on GitHub Actions, only with the owner's go-ahead**

Ask the owner: "OK to run a GitHub Actions job on your repository for about 4 hours? A GitHub server (a cloud computer) checks Zillow and Craigslist 16 times, 15 minutes apart, to learn whether the bot can live in the cloud. It's free on a public repository." If no, record "cloud: not tested" and go to Step 6. If yes, add `.github/workflows/feasibility.yml` to `discord-bot`, commit it and push `discord-bot`. That push starts it; later pushes don't unless they change this file (once merged, it can also be started from the Actions tab):

```yaml
name: Feasibility check
on:
  push:
    branches: [discord-bot]
    paths: [.github/workflows/feasibility.yml]
  workflow_dispatch:
jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v10.3.3
        with:
          python-version: "3.12"
      - shell: bash
        run: uv run --no-project --with 'curl-cffi>=0.16.3' python scripts/feasibility_check.py --rounds 1 --gap-minutes 0 | tee feasibility.log
      - if: always()
        run: grep -E '^ +(zillow|craigslist) ' feasibility.log | sed -E 's/^ +/::notice title=smoke::/'
  sustained:
    needs: smoke
    runs-on: ubuntu-latest
    timeout-minutes: 330
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v10.3.3
        with:
          python-version: "3.12"
      - shell: bash
        run: uv run --no-project --with 'curl-cffi>=0.16.3' python scripts/feasibility_check.py --rounds 16 --gap-minutes 15 --out feasibility.csv | tee feasibility.log
      - if: always()
        run: grep -E '^(zillow|craigslist) ' feasibility.log | sed -E 's/^/::notice title=sustained::/'
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: feasibility
          path: feasibility.csv
```

`shell: bash` turns on `pipefail`, so a `FAIL` (exit 1) fails the job despite the `tee`. Read the results through the REST API, which works from a cloud session too: the run with `gh api "repos/DanXSpace/apartment-hunter/actions/workflows/feasibility.yml/runs?branch=discord-bot" --jq '.workflow_runs[0] | {id, status, conclusion}'`, its jobs with `gh api repos/DanXSpace/apartment-hunter/actions/runs/<run id>/jobs --jq '.jobs[] | {name, id, status, conclusion}'`, and a finished job's lines with `gh api repos/DanXSpace/apartment-hunter/check-runs/<job id>/annotations --jq '.[].message'`. The smoke job's six lines arrive within minutes; if Zillow is already blocked there (HTTP 403/429 or `blocked`), tell the owner right away so Step 6 can start early. Tasks 2–5 don't depend on the result and proceed meanwhile.

- [ ] **Step 6: Home run, only if the cloud run didn't pass or wasn't allowed (about 4 hours)**

The same 16 rounds from the owner's home connection. On the owner's computer, run it in the background with `mkdir -p data && uv run python scripts/feasibility_check.py --rounds 16 > data/feasibility-home.log 2>&1` and read the last two lines when it ends. A cloud session can't reach Zillow or Craigslist, so it asks the owner to run this on their computer, kept awake, from a checkout of `discord-bot` (`git clone -b discord-bot https://github.com/DanXSpace/apartment-hunter.git`), and to paste back the last two lines.

- [ ] **Step 7: Gate (do this before starting Task 6)**

Record the results in the task notes; Task 15 uses them for the README's hosting section.
- Cloud run shows `zillow PASS` and `craigslist PASS` → hosting: cloud. A cloud server that keeps answering under sustained polling also settles the home question, so Step 6 isn't needed.
- Otherwise the home run decides. Home `zillow FAIL` → **stop**: report the results to the owner and revisit the Source mix together (spec, Hosting); don't start Task 6. Home `zillow PASS` → hosting: the owner's place (note any Craigslist failure for Task 13 and the README).

---

### Task 2: Retire v0.2 (CLI, desktop app, apartments.com, browser)

**Files:**
- Delete: `src/apartment_hunter/{cli.py,desktop.py,config.py,fetchers.py,runner.py}`, `src/apartment_hunter/web/`, `src/apartment_hunter/scrapers/`, `tests/{test_cli.py,test_web.py,test_config.py,test_apartments_com.py,test_runner.py}`, `tests/fixtures/apartments_com/`, `config.example.toml`, `.claude/skills/run-app/` (the desktop-app run recipe)
- Modify: `src/apartment_hunter/notifiers/discord.py`, `src/apartment_hunter/appdata.py`, `src/apartment_hunter/models.py`, `src/apartment_hunter/scheduler.py`, `tests/test_discord.py`, `tests/test_appdata.py`, `tests/test_scheduler.py`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`

**Interfaces:**
- Produces: `DataDir(root)` with only `database`, `log_file`, `lock_file`, `log_handler()`, `lock()`; `notifiers.discord.build_embed` (unchanged for now); `models.Listing` (unchanged for now). `ScrapeResult` and `DiscordNotifier` are gone. The Dockerfile and its CI job go stale until Task 14 (CI only runs on `main` and on PRs, and no PR opens before then).

- [x] **Step 1: Tag v0.2 locally**

Run: `git tag v0.2-apartments-com 5fddee608c85fa8ec7676a97280d24f74a3a8236` (the tip of `main`, which is v0.2; a fresh clone may have no local `main`)
Expected: `git tag` lists it (pushing it waits for the owner; see Task 15).

- [x] **Step 2: Delete the old application layer**

Run: `git rm -r src/apartment_hunter/cli.py src/apartment_hunter/desktop.py src/apartment_hunter/config.py src/apartment_hunter/fetchers.py src/apartment_hunter/runner.py src/apartment_hunter/web src/apartment_hunter/scrapers tests/test_cli.py tests/test_web.py tests/test_config.py tests/test_apartments_com.py tests/test_runner.py tests/fixtures/apartments_com config.example.toml .claude/skills/run-app`

- [x] **Step 3: Trim what stays**

- `notifiers/discord.py`: delete `DiscordNotifier` and its imports (`requests`, `time`, `Callable` if unused); keep `build_embed` and its helpers.
- `tests/test_discord.py`: delete `Resp`, `_patch`, `test_retries_on_rate_limit`, `test_client_error_raises_without_retry`, `test_gives_up_after_max_attempts`, `test_webhook_check` and the `requests` import.
- `appdata.py`: delete `app_data()`, `config`, `browser_profile`, `port_file` and the `platformdirs` import; docstring becomes "A directory of bot state: the database, logs and the single-instance lock."
- `tests/test_appdata.py`: delete `test_app_data_dir_is_the_per_user_data_location`. `tests/conftest.py`: delete the `app_data_root` fixture (and `sys` if unused).
- `models.py`: delete `ScrapeResult`.
- `scheduler.py`: replace `from apartment_hunter.config import Search` with a local `class Schedulable(Protocol): name: str; interval_minutes: float` used in its signatures (Task 9 rewrites the scheduler).
- `tests/test_scheduler.py`: replace the `config.Search` import with a local `@dataclass(frozen=True) class S: name: str; interval_minutes: float` and `A = S("A", 15)`, `B = S("B", 30)`.

- [x] **Step 4: Trim dependencies**

Run: `uvx uv@0.12.19 remove playwright fastapi jinja2 uvicorn platformdirs requests scrapling && uvx uv@0.12.19 add 'scrapling>=0.5.1' && uvx uv@0.12.19 remove --dev httpx`
Then delete the two `[project.scripts]` entries from `pyproject.toml` (Task 11 adds the bot's) and set `description = "A Discord bot that posts new rental listings from Zillow and Craigslist"`. Keep the `playwright` entry in ruff's banned-api list.

- [x] **Step 5: Verify**

Run: `uv sync && uv run ruff check && uv run mypy && uv run pytest -q`
Expected: all pass (the appdata, embed and scheduler tests remain).

- [x] **Step 6: Commit**

```bash
git add -A
git commit -m "Retire the v0.2 CLI, desktop app, apartments.com scraper and browser (kept at tag v0.2-apartments-com)"
```

---

### Task 3: Domain models: Area, Criteria, Search, Listing.source

**Files:**
- Create: `src/apartment_hunter/criteria.py`
- Modify: `src/apartment_hunter/models.py`, `tests/conftest.py`
- Test: `tests/test_criteria.py`

**Interfaces:**
- Produces (in `criteria.py`):
  - `class HomeType(StrEnum)`: `ANY="any"`, `APARTMENT_CONDO="apartment_condo"`, `HOUSE="house"`, `TOWNHOME="townhome"`
  - `class Pets(StrEnum)`: `NONE="none"`, `DOGS="dogs"`, `CATS="cats"`
  - `@dataclass(frozen=True) class Area: display: str; kind: str; zillow_region_id: int; zillow_region_type: int; lat: float; lon: float; craigslist_site: str; craigslist_radius_miles: int` (`kind` is `"city"`, `"neighborhood"` or `"zipcode"`)
  - `@dataclass(frozen=True) class Criteria: area: Area; min_price: int | None = None; max_price: int | None = None; min_beds: float | None = None; min_baths: float | None = None; home_type: HomeType = HomeType.ANY; pets: Pets = Pets.NONE`
  - `summarize(criteria: Criteria) -> str`, `criteria_to_json(criteria: Criteria) -> str`, `criteria_from_json(text: str) -> Criteria`
- Produces (in `models.py`): `Listing` gains a first field `source: str` (a Source key: `"zillow"`, `"craigslist"`); `@dataclass(frozen=True) class Search: id: str; server_id: int; channel_id: int; created_by: int; name: str; criteria: Criteria; sources: tuple[str, ...]; created_at: datetime; paused: bool = False; attention: str | None = None`
- Produces (in `tests/conftest.py`): `AT = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)`; `CHICAGO = Area("Chicago, IL", "city", 17426, 6, 41.8781, -87.6298, "chicago", 10)`; `LINCOLN_PARK = Area("Lincoln Park, Chicago, IL", "neighborhood", 269590, 8, 41.9237, -87.6505, "chicago", 2)`; `make_listing(listing_id="abc", price=2000, source="zillow", **overrides)` (url `https://www.zillow.com/homedetails/{listing_id}/`, `scraped_at=AT`); `make_search(search_id="s1", server_id=1, name="Chicago", sources=("zillow",), **overrides)` (channel 10, created_by 99, `Criteria(CHICAGO)`, `created_at=AT`)

- [x] **Step 1: Write the failing tests** (`tests/test_criteria.py`)

```python
def test_summary_lists_criteria_in_a_fixed_order():
    c = Criteria(CHICAGO, max_price=2400, min_beds=2, pets=Pets.DOGS)
    assert summarize(c) == "Chicago, IL · 2+ bd · up to $2,400 · dogs"

def test_summary_formats_every_field():
    c = Criteria(CHICAGO, min_price=1500, max_price=2400, min_beds=0, min_baths=1.5,
                 home_type=HomeType.HOUSE, pets=Pets.CATS)
    assert summarize(c) == "Chicago, IL · studio+ · 1.5+ ba · $1,500–$2,400 · houses · cats"

def test_summary_min_price_only_other_home_types_and_area_only():
    assert summarize(Criteria(CHICAGO, min_price=1500)) == "Chicago, IL · from $1,500"
    assert summarize(Criteria(CHICAGO, home_type=HomeType.APARTMENT_CONDO)) == "Chicago, IL · apartments & condos"
    assert summarize(Criteria(CHICAGO, home_type=HomeType.TOWNHOME)) == "Chicago, IL · townhomes"
    assert summarize(Criteria(CHICAGO)) == "Chicago, IL"

def test_criteria_round_trip_through_json():
    c = Criteria(LINCOLN_PARK, min_price=1500, max_price=2400, min_beds=2, min_baths=1,
                 home_type=HomeType.APARTMENT_CONDO, pets=Pets.DOGS)
    assert criteria_from_json(criteria_to_json(c)) == c
```

- [x] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_criteria.py -q`
Expected: FAIL (`ModuleNotFoundError: apartment_hunter.criteria`).

- [x] **Step 3: Implement `criteria.py`, extend `models.py`, update `conftest.py`**

Summary order: area · beds · baths · price · home type · pets, joined with `" · "`. Beds: `"studio+"` for 0, else `"{n}+ bd"`; baths `"{n}+ ba"` (no trailing `.0`); price `"$a–$b"` / `"up to $b"` / `"from $a"` with thousands separators; home type labels `apartments & condos` / `houses` / `townhomes`; pets `dogs` / `cats`; `ANY` and `NONE` add nothing. JSON: `dataclasses.asdict` plus enum values; the reverse rebuilds `Area` and enums.

- [x] **Step 4: Verify**

Run: `uv run pytest -q && uv run ruff check && uv run mypy`
Expected: PASS (the embed tests still pass with `make_listing`'s new `source`).

- [x] **Step 5: Commit**

```bash
git add src/apartment_hunter/criteria.py src/apartment_hunter/models.py tests/conftest.py tests/test_criteria.py
git commit -m "Add Criteria, Area and Search models; Listings know their Source"
```

---

### Task 4: Sources foundation and polite HTTP

**Files:**
- Create: `src/apartment_hunter/sources/__init__.py`, `src/apartment_hunter/sources/http.py`, `tests/fakes.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: `Criteria` (Task 3), `Listing` (Task 3)
- Produces (`sources/__init__.py`): `SOURCE_NAMES = {"zillow": "Zillow", "craigslist": "Craigslist"}`; `class ScrapeError(Exception)` (message is a plain-language reason); `class BlockedError(ScrapeError)`; `@dataclass(frozen=True) class SourcePage: listings: list[Listing]; total: int | None; has_more: bool`; `class Source(Protocol): key: str; def fetch(self, criteria: Criteria, page: int) -> SourcePage; def results_url(self, criteria: Criteria) -> str`
- Produces (`sources/http.py`): `@dataclass(frozen=True) class HttpResponse: status: int; text: str`; `class HttpClient(Protocol): def get(self, url: str) -> HttpResponse`; `class CurlHttpClient(timeout: float = 25.0, impersonate: str = "chrome")` whose network errors raise `ScrapeError(f"Couldn't reach {host}: {error}")`; `class PoliteHttpClient(inner: HttpClient, gap_seconds: tuple[float, float] = (3.0, 8.0), clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep, rand: Callable[[float, float], float] = random.uniform)`

- [ ] **Step 1: Write the failing tests.** First create `tests/fakes.py` with `FakeHttp(response: HttpResponse | str = HttpResponse(200, "ok"))`: it returns that response for every URL (a str means HTTP 200 with that body) and records URLs in `.requested`. Later tasks add their fakes to this file. Then write `tests/test_http.py`, with a local `FakeClock` whose `advance` takes seconds:

```python
def test_waits_out_the_gap_between_requests_to_the_same_host():
    inner, clock, slept = FakeHttp(), FakeClock(), []
    def sleep(s): slept.append(s); clock.advance(s)
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_http.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement both modules**

`PoliteHttpClient` keeps the last request time per host (`urlsplit(url).hostname`) and draws a fresh gap with `rand(*gap_seconds)` for each request. Runs are sequential, so it needs no lock. `CurlHttpClient.get` uses one `curl_cffi.requests.Session(impersonate=...)` and returns `HttpResponse(r.status_code, r.text)`.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q && uv run ruff check && uv run mypy` → PASS

- [ ] **Step 5: Commit**

```bash
git add src/apartment_hunter/sources tests/test_http.py tests/fakes.py
git commit -m "Add the Source interface and polite curl_cffi HTTP client"
```

---

### Task 5: Store for many servers

**Files:**
- Modify (rewrite): `src/apartment_hunter/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `Search`, `Listing` (Task 3), `criteria_to_json` / `criteria_from_json` (Task 3)
- Produces: `@dataclass(frozen=True) class SeenListing: listing_id: str; price_min: int | None`; `@dataclass(frozen=True) class SourceHealth: seeded: bool; consecutive_failures: int; broken_alert_sent: bool; last_error: str | None`; `class DuplicateName(Exception)`; `class Store(path: Path | str, now: Callable[[], datetime] = lambda: datetime.now(UTC))` with:
  - `close()`, `add_search(search: Search) -> None` (raises `DuplicateName`), `get_search(search_id: str) -> Search | None`, `find_search(server_id: int, name: str) -> Search | None` (case-insensitive name), `searches(server_id: int | None = None) -> list[Search]` (oldest first), `update_search(search: Search, reseed: bool) -> None` (raises `DuplicateName` on a rename clash; `reseed=True` deletes the Search's listings and source health), `delete_search(search_id: str) -> None` (with its listings and health), `record_run(search_id: str) -> None`, `last_run(search_id: str) -> datetime | None`
  - `source_health(search_id: str, source: str) -> SourceHealth`, `mark_seeded(search_id, source)`, `record_failure(search_id, source, error: str) -> int` (new streak length), `record_success(search_id, source)` (streak 0, `broken_alert_sent` false, `last_error` None), `mark_broken_alert_sent(search_id, source)`
  - `get_listing(search_id, source, listing_id) -> SeenListing | None`, `save_listing(search_id: str, listing: Listing) -> None` (keyed by `listing.source`; keeps the last known price when the new one is None), `listing_count(search_id) -> int`
  - `owner_alert_sent(source: str) -> bool`, `set_owner_alert_sent(source: str, sent: bool) -> None`
- Schema: tables `searches` (id PK, server_id, channel_id, created_by, name, criteria JSON, sources comma-joined, created_at, paused, attention, last_run, `UNIQUE(server_id, name COLLATE NOCASE)`), `source_health` (PK search_id+source), `listings` (PK search_id+source+listing_id), `owner_alerts` (PK source), `meta`. Same thread-safety as today (`_synchronized`, one connection). No migration from v0.2 databases.

- [ ] **Step 1: Write the failing tests** (`tests/test_storage.py`, fixture `store = Store(":memory:")`)

```python
def test_searches_round_trip_and_list_per_server(store):
    a, b, c = make_search("a", 1, "A"), make_search("b", 1, "B"), make_search("c", 2, "C")
    for s in (a, b, c):
        store.add_search(s)
    assert store.get_search("a") == a
    assert [s.name for s in store.searches(1)] == ["A", "B"]
    assert len(store.searches()) == 3
    assert store.find_search(1, "a") == a and store.find_search(2, "A") is None

def test_names_are_unique_within_a_server_only(store):
    store.add_search(make_search("a", 1, "Home"))
    with pytest.raises(DuplicateName):
        store.add_search(make_search("b", 1, "home"))
    store.add_search(make_search("c", 2, "Home"))

def test_reseed_clears_listings_and_health_but_a_rename_does_not(store):
    s = make_search()
    store.add_search(s)
    store.save_listing(s.id, make_listing("x"))
    store.mark_seeded(s.id, "zillow")
    store.update_search(replace(s, name="Renamed"), reseed=False)
    assert store.listing_count(s.id) == 1 and store.source_health(s.id, "zillow").seeded
    store.update_search(replace(s, name="Renamed"), reseed=True)
    assert store.listing_count(s.id) == 0 and not store.source_health(s.id, "zillow").seeded

def test_delete_removes_the_search_with_its_listings(store):
    s = make_search()
    store.add_search(s)
    store.save_listing(s.id, make_listing("x"))
    store.delete_search(s.id)
    assert store.get_search(s.id) is None and store.listing_count(s.id) == 0

def test_failures_count_per_source_and_reset_on_success(store):
    store.add_search(make_search())
    assert store.record_failure("s1", "zillow", "blocked") == 1
    assert store.record_failure("s1", "zillow", "blocked") == 2
    store.mark_broken_alert_sent("s1", "zillow")
    assert store.source_health("s1", "craigslist").consecutive_failures == 0
    store.record_success("s1", "zillow")
    assert store.source_health("s1", "zillow") == SourceHealth(False, 0, False, None)

def test_listings_are_keyed_by_source_and_keep_the_last_known_price(store):
    store.add_search(make_search())
    store.save_listing("s1", make_listing("x", 2000, source="zillow"))
    store.save_listing("s1", make_listing("x", 1500, source="craigslist"))
    store.save_listing("s1", make_listing("x", None, source="zillow"))
    assert store.listing_count("s1") == 2
    assert store.get_listing("s1", "zillow", "x") == SeenListing("x", 2000)
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_storage.py -q` → FAIL
- [ ] **Step 3: Rewrite `storage.py`** to the interface above (the v0.1 legacy import goes away).
- [ ] **Step 4: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/apartment_hunter/storage.py tests/test_storage.py
git commit -m "Store keeps Searches per Discord server, with per-Source health"
```

---

### Task 6: Zillow Source

**Gate first:** Task 1 Step 7 must have settled hosting (a cloud pass, or home `zillow PASS`). If it hasn't yet, wait for it; if home Zillow failed, stop.

**Files:**
- Create: `src/apartment_hunter/sources/zillow.py`
- Test: `tests/test_zillow.py`

**Interfaces:**
- Consumes: `Criteria`, `HomeType`, `Pets` (Task 3); `SourcePage`, `ScrapeError`, `BlockedError` (Task 4); `HttpClient`, `HttpResponse` (Task 4)
- Produces: `SEARCH_URL = "https://www.zillow.com/homes/for_rent/"`; `search_url(criteria: Criteria, page: int = 1) -> str`; `@dataclass(frozen=True) class ZillowCard: listing: Listing; home_type: str`; `parse_search_page(html: str, scraped_at: datetime) -> tuple[list[ZillowCard], int | None, bool]` (cards, total, has_more); `matches_home_type(home_type: str, wanted: HomeType) -> bool`; `class ZillowSource(http: HttpClient, now: Callable[[], datetime] = lambda: datetime.now(UTC))` with `key = "zillow"`, `fetch(criteria, page) -> SourcePage`, `results_url(criteria) -> str` (= `search_url(criteria, 1)`)
- Pinned request format (verified against Zillow on 2026-11-17): the query parameter `searchQueryState` holds compact JSON `{"pagination": {} | {"currentPage": n}, "isMapVisible": false, "isListVisible": true, "regionSelection": [{"regionId": area.zillow_region_id, "regionType": area.zillow_region_type}], "filterState": {...}}`. `filterState` always holds Task 1's `BASE`, plus: `"monthlyPayment": {"min"?, "max"?}` (only the bounds given), `"beds": {"min": n}`, `"baths": {"min": n}`; `Pets.DOGS` → `"onlyRentalSmallDogsAllowed": {"value": true}` (the broadest dog filter); `Pets.CATS` → `"onlyRentalCatsAllowed": {"value": true}`; and home types exclude the others with `{"value": false}`: HOUSE → isApartment, isCondo, isTownhouse, isMultiFamily, isManufactured, isLotLand; APARTMENT_CONDO → isSingleFamily, isTownhouse, isMultiFamily, isManufactured, isLotLand; TOWNHOME → isSingleFamily, isApartment, isCondo, isMultiFamily, isManufactured, isLotLand.
- Pinned parsing: JSON in `<script id="__NEXT_DATA__">` at `props.pageProps.searchPageState.cat1`: `searchResults.listResults` (cards), `searchList.totalResultCount` (total), `searchList.pagination.nextUrl` present → has_more. Single unit: id `str(zpid)`, url `detailUrl`, name `""`, address, price `unformattedPrice` (min = max), beds, baths, image `imgSrc`, home type `hdpData.homeInfo.homeType`. Building (`isBuilding`): id `str(zpid)`, name `buildingName`, url `"https://www.zillow.com" + detailUrl` when relative, prices from `units[].price` ("$2,263+" → 2263; min and max over the units), beds from `units[].beds` (strings; "0" is a studio), baths None, home type `"BUILDING"`.
- Pinned errors: HTTP 403/429, or a body containing `px-captcha` or `Press & Hold` → `BlockedError("Zillow is blocking requests from this computer right now (bot protection). This usually clears up on its own.")`; any other non-200 → `ScrapeError(f"Zillow answered with an error (HTTP {status}).")`; no `__NEXT_DATA__` or no `cat1` → `ScrapeError("Zillow's page layout changed, so listings couldn't be read. The bot needs an update.")`.
- `matches_home_type`: ANY → all; APARTMENT_CONDO → APARTMENT, CONDO, BUILDING; HOUSE → SINGLE_FAMILY; TOWNHOME → TOWNHOUSE. `fetch` drops cards that don't match, as a safety net (Zillow shows buildings even in a houses-only search).

- [x] **Step 1: Save a real page as a fixture** (done 2026-11-17 from a home connection: 4 buildings and 1 single unit, total 15,830, `nextUrl` present). To refresh it, re-run this from a computer that reaches Zillow; pytest itself stays offline. It keeps only the parts the parser reads, with 5 real cards:

```bash
mkdir -p tests/fixtures/zillow && uv run python - <<'EOF'
import json, re
from curl_cffi import requests
html = requests.get("https://www.zillow.com/chicago-il/rentals/", impersonate="chrome", timeout=25).text
data = json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', html, re.S).group(1))
cat1 = data["props"]["pageProps"]["searchPageState"]["cat1"]
keep = {"props": {"pageProps": {"searchPageState": {"cat1": {
    "searchResults": {"listResults": cat1["searchResults"]["listResults"][:5]},
    "searchList": {"totalResultCount": cat1["searchList"]["totalResultCount"],
                   "pagination": cat1["searchList"]["pagination"]}}}}}}
with open("tests/fixtures/zillow/chicago_rentals.html", "w", encoding="utf-8") as f:
    f.write('<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(keep) + "</script></html>")
EOF
```

- [ ] **Step 1b: Write the failing tests** (`tests/test_zillow.py`; helpers `query_state(url) = json.loads(parse_qs(urlsplit(url).query)["searchQueryState"][0])` and `zillow_page(cards, total, next_url=None)`, which wraps `{"props": {"pageProps": {"searchPageState": {"cat1": {"searchResults": {"listResults": cards}, "searchList": {"totalResultCount": total, "pagination": {"nextUrl": next_url} if next_url else {}}}}}}}` in a `<script id="__NEXT_DATA__" type="application/json">` tag)

```python
SINGLE = {"zpid": "456940401", "id": "456940401",
          "detailUrl": "https://www.zillow.com/apartments/chicago-il/inside-info-fremont/CrvVmL/",
          "statusText": "Apartment for rent", "price": "$2,550/mo", "unformattedPrice": 2550,
          "address": "Inside Info Fremont, 1529 N Fremont St #217, Chicago, IL 60642",
          "beds": 1, "baths": 1, "imgSrc": "https://photos.zillowstatic.com/fp/d17f.jpg",
          "hdpData": {"homeInfo": {"homeType": "APARTMENT"}}}
BUILDING = {"zpid": "41.88502--87.6548", "id": "41.88502--87.6548",
            "detailUrl": "/apartments/chicago-il/one-six-six/Chg442/", "statusText": "One Six Six",
            "address": "166 N Aberdeen St, Chicago, IL", "buildingName": "One Six Six", "isBuilding": True,
            "units": [{"price": "$2,263+", "beds": "0"}, {"price": "$2,688+", "beds": "1"},
                      {"price": "$4,194+", "beds": "2"}],
            "imgSrc": "https://photos.zillowstatic.com/fp/0457.jpg"}
BASE_KEYS = {"sortSelection", "isForRent", "isForSaleByAgent", "isForSaleByOwner",
             "isNewConstruction", "isComingSoon", "isAuction", "isForSaleForeclosure"}

def test_search_url_carries_region_filters_paging_and_newest_first():
    c = Criteria(LINCOLN_PARK, min_price=1500, max_price=2400, min_beds=2, min_baths=1, pets=Pets.DOGS)
    url = search_url(c, page=2)
    assert url.startswith("https://www.zillow.com/homes/for_rent/?searchQueryState=")
    state = query_state(url)
    assert state["regionSelection"] == [{"regionId": 269590, "regionType": 8}]
    assert state["pagination"] == {"currentPage": 2}
    f = state["filterState"]
    assert f["sortSelection"] == {"value": "days"} and f["isForRent"] == {"value": True}
    assert f["monthlyPayment"] == {"min": 1500, "max": 2400}
    assert f["beds"] == {"min": 2} and f["baths"] == {"min": 1}
    assert f["onlyRentalSmallDogsAllowed"] == {"value": True}

def test_page_one_has_empty_paging_and_only_the_base_filters():
    state = query_state(search_url(Criteria(CHICAGO, max_price=2400)))
    assert state["pagination"] == {}
    assert set(state["filterState"]) == BASE_KEYS | {"monthlyPayment"}
    assert state["filterState"]["monthlyPayment"] == {"max": 2400}

@pytest.mark.parametrize("home_type,excluded", [
    (HomeType.HOUSE, {"isApartment", "isCondo", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand"}),
    (HomeType.APARTMENT_CONDO, {"isSingleFamily", "isTownhouse", "isMultiFamily", "isManufactured", "isLotLand"}),
    (HomeType.TOWNHOME, {"isSingleFamily", "isApartment", "isCondo", "isMultiFamily", "isManufactured", "isLotLand"}),
])
def test_home_type_excludes_the_other_types(home_type, excluded):
    f = query_state(search_url(Criteria(CHICAGO, home_type=home_type)))["filterState"]
    assert set(f) - BASE_KEYS == excluded
    assert all(f[k] == {"value": False} for k in excluded)

def test_cats_filter():
    f = query_state(search_url(Criteria(CHICAGO, pets=Pets.CATS)))["filterState"]
    assert f["onlyRentalCatsAllowed"] == {"value": True}

def test_parses_a_single_unit():
    cards, total, has_more = parse_search_page(zillow_page([SINGLE], 205, "/x/2_p/"), AT)
    assert (total, has_more, cards[0].home_type) == (205, True, "APARTMENT")
    assert cards[0].listing == Listing(
        source="zillow", listing_id="456940401",
        url="https://www.zillow.com/apartments/chicago-il/inside-info-fremont/CrvVmL/", name="",
        address="Inside Info Fremont, 1529 N Fremont St #217, Chicago, IL 60642",
        price_min=2550, price_max=2550, beds_min=1, beds_max=1, baths_min=1, baths_max=1,
        image_url="https://photos.zillowstatic.com/fp/d17f.jpg", scraped_at=AT)

def test_parses_a_building_as_ranges_with_an_absolute_url():
    card = parse_search_page(zillow_page([BUILDING], 1), AT)[0][0]
    l = card.listing
    assert (card.home_type, l.listing_id, l.name) == ("BUILDING", "41.88502--87.6548", "One Six Six")
    assert l.url == "https://www.zillow.com/apartments/chicago-il/one-six-six/Chg442/"
    assert (l.price_min, l.price_max, l.beds_min, l.beds_max, l.baths_min) == (2263, 4194, 0, 2, None)

def test_missing_price_is_none():
    card = {**SINGLE, "price": "Contact for price", "unformattedPrice": None}
    l = parse_search_page(zillow_page([card], 1), AT)[0][0].listing
    assert (l.price_min, l.price_max) == (None, None)

def test_zero_matches_is_an_empty_page_not_an_error():
    assert parse_search_page(zillow_page([], 0), AT) == ([], 0, False)

def test_challenge_page_is_blocked():
    with pytest.raises(BlockedError, match="Zillow is blocking"):
        parse_search_page('<html><div id="px-captcha"></div></html>', AT)

def test_page_without_listing_data_is_a_layout_change():
    with pytest.raises(ScrapeError, match="layout changed"):
        parse_search_page("<html><body>hello</body></html>", AT)

def test_fetch_drops_other_home_types_and_reports_the_page():
    house = {**SINGLE, "zpid": "9", "hdpData": {"homeInfo": {"homeType": "SINGLE_FAMILY"}}}
    http = FakeHttp(HttpResponse(200, zillow_page([BUILDING, house], 2)))
    page = ZillowSource(http, now=lambda: AT).fetch(Criteria(CHICAGO, home_type=HomeType.HOUSE), 1)
    assert [l.listing_id for l in page.listings] == ["9"]
    assert (page.total, page.has_more) == (2, False)
    assert http.requested == [search_url(Criteria(CHICAGO, home_type=HomeType.HOUSE), 1)]

@pytest.mark.parametrize("status", [403, 429])
def test_refused_status_is_blocked(status):
    with pytest.raises(BlockedError):
        ZillowSource(FakeHttp(HttpResponse(status, "")), now=lambda: AT).fetch(Criteria(CHICAGO), 1)

def test_parses_a_saved_real_page(fixture_html):
    cards, total, has_more = parse_search_page(fixture_html("zillow/chicago_rentals.html"), AT)
    assert len(cards) == 5 and total > 0 and has_more
    assert all(c.listing.listing_id and c.listing.url.startswith("https://www.zillow.com/") and c.listing.address
               for c in cards)
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_zillow.py -q` → FAIL
- [ ] **Step 3: Implement `sources/zillow.py`** to the pinned format. Find the script with Scrapling's `Selector(html).css("script#__NEXT_DATA__")`; check for block markers before parsing.
- [ ] **Step 4: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/apartment_hunter/sources/zillow.py tests/test_zillow.py tests/fixtures/zillow
git commit -m "Add the Zillow Source: criteria to search URL, newest-first page parsing"
```

---

### Task 7: Area lookup (Zillow places, nearest Craigslist site)

**Files:**
- Create: `src/apartment_hunter/areas.py`, `src/apartment_hunter/sources/craigslist_sites.json`
- Test: `tests/test_areas.py`

**Interfaces:**
- Consumes: `Area` (Task 3), `HttpClient`, `ScrapeError` (Task 4)
- Produces: `ACCEPTED_KINDS = {"city": 6, "zipcode": 7, "neighborhood": 8}` (Zillow region types; 6 and 8 verified, 7 by Zillow's numbering); `@dataclass(frozen=True) class Place: display: str; kind: str; region_id: int; lat: float; lon: float`; `@dataclass(frozen=True) class CraigslistSite: hostname: str; lat: float; lon: float`; `class AreaNotFound(Exception)`; `suggestions_url(text: str) -> str`; `parse_suggestions(body: str) -> list[Place]`; `load_craigslist_sites() -> list[CraigslistSite]`; `nearest_craigslist_site(lat: float, lon: float, sites: Sequence[CraigslistSite]) -> str`; `class Areas(Protocol): def suggest(self, text: str) -> list[str]; def resolve(self, text: str) -> Area`; `class AreaLookup(http: HttpClient, sites: Sequence[CraigslistSite] | None = None, clock: Callable[[], float] = time.monotonic, min_gap_seconds: float = 2.0)` implementing `Areas`
- Pinned: `suggestions_url(t) = "https://www.zillowstatic.com/autocomplete/v3/suggestions?q=" + urllib.parse.quote(t, safe="")`. The response is `{"results": [{"display", "resultType", "metaData": {"regionId", "regionType", "lat", "lng"}}]}`; only `resultType == "Region"` with `regionType` in `ACCEPTED_KINDS` becomes a `Place`. Radius: 10 miles for a city, 2 for a neighborhood or ZIP code. Nearest site by great-circle distance. `suggest`: strip; fewer than 3 characters → `[]`; cached text → cached displays; fewer than `min_gap_seconds` since the last lookup request → `[]`; otherwise request, cache, and return the displays. `resolve`: cached or fresh lookup (never throttled); the place whose display equals the text case-insensitively, else the first; none → `AreaNotFound(f"I couldn't find a place called \"{text}\". Try a city, neighborhood or ZIP code, like \"Chicago, IL\" or \"60614\".")`.

- [x] **Step 1: Generate the bundled site list** (done 2026-11-17: 413 US sites, `chicago` as expected)

Run: `curl -s https://reference.craigslist.org/Areas | uv run python -c "import json,sys; a=json.load(sys.stdin); json.dump(sorted(({'hostname': x['Hostname'], 'lat': x['Latitude'], 'lon': x['Longitude']} for x in a if x['Country'] == 'US'), key=lambda s: s['hostname']), sys.stdout, indent=0)" > src/apartment_hunter/sources/craigslist_sites.json`
Expected: a JSON list of about 400 US sites, including `{"hostname": "chicago", "lat": 41.849998, "lon": -87.650002}`.

- [ ] **Step 2: Write the failing tests** (`tests/test_areas.py`; `FakeHttp(body)` returns `HttpResponse(200, body)` and records URLs; `FakeClock` in seconds)

```python
SUGGEST_BODY = json.dumps({"results": [
    {"display": "Lincoln Park, Chicago, IL", "resultType": "Region",
     "metaData": {"regionId": 269590, "regionType": "neighborhood", "lat": 41.9237, "lng": -87.6505}},
    {"display": "1906 N Lincoln Park W Chicago, IL 60614", "resultType": "Address",
     "metaData": {"addressType": "rental_address"}},
    {"display": "Illinois", "resultType": "Region",
     "metaData": {"regionId": 21, "regionType": "state", "lat": 40.0, "lng": -89.0}},
    {"display": "Chicago, IL", "resultType": "Region",
     "metaData": {"regionId": 17426, "regionType": "city", "lat": 41.8781, "lng": -87.6298}}]})
SITES = [CraigslistSite("chicago", 41.849998, -87.650002), CraigslistSite("newyork", 40.7166, -74.0),
         CraigslistSite("milwaukee", 43.0389, -87.9065)]

def test_only_cities_neighborhoods_and_zip_codes_are_offered():
    assert [p.display for p in parse_suggestions(SUGGEST_BODY)] == ["Lincoln Park, Chicago, IL", "Chicago, IL"]

def test_suggestions_url_encodes_the_text():
    assert suggestions_url("Coeur d'Alene, ID") == \
        "https://www.zillowstatic.com/autocomplete/v3/suggestions?q=Coeur%20d%27Alene%2C%20ID"

def test_nearest_craigslist_site():
    assert nearest_craigslist_site(41.9237, -87.6505, SITES) == "chicago"
    assert nearest_craigslist_site(43.05, -87.95, SITES) == "milwaukee"

def test_bundled_sites_include_chicago():
    assert nearest_craigslist_site(41.8781, -87.6298, load_craigslist_sites()) == "chicago"

def test_resolve_picks_the_matching_place_and_builds_the_area():
    lookup = AreaLookup(FakeHttp(SUGGEST_BODY), SITES, clock=FakeClock())
    assert lookup.resolve("chicago, il") == Area("Chicago, IL", "city", 17426, 6, 41.8781, -87.6298, "chicago", 10)
    lp = lookup.resolve("Lincoln Park, Chicago, IL")
    assert (lp.zillow_region_type, lp.craigslist_radius_miles) == (8, 2)

def test_resolve_with_no_usable_place_raises():
    lookup = AreaLookup(FakeHttp(json.dumps({"results": []})), SITES, clock=FakeClock())
    with pytest.raises(AreaNotFound, match="couldn't find a place called"):
        lookup.resolve("123 Nowhere Rd")

def test_suggest_needs_three_characters_caches_and_throttles():
    http, clock = FakeHttp(SUGGEST_BODY), FakeClock()
    lookup = AreaLookup(http, SITES, clock=clock, min_gap_seconds=2)
    assert lookup.suggest("ch") == []
    assert lookup.suggest("Chi") == ["Lincoln Park, Chicago, IL", "Chicago, IL"]
    assert lookup.suggest("Chic") == []
    assert lookup.suggest("Chi") == ["Lincoln Park, Chicago, IL", "Chicago, IL"]
    clock.advance(2)
    lookup.suggest("Chic")
    assert len(http.requested) == 2
```

- [ ] **Step 3: Run to verify they fail** — `uv run pytest tests/test_areas.py -q` → FAIL
- [ ] **Step 4: Implement `areas.py`**; load the JSON with `importlib.resources.files("apartment_hunter.sources") / "craigslist_sites.json"`.
- [ ] **Step 5: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy` → PASS
- [ ] **Step 6: Commit**

```bash
git add src/apartment_hunter/areas.py src/apartment_hunter/sources/craigslist_sites.json tests/test_areas.py
git commit -m "Resolve areas through Zillow's place lookup and the nearest Craigslist site"
```

---

### Task 8: SearchRunner: per-Source seeding, burst cap, health

**Files:**
- Create (rewrite): `src/apartment_hunter/runner.py`, `src/apartment_hunter/text.py`
- Modify: `src/apartment_hunter/notifiers/__init__.py`, `src/apartment_hunter/notifiers/discord.py`
- Test: `tests/test_runner.py`, `tests/test_discord.py`

**Interfaces:**
- Consumes: `Store` (Task 5), `Source`, `SourcePage`, `ScrapeError`, `SOURCE_NAMES` (Task 4), `Search`, `Listing` (Task 3)
- Produces: `text.places_match(n: int) -> str` ("1 place matches" / "214 places match"); `notifiers.ChannelGone(NotifyError)`; `@dataclass(frozen=True) class SourceResult: source: str; ok: bool; seeded: bool; found: int; total: int | None; more_pages: bool; error: str | None`; `@dataclass(frozen=True) class RunOutcome: search_id: str; results: tuple[SourceResult, ...]; alerts_sent: int; channel_gone: bool = False`; `class SearchRunner(sources: Mapping[str, Source], store: Store, notifier_for: Callable[[Search], Notifier], *, failure_alert_threshold: int = 3, burst_cap: int = 10, seeding_pages: int = 3)` with `run(search: Search) -> RunOutcome`. `Notifier` is unchanged: `send_listing(listing, search_name, previous_price=None)`, `send_text(message)`.
- Pinned behaviour per Source in `search.sources` order. Not yet seeded → read pages 1…3 while `has_more`, save every Listing silently, `mark_seeded`. Seeded → read page 1; an unseen Listing is a new-listing candidate; a seen Listing with both prices known and lower now is a price-drop candidate; save unchanged ones. `ScrapeError` → `record_failure`; success → `record_success`. `found` counts Listings read; `total` and `more_pages` come from page 1. After all Sources: send candidates in order up to the burst cap, saving each once it's delivered (a `NotifyError` leaves it unsaved for the next Run). Then send one summary per Source for the rest, and save those once the summary is delivered. Then health texts. A `ChannelGone` from any send stops the Run at once (`channel_gone=True`, nothing more saved). `record_run` always.
- Pinned copy: broken (when the streak reaches the threshold and no broken alert was sent) `"⚠️ {Source} hasn't worked for **{name}** in {n} checks in a row: {reason}"`, plus `" {Other} is still being checked."` when another of the Search's Sources succeeded in this Run; recovered (success after a broken alert) `"✅ {Source} is working again for **{name}**."`; working now (a successful seeding run after one or more failures, with no broken alert sent) `"✅ {Source} is working for **{name}** now: {places_match(n)}. I'll post new ones here."` with n = total, or found when total is None; summary `f"…and {k} more new {'place' if k == 1 else 'places'} on {Source}: {results_url}"`.
- `build_embed(listing, search_name, previous_price=None)`: description line `"New listing on {Source}"` or `"📉 Price drop on {Source}: ${old} → ${new}"` (after the address line when the listing has a name, as today); footer `{"text": search_name}`; `"timestamp": listing.scraped_at.isoformat()`. Ranges, studio and "Call for price" stay as today.

- [ ] **Step 1: Write the failing tests**

`tests/test_runner.py`, with two fakes added to `tests/fakes.py`: `FakeSource(key)` (`.pages` holds a list of items consumed one per `fetch`: a `SourcePage`, a `list[Listing]` meaning `SourcePage(listings, len(listings), False)`, or an exception to raise; `.requested` records page numbers; `results_url` returns `f"https://{key}.example/results"`), and `FakeNotifier` (`.listings` holds `(listing_id, previous_price)`, `.texts`; `.fail` raises `NotifyError`, `.gone` raises `ChannelGone`). The `env` fixture wires `Store(":memory:")`, `zillow`, `craigslist`, one notifier and a runner, and adds `SEARCH = make_search(name="Chicago")` and `BOTH = make_search("s2", name="Both", sources=("zillow", "craigslist"))` to the store.

```python
def test_seeding_run_records_silently_and_reports_the_total(env):
    env.zillow.pages = [SourcePage([make_listing("a"), make_listing("b")], 214, False)]
    outcome = env.runner.run(SEARCH)
    assert outcome.results == (SourceResult("zillow", True, True, 2, 214, False, None),)
    assert env.notifier.listings == [] and env.notifier.texts == []
    assert env.store.listing_count(SEARCH.id) == 2

def test_seeding_reads_up_to_three_pages_then_runs_read_page_one(env):
    more = SourcePage([make_listing("a")], 500, True)
    env.zillow.pages = [more, more, more, more]
    env.runner.run(SEARCH)
    env.runner.run(SEARCH)
    assert env.zillow.requested == [1, 2, 3, 1]

def test_new_listing_alerts_once(env):
    env.zillow.pages = [[make_listing("a")], [make_listing("a"), make_listing("b")],
                        [make_listing("a"), make_listing("b")]]
    for _ in range(3):
        env.runner.run(SEARCH)
    assert env.notifier.listings == [("b", None)]

def test_price_drop_alerts_but_increase_does_not(env):
    env.zillow.pages = [[make_listing("a", 2000)], [make_listing("a", 2100)],
                        [make_listing("a", 1900)], [make_listing("a", 1900)]]
    for _ in range(4):
        env.runner.run(SEARCH)
    assert env.notifier.listings == [("a", 2100)]

def test_unknown_price_is_never_a_price_drop(env):
    env.zillow.pages = [[make_listing("a", 2000)], [make_listing("a", None)], [make_listing("a", 2000)]]
    for _ in range(3):
        env.runner.run(SEARCH)
    assert env.notifier.listings == []

def test_failed_delivery_is_retried_next_run(env):
    env.zillow.pages = [[], [make_listing("a")], [make_listing("a")]]
    env.runner.run(SEARCH)
    env.notifier.fail = True
    env.runner.run(SEARCH)
    assert env.store.get_listing(SEARCH.id, "zillow", "a") is None
    env.notifier.fail = False
    env.runner.run(SEARCH)
    assert env.notifier.listings == [("a", None)]

def test_burst_cap_sends_ten_and_summarises_the_rest(env):
    fourteen = [make_listing(str(i)) for i in range(14)]
    env.zillow.pages = [[], fourteen, fourteen]
    for _ in range(3):
        env.runner.run(SEARCH)
    assert [i for i, _ in env.notifier.listings] == [str(i) for i in range(10)]
    assert env.notifier.texts == ["…and 4 more new places on Zillow: https://zillow.example/results"]
    assert env.store.listing_count(SEARCH.id) == 14

def test_channel_gone_stops_the_run_without_marking_seen(env):
    env.zillow.pages = [[], [make_listing("a"), make_listing("b")]]
    env.runner.run(SEARCH)
    env.notifier.gone = True
    outcome = env.runner.run(SEARCH)
    assert outcome.channel_gone and outcome.alerts_sent == 0
    assert env.store.get_listing(SEARCH.id, "zillow", "a") is None
    assert env.store.get_listing(SEARCH.id, "zillow", "b") is None

def test_health_alert_per_source_names_the_source_still_working(env):
    env.zillow.pages = [[]] + [ScrapeError("blocked.")] * 4 + [[]]
    env.craigslist.pages = [[]] * 6
    for _ in range(4):
        env.runner.run(BOTH)
    assert env.notifier.texts == [
        "⚠️ Zillow hasn't worked for **Both** in 3 checks in a row: blocked. Craigslist is still being checked."]
    env.runner.run(BOTH)
    env.runner.run(BOTH)
    assert env.notifier.texts[-1] == "✅ Zillow is working again for **Both**."

def test_failed_seeding_then_success_says_it_is_working_now(env):
    env.zillow.pages = [ScrapeError("blocked"), SourcePage([make_listing("a")], 1, False)]
    first = env.runner.run(SEARCH)
    assert first.results[0].ok is False and first.results[0].error == "blocked"
    env.runner.run(SEARCH)
    assert env.notifier.texts == ["✅ Zillow is working for **Chicago** now: 1 place matches. I'll post new ones here."]

def test_zero_matches_is_healthy(env):
    env.zillow.pages = [SourcePage([], 0, False), SourcePage([], 0, False)]
    env.runner.run(SEARCH)
    outcome = env.runner.run(SEARCH)
    assert outcome.results[0].ok
    assert env.store.source_health(SEARCH.id, "zillow") == SourceHealth(True, 0, False, None)

def test_a_failing_source_does_not_stop_the_other_from_seeding(env):
    env.zillow.pages = [ScrapeError("blocked")]
    env.craigslist.pages = [[make_listing("c", source="craigslist")]]
    env.runner.run(BOTH)
    assert env.store.source_health(BOTH.id, "craigslist").seeded
    assert not env.store.source_health(BOTH.id, "zillow").seeded
```

(Real Source errors end with a period, as the fake's `"blocked."` does; the runner adds no punctuation of its own.)

`tests/test_discord.py`, added to the remaining embed tests (rename "Downtown" to "Chicago"):

```python
def test_embed_names_the_source_the_search_and_the_time():
    e = build_embed(make_listing(name=""), "Chicago")
    assert e["description"] == "New listing on Zillow"
    assert e["footer"] == {"text": "Chicago"}
    assert e["timestamp"] == "2026-11-05T12:00:00+00:00"

def test_embed_price_drop():
    e = build_embed(make_listing(price=1900, source="craigslist"), "Chicago", previous_price=2100)
    assert e["description"].endswith("📉 Price drop on Craigslist: $2,100 → $1,900")
```

(With a name, the description starts with the address line, as today; the first test passes `name=""` to see the line alone.)

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_runner.py tests/test_discord.py -q` → FAIL
- [ ] **Step 3: Implement** `text.py`, `ChannelGone`, the `build_embed` changes and `runner.py` to the pinned behaviour.
- [ ] **Step 4: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/apartment_hunter/runner.py src/apartment_hunter/text.py src/apartment_hunter/notifiers tests/test_runner.py tests/test_discord.py tests/fakes.py
git commit -m "SearchRunner seeds and tracks health per Source, caps alert bursts"
```

---

### Task 9: Scheduler keyed by Search id

**Files:**
- Modify: `src/apartment_hunter/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Produces: `Scheduler(run: Callable[[str], None], interval_minutes: float = 15, clock: Callable[[], float] = time.monotonic, next_delay: Callable[[float], float] = next_delay_seconds)` with new `add(search_id: str, first_delay: float = 0.0, paused: bool = False) -> None` and `remove(search_id: str) -> None` (also drops it from the run-now queue). Everything else keeps today's behaviour, keyed by id: `start`, `stop`, `running`, `pause`, `resume`, `is_paused`, `current`, `next_run`, `run_now` (KeyError for an unknown id), `run_pending`, `serve_forever`, `start_in_background`, `shutdown`. The constructor no longer takes searches, and the `Schedulable` protocol from Task 2 is removed.

- [ ] **Step 1: Update the tests.** In `test_scheduler.py`, build the env with `Scheduler(ran.append, interval_minutes=15, clock=clock, next_delay=lambda m: m * 60)` and then `add("A")`, `add("B")`. Give B its own 30-minute rhythm by constructing a second scheduler where a test needs it, or drop the 30-minute expectations: one global interval now. Then add:

```python
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
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_scheduler.py -q` → FAIL
- [ ] **Step 3: Implement.** `add` sets `next_run[id] = clock() + first_delay`; `run_pending` passes the id to `run` and reschedules with `next_delay(interval_minutes)`; an id removed mid-run isn't rescheduled.
- [ ] **Step 4: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/apartment_hunter/scheduler.py tests/test_scheduler.py
git commit -m "Scheduler adds and removes Searches by id while running"
```

---

### Task 10: SearchService core (create, statuses, load, run)

**Files:**
- Create: `src/apartment_hunter/service.py`
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `Store`, `DuplicateName` (Task 5); `SearchRunner`, `RunOutcome` (Task 8); `Scheduler` (Task 9); `Areas`, `AreaNotFound` (Task 7); `ScrapeError`, `SOURCE_NAMES` (Task 4); `Criteria`, `HomeType`, `Pets`, `summarize`, `Search` (Task 3)
- Produces: `@dataclass(frozen=True) class Limits: per_server: int = 5; total: int = 25; check_gap_minutes: float = 5.0`; `class SearchError(Exception)` (`str(e)` is shown to the user); `@dataclass(frozen=True) class NewSearch: area: str; min_price: int | None = None; max_price: int | None = None; min_beds: float | None = None; min_baths: float | None = None; home_type: HomeType = HomeType.ANY; pets: Pets = Pets.NONE; sources: tuple[str, ...] | None = None; name: str | None = None`; `@dataclass(frozen=True) class SearchStatus: search: Search; state: str; reason: str | None; last_run: datetime | None; listings: int`; `class Pending` with `wait(timeout: float | None = None) -> RunOutcome | None` (the outcome of the first Run of that Search to finish after the Pending was created; None on timeout); `class SearchService(*, store, runner, scheduler, areas: Areas, source_keys: tuple[str, ...], limits: Limits = Limits(), interval_minutes: float = 15, clock: Callable[[], datetime] = lambda: datetime.now(UTC), new_id: Callable[[], str] = lambda: uuid4().hex[:12], rand: Callable[[float, float], float] = random.uniform, direct_message: Callable[[int, str], None] = lambda user_id, text: None, owner_id: int | None = None)` with `load() -> None`, `run(search_id: str) -> None` (the Scheduler's callback), `create(server_id: int, channel_id: int, user_id: int, new: NewSearch, *, can_post: bool) -> tuple[Search, Pending]`, `statuses(server_id: int) -> list[SearchStatus]`, `names(server_id: int, current: str) -> list[str]` (case-insensitive substring match, at most 25), `suggest_areas(text: str) -> list[str]`
- Pinned `create` order and copy:
  1. `not can_post` → `"I can't post in this channel. Give me the View Channel, Send Messages and Embed Links permissions here, or run the command in another channel."`
  2. The server is at `per_server` → `f"This server already has {n} searches, the most allowed. Delete one with /search delete first."`; the bot is at `total` → `"The bot is watching as many searches as it can right now. Try again later, or ask whoever runs it."`
  3. `areas.resolve`: `AreaNotFound` → its message; `ScrapeError` → `f"I couldn't look up places right now: {reason} Try again in a minute."`
  4. Name: `new.name.strip()` if given, else `summarize(criteria)`; `DuplicateName` → `f"This server already has a search called \"{name}\". Pick another name with name:."`
  5. Save, register a Pending, `scheduler.add(id, first_delay=interval_minutes * 60)`, `scheduler.run_now(id)`.
- Pinned `run`: load the Search (gone → return); `runner.run`; on `channel_gone`, save it with `paused=True, attention=CHANNEL_GONE`, `scheduler.pause`, and `direct_message(created_by, f"I paused your search \"{name}\": I can't post in its channel anymore (it was deleted or I lost permission). Fix the channel and use /search resume, or delete it with /search delete.")`. `CHANNEL_GONE = "I can't post in its channel anymore (it was deleted or I lost permission)."` Then resolve its Pendings.
- Pinned `statuses` state, first match wins: `scheduler.current == id` → "Running"; `attention` → "Needs attention" (reason = attention); `paused` → "Paused"; any Source of the Search with a failure streak → "Needs attention" (reason = `"{Source}: {last_error}"`, joined with `"; "`); no Source seeded and no last run → "Not run yet"; else "Healthy".
- `load`: `scheduler.add(id, first_delay=rand(0, interval_minutes * 60), paused=search.paused)` for every stored Search.

- [ ] **Step 1: Write the failing tests** (`tests/test_service.py`; fixture `env` builds `Store(":memory:")`, `zillow = FakeSource("zillow")` (from `tests/fakes.py`), `notifiers = defaultdict(FakeNotifier)` keyed by channel, a runner over `{"zillow": zillow}`, `FakeClock`, `Scheduler(lambda sid: service.run(sid), clock=clock, next_delay=lambda m: m * 60)` started, `FakeAreas` (resolves "Chicago, IL" to `CHICAGO`, anything else raises `AreaNotFound("I couldn't find a place called …")`), ids `"s1"`, `"s2"`, … from a counter, `dms` list as `direct_message`, and the service's `clock = lambda: AT + timedelta(seconds=clock())`, so it moves with the scheduler's `FakeClock`, whose `advance` takes minutes as in `test_scheduler.py`)

```python
def test_create_seeds_right_away_and_reports_the_count(env):
    env.zillow.pages = [SourcePage([make_listing("a"), make_listing("b")], 214, False)]
    search, pending = env.service.create(1, 10, 99, NewSearch("Chicago, IL", max_price=2400, min_beds=2,
                                                             pets=Pets.DOGS), can_post=True)
    env.scheduler.run_pending()
    outcome = pending.wait(0)
    assert search.name == "Chicago, IL · 2+ bd · up to $2,400 · dogs"
    assert outcome.results[0].total == 214 and outcome.results[0].seeded
    assert env.notifiers[10].texts == [] and env.notifiers[10].listings == []

def test_create_refuses_without_permission_and_saves_nothing(env):
    with pytest.raises(SearchError, match="can't post in this channel"):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=False)
    assert env.store.searches() == []

def test_create_refuses_an_unknown_area_and_saves_nothing(env):
    with pytest.raises(SearchError, match="couldn't find a place called"):
        env.service.create(1, 10, 99, NewSearch("Illinois"), can_post=True)
    assert env.store.searches() == []

def test_limits_per_server_and_in_total(env):
    env.service.limits = Limits(per_server=2, total=3)
    for name in ("a", "b"):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL", name=name), can_post=True)
    with pytest.raises(SearchError, match="already has 2 searches"):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="c"), can_post=True)
    env.service.create(2, 20, 99, NewSearch("Chicago, IL", name="c"), can_post=True)
    with pytest.raises(SearchError, match="as many searches as it can"):
        env.service.create(3, 30, 99, NewSearch("Chicago, IL", name="d"), can_post=True)

def test_names_are_unique_per_server_only(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    with pytest.raises(SearchError, match='already has a search called "Home"'):
        env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.service.create(2, 20, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)

def test_servers_only_see_their_own_searches(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Mine"), can_post=True)
    env.service.create(2, 20, 99, NewSearch("Chicago, IL", name="Theirs"), can_post=True)
    assert [s.search.name for s in env.service.statuses(2)] == ["Theirs"]
    assert env.service.names(2, "") == ["Theirs"]

def test_status_needs_attention_with_the_reason_after_one_failure(env):
    env.zillow.pages = [SourcePage([], 0, False), ScrapeError("Zillow is blocking requests.")]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL"), can_post=True)
    env.scheduler.run_pending()
    assert env.service.statuses(1)[0].state == "Healthy"
    env.clock.advance(15)
    env.scheduler.run_pending()
    status = env.service.statuses(1)[0]
    assert (status.state, status.reason) == ("Needs attention", "Zillow: Zillow is blocking requests.")

def test_loaded_searches_are_spread_across_the_first_interval(env):
    env.store.add_search(make_search("a", name="A"))
    env.store.add_search(make_search("b", name="B"))
    offsets = iter([100.0, 700.0])
    env.service.rand = lambda lo, hi: next(offsets)
    env.service.load()
    assert env.scheduler.next_run("a") == env.clock() + 100
    assert env.scheduler.next_run("b") == env.clock() + 700

def test_channel_gone_pauses_the_search_and_tells_its_creator(env):
    env.zillow.pages = [[], [make_listing("a")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.notifiers[10].gone = True
    env.clock.advance(15)
    env.scheduler.run_pending()
    status = env.service.statuses(1)[0]
    assert status.state == "Needs attention" and "can't post" in status.reason
    assert env.scheduler.is_paused(search.id)
    assert env.dms == [(99, 'I paused your search "Home": I can\'t post in its channel anymore (it was '
                            "deleted or I lost permission). Fix the channel and use /search resume, or "
                            "delete it with /search delete.")]
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_service.py -q` → FAIL
- [ ] **Step 3: Implement `service.py`** to the pinned behaviour. Pendings live in a dict guarded by a `threading.Lock`; `Pending` wraps a `threading.Event`.
- [ ] **Step 4: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/apartment_hunter/service.py tests/test_service.py
git commit -m "SearchService: create, seed on add, per-server statuses and isolation"
```

---

### Task 11: The bot: settings, replies, Discord bridge, `/search new | list | help`

**Files:**
- Create: `src/apartment_hunter/settings.py`, `src/apartment_hunter/bot/__init__.py`, `src/apartment_hunter/bot/replies.py`, `src/apartment_hunter/bot/posting.py`, `src/apartment_hunter/bot/app.py`, `src/apartment_hunter/bot/main.py`, `.env.example` (replace)
- Modify: `pyproject.toml`, `uv.lock`, `src/apartment_hunter/text.py`
- Test: `tests/test_settings.py`, `tests/test_replies.py`

**Interfaces:**
- Consumes: `SearchService`, `NewSearch`, `SearchError`, `SearchStatus`, `Limits`, `Pending` (Task 10); `RunOutcome` (Task 8); `build_embed`, `ChannelGone`, `NotifyError` (Task 8); `ZillowSource` (Task 6); `AreaLookup` (Task 7); `CurlHttpClient`, `PoliteHttpClient` (Task 4); `Store` (Task 5); `Scheduler` (Task 9); `DataDir` (Task 2)
- Produces:
  - `settings.py`: `class SettingsError(Exception)`; `@dataclass(frozen=True) class BotSettings: token: str; data_dir: Path = Path("data"); owner_id: int | None = None; dev_guild_id: int | None = None; interval_minutes: float = 15; per_server: int = 5; total: int = 25; allowed_servers: frozenset[int] = frozenset(); log_level: str = "INFO"`; `load_settings(env: Mapping[str, str]) -> BotSettings` reading `DISCORD_TOKEN`, `DATA_DIR`, `OWNER_USER_ID`, `DEV_GUILD_ID`, `INTERVAL_MINUTES` (below 10 is raised to 10 with a warning), `MAX_SEARCHES_PER_SERVER`, `MAX_SEARCHES_TOTAL`, `ALLOWED_SERVERS` (comma-separated ids), `LOG_LEVEL`. A missing token or a non-numeric value raises `SettingsError` naming the variable.
  - `text.py` adds `join_and(items: Sequence[str]) -> str` ("A", "A and B", "A, B and C") and `time_ago(seconds: float) -> str` ("just now" under 1 min, "N min ago", "N h ago", "N days ago"; v0.2's web UI rounding).
  - `bot/replies.py` (pure, no discord import): `checking_reply(search: Search) -> str`, `created_reply(search: Search, outcome: RunOutcome | None, interval_minutes: float) -> str`, `statuses_reply(statuses: list[SearchStatus], now: datetime) -> str`, `help_reply(limits: Limits, interval_minutes: float) -> str`, `invite_url(application_id: int) -> str`, `refusal(e: SearchError) -> str` (= `str(e)`)
  - `bot/posting.py`: `class DiscordBridge(client: discord.Client, timeout: float = 30.0)` with `post(channel_id: int, *, content: str | None = None, embed: dict | None = None) -> None` (runs on `client.loop` via `asyncio.run_coroutine_threadsafe(...).result(timeout)`; `discord.NotFound`/`discord.Forbidden` → `ChannelGone`; other `discord.HTTPException` or a timeout → `NotifyError`) and `direct_message(user_id: int, text: str) -> None` (logs and swallows failures); `class ChannelNotifier(bridge: DiscordBridge, channel_id: int)` implementing `Notifier` with `build_embed`.
  - `bot/app.py`: `build_client(service: SearchService, settings: BotSettings, on_first_ready: Callable[[], None]) -> discord.Client`.
  - `bot/main.py`: `main(argv: list[str] | None = None) -> int`; script `apartment-hunter = "apartment_hunter.bot.main:main"`.
- Pinned copy: `checking_reply` → `f"Checking {join_and(source names)} for \"{name}\"…"`. `created_reply`, with N = the sum over successful Sources of total (or found when total is None):
  - all succeeded → `f"✅ Watching \"{name}\". {places_match(N)} right now. I'll post new ones and price drops in this channel."`, plus `" That's more than one page of results, so price drops are only caught among the newest ones. Narrow it with /search edit to catch them all."` when any result has `more_pages`;
  - some failed → `f"✅ Watching \"{name}\". {places_match(N)} on {join_and(ok names)} right now. {join_and(failed names)} didn't work yet ({'; '.join(errors)}); I'll keep trying."`;
  - all failed → `f"⚠️ I saved \"{name}\", but couldn't check it yet: {'; '.join(errors)} I'll keep trying every {interval:g} minutes and post here once it works."`;
  - None → `f"⏳ Saved \"{name}\". The first check is taking a while; I'll post here once it's done."`.

  `statuses_reply`: one line per Search, `f"**{name}** · {state}{f' ({reason})' if reason else ''} · last checked {time_ago or 'never'} · {listings} seen · <#{channel_id}>"`; no Searches → `"No searches in this server yet. Start one with /search new."`. `invite_url(id)` → `f"https://discord.com/oauth2/authorize?client_id={id}&scope=bot+applications.commands&permissions=19456"`.
- Pinned wiring (`main`): `load_dotenv()`; settings (`SettingsError` → log, return 2); logging at `log_level`; `DataDir(settings.data_dir)`; hold `lock()` (`AlreadyRunning` → log, return 2) and attach `log_handler()`. `Store(data.database)`. Runs use `PoliteHttpClient(CurlHttpClient())`; area lookups use their own `CurlHttpClient(timeout=2.5)` (never the polite one: autocomplete must answer within 3 s). Sources `{"zillow": ZillowSource(run_http)}`. Create in this order: runner (`notifier_for = lambda s: ChannelNotifier(bridge, s.channel_id)`), scheduler (`run = lambda sid: service.run(sid)`, `interval_minutes`), service (`direct_message = lambda uid, text: bridge.direct_message(uid, text)`, `owner_id`), `client = build_client(...)` with `on_first_ready` = `service.load(); scheduler.start_in_background()`, then `bridge = DiscordBridge(client)`. The lambdas look `bridge` and `service` up when called, after all of them exist. `client.run(token, log_handler=None)`; finally `scheduler.shutdown(timeout=30)` and `store.close()`. `--help` prints usage and exits 0 (CI uses it).
- Pinned commands: group `search` ("Find new rentals and get them posted here"). `new`: `area` (str, required, autocomplete → `service.suggest_areas` via `asyncio.to_thread`), `max_price` / `min_price` (`app_commands.Range[int, 1, 100000]`), `min_beds` (`Range[int, 0, 10]`, "0 = studio"), `min_baths` (`Range[float, 0, 10]`), `home_type` choices (Any / Apartment or condo / House / Townhome → the `HomeType` values), `pets` choices (No requirement / Dogs / Cats), `name` (str, max 60). `list` and `help` take no options. Flow for `new`: compute `can_post` from `interaction.channel.permissions_for(guild.me)` (view_channel, send_messages, embed_links); `await interaction.response.defer(thinking=True)`; `service.create` via `to_thread`; on `SearchError`, delete the original response and send `str(e)` as an ephemeral follow-up; else edit to `checking_reply`, `await asyncio.to_thread(pending.wait, 840)`, and edit to `created_reply`. If that returned None, keep awaiting `pending.wait(None)` in a background task and post `created_reply` in the channel when it resolves. `list` and `help` reply ephemerally. Commands sync in `setup_hook`: with `dev_guild_id`, `tree.copy_global_to(guild=...)` and `sync(guild=...)`; else a global `sync()`. `on_ready` logs `invite_url(client.application_id)` and calls `on_first_ready` once. Intents: `discord.Intents.none()` with `guilds = True`.

- [ ] **Step 1: Add discord.py** — `uvx uv@0.12.19 add 'discord.py>=2.7.1'`; add the `[project.scripts]` entry.

- [ ] **Step 2: Write the failing tests**

`tests/test_settings.py`:

```python
def test_defaults():
    assert load_settings({"DISCORD_TOKEN": "t"}) == BotSettings(token="t")

def test_missing_token_is_a_plain_error():
    with pytest.raises(SettingsError, match="DISCORD_TOKEN"):
        load_settings({})

def test_interval_below_ten_minutes_is_raised_to_ten():
    assert load_settings({"DISCORD_TOKEN": "t", "INTERVAL_MINUTES": "5"}).interval_minutes == 10

def test_parses_ids_and_the_allowlist():
    s = load_settings({"DISCORD_TOKEN": "t", "OWNER_USER_ID": "123", "ALLOWED_SERVERS": "1, 2"})
    assert (s.owner_id, s.allowed_servers) == (123, frozenset({1, 2}))

def test_a_non_numeric_value_names_the_variable():
    with pytest.raises(SettingsError, match="MAX_SEARCHES_TOTAL"):
        load_settings({"DISCORD_TOKEN": "t", "MAX_SEARCHES_TOTAL": "lots"})
```

`tests/test_replies.py` (with `ok(source, total, more=False)` and `failed(source, error)` helpers building `SourceResult`s, and `SEARCH = make_search(name="Chicago 2BR", sources=("zillow", "craigslist"))`):

```python
def test_checking_reply_names_the_sources():
    assert checking_reply(SEARCH) == 'Checking Zillow and Craigslist for "Chicago 2BR"…'

def test_created_reply_when_everything_worked():
    outcome = RunOutcome("s1", (ok("zillow", 200), ok("craigslist", 14)), 0)
    assert created_reply(SEARCH, outcome, 15) == (
        '✅ Watching "Chicago 2BR". 214 places match right now. I\'ll post new ones and price drops in this channel.')

def test_created_reply_suggests_narrowing_a_broad_search():
    outcome = RunOutcome("s1", (ok("zillow", 900, more=True),), 0)
    assert created_reply(SEARCH, outcome, 15).endswith(
        "price drops are only caught among the newest ones. Narrow it with /search edit to catch them all.")

def test_created_reply_when_one_source_failed():
    outcome = RunOutcome("s1", (ok("zillow", 1), failed("craigslist", "Craigslist answered with an error (HTTP 500).")), 0)
    assert created_reply(SEARCH, outcome, 15) == (
        '✅ Watching "Chicago 2BR". 1 place matches on Zillow right now. Craigslist didn\'t work yet '
        "(Craigslist answered with an error (HTTP 500).); I'll keep trying.")

def test_created_reply_when_everything_failed():
    outcome = RunOutcome("s1", (failed("zillow", "Zillow is blocking requests."),), 0)
    assert created_reply(SEARCH, outcome, 15) == (
        '⚠️ I saved "Chicago 2BR", but couldn\'t check it yet: Zillow is blocking requests. '
        "I'll keep trying every 15 minutes and post here once it works.")

def test_statuses_reply_lines_and_empty_server():
    status = SearchStatus(SEARCH, "Needs attention", "Zillow: blocked", AT - timedelta(minutes=12), 214)
    assert statuses_reply([status], AT) == (
        "**Chicago 2BR** · Needs attention (Zillow: blocked) · last checked 12 min ago · 214 seen · <#10>")
    assert statuses_reply([], AT) == "No searches in this server yet. Start one with /search new."

def test_invite_url():
    assert invite_url(42) == ("https://discord.com/oauth2/authorize?client_id=42"
                              "&scope=bot+applications.commands&permissions=19456")
```

- [ ] **Step 3: Run to verify they fail** — `uv run pytest tests/test_settings.py tests/test_replies.py -q` → FAIL
- [ ] **Step 4: Implement** `settings.py`, the `text.py` additions, `replies.py`, `posting.py`, `app.py` and `main.py` to the pinned interfaces. Write `help_reply` in plain language: what the bot does, the commands, the limits (per server, total, interval), and what Healthy / Running / Paused / Needs attention / Not run yet mean. Write `.env.example` with every variable, commented, and `DISCORD_TOKEN=` left empty.
- [ ] **Step 5: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy && uv run apartment-hunter --help` → PASS, and usage printed.
- [ ] **Step 6: Owner end-to-end check (manual, needs the owner; a cloud session leaves it for Task 15 Step 6b)**

Ask the owner to: (1) create an application in the Discord Developer Portal, add a bot, turn on **Public Bot**, and copy the token **into `.env` themselves** (never into chat); (2) set `DEV_GUILD_ID` to a test server they own and `INTERVAL_MINUTES=10`. Then run `uv run apartment-hunter` (background), have the owner open the invite URL from the log and add the bot to the test server, and have them run `/search new area: <their city> max_price: …`. Expected: "Checking Zillow for …", then "✅ Watching … N places match right now…"; `/search list` shows it Healthy; `/search help` reads well; within about an hour, any new Zillow listing arrives as an embed with a photo. Note anything off before continuing.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .env.example src/apartment_hunter/settings.py src/apartment_hunter/text.py src/apartment_hunter/bot tests/test_settings.py tests/test_replies.py
git commit -m "Discord bot with /search new, list and help, posting Zillow alerts"
```

---

### Task 12: Managing Searches, lifecycle, owner alerts, allowlist

**Files:**
- Modify: `src/apartment_hunter/service.py`, `src/apartment_hunter/bot/app.py`, `src/apartment_hunter/bot/replies.py`
- Test: `tests/test_service.py`, `tests/test_replies.py`

**Interfaces:**
- Consumes: everything from Task 10 and Task 11
- Produces (service): `@dataclass(frozen=True) class SearchChanges: area: str | None = None; min_price: int | None = None; max_price: int | None = None; min_beds: float | None = None; min_baths: float | None = None; home_type: HomeType | None = None; pets: Pets | None = None; sources: tuple[str, ...] | None = None; name: str | None = None` (None = unchanged); `edit(server_id: int, name: str, changes: SearchChanges) -> tuple[Search, Pending | None]` (a Pending only when Criteria or Sources changed, which reseeds via `update_search(..., reseed=True)` + `run_now`); `pause(server_id, name) -> Search`; `resume(server_id, name) -> Search` (clears `attention`); `delete(server_id, name) -> None`; `check(server_id, name) -> tuple[Search, Pending]`; `server_removed(server_id: int) -> int`; owner alerts inside `run`.
- Pinned copy: unknown name in this server → `f"There's no search called \"{name}\" in this server. /search list shows them."`; a check too soon → `f"You can check \"{name}\" again in {m} minutes."` (m rounded up); owner broken DM → `f"⚠️ {Source} has failed in every search for 3 checks in a row: {reason} The host may be blocked, or {Source} changed its pages."`; owner recovered DM → `f"✅ {Source} is working again."`.
- Pinned owner rule (after each Run, for each Source of that Search, when `owner_id` is set): the Searches using that Source that aren't paused; all with a streak ≥ 3 and no owner alert sent → DM + `set_owner_alert_sent(source, True)`; owner alert sent and any with streak 0 → DM recovered + `set_owner_alert_sent(source, False)`.
- Bot: `edit` (`search` autocomplete + `new`'s options, all optional, + `name`), `pause`, `resume`, `delete` (an ephemeral confirmation with a red "Delete" button, a `discord.ui.View` with a 60 s timeout), `check` (deferred like `new`, then `checked_reply`). `on_guild_remove` → `service.server_removed`. `on_guild_join`: when `allowed_servers` is set and the server isn't in it, send `"This bot is private. Ask whoever runs it to add this server to its allowlist."` to the system channel when possible, then leave. Replies, all ephemeral: `edited_reply(search, reseeding: bool) -> str`; `paused_reply(search) -> str` = `f"Paused \"{name}\". Use /search resume to start it again."`; `resumed_reply(search) -> str` = `f"Resumed \"{name}\"."`; `deleted_reply(name: str) -> str` = `f"Deleted \"{name}\"."`; `checked_reply(search, outcome: RunOutcome | None) -> str` = `f"Checked \"{name}\": {n} new {'alert' if n == 1 else 'alerts'} posted."`, plus `f" {join_and(failed names)} didn't work ({'; '.join(errors)})"` when a Source failed, or `f"Still checking \"{name}\"; any alerts will show up in the channel."` when the outcome is None.

- [ ] **Step 1: Write the failing tests** (add to `tests/test_service.py`)

```python
def test_editing_criteria_reseeds_without_alerting(env):
    env.zillow.pages = [[make_listing("a")], [make_listing("a"), make_listing("b"), make_listing("c")]]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    _, pending = env.service.edit(1, "Home", SearchChanges(max_price=1800))
    env.scheduler.run_pending()
    assert pending.wait(0).results[0].seeded
    assert env.notifiers[10].listings == []

def test_renaming_keeps_state_and_does_not_reseed(env):
    env.zillow.pages = [[make_listing("a")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    renamed, pending = env.service.edit(1, "home", SearchChanges(name="Flat"))
    assert pending is None and renamed.name == "Flat"
    assert env.store.listing_count(search.id) == 1

def test_pause_skips_scheduled_runs_and_resume_restarts_them(env):
    env.zillow.pages = [[], [], []]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.service.pause(1, "Home")
    env.clock.advance(15)
    assert env.scheduler.run_pending() == 0
    assert env.service.statuses(1)[0].state == "Paused"
    env.service.resume(1, "Home")
    assert env.scheduler.run_pending() == 1

def test_delete_removes_the_search_and_its_listings(env):
    env.zillow.pages = [[make_listing("a")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.service.delete(1, "Home")
    assert env.service.statuses(1) == [] and env.store.listing_count(search.id) == 0

def test_check_runs_now_and_is_rate_limited(env):
    env.zillow.pages = [[], [], []]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    _, pending = env.service.check(1, "Home")
    env.scheduler.run_pending()
    assert pending.wait(0) is not None
    with pytest.raises(SearchError, match='check "Home" again in 5 minutes'):
        env.service.check(1, "Home")
    env.clock.advance(5)
    env.service.check(1, "Home")

def test_other_servers_cannot_touch_a_search(env):
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    with pytest.raises(SearchError, match="no search called \"Home\" in this server"):
        env.service.pause(2, "Home")

def test_bot_removed_from_a_server_deletes_its_searches(env):
    a, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="A"), can_post=True)
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="B"), can_post=True)
    assert env.service.server_removed(1) == 2
    assert env.store.searches(1) == []
    with pytest.raises(KeyError):
        env.scheduler.run_now(a.id)

def test_owner_hears_once_when_a_source_fails_everywhere_and_once_on_recovery(env):
    env.service.owner_id = 7
    env.zillow.pages = [[], []] + [ScrapeError("blocked.")] * 8 + [[], []]
    env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="A"), can_post=True)
    env.service.create(2, 20, 98, NewSearch("Chicago, IL", name="B"), can_post=True)
    for _ in range(5):
        env.scheduler.run_pending()
        env.clock.advance(15)
    assert [d for d in env.dms if d[0] == 7] == [
        (7, "⚠️ Zillow has failed in every search for 3 checks in a row: blocked. "
            "The host may be blocked, or Zillow changed its pages.")]
    env.scheduler.run_pending()
    assert env.dms[-1] == (7, "✅ Zillow is working again.")

def test_resume_after_the_channel_came_back_clears_the_reason(env):
    env.zillow.pages = [[], [make_listing("a")], [make_listing("a")]]
    search, _ = env.service.create(1, 10, 99, NewSearch("Chicago, IL", name="Home"), can_post=True)
    env.scheduler.run_pending()
    env.notifiers[10].gone = True
    env.clock.advance(15)
    env.scheduler.run_pending()
    env.notifiers[10].gone = False
    env.service.resume(1, "Home")
    assert env.service.statuses(1)[0].state == "Healthy"
    assert not env.scheduler.is_paused(search.id)
```

Add to `tests/test_replies.py`: `edited_reply(SEARCH, True)` mentions "fresh start" (pin it as `f"Updated \"{name}\". Its criteria changed, so it's taking a fresh look; no alerts for what's already listed."`), and `edited_reply(SEARCH, False) == f'Updated "{SEARCH.name}".'`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_service.py tests/test_replies.py -q` → FAIL
- [ ] **Step 3: Implement** the service methods, the owner rule, the replies and the bot commands and events.
- [ ] **Step 4: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy` → PASS
- [ ] **Step 5: Owner check (manual; a cloud session leaves it for Task 15 Step 6b)** — in the test server: `/search edit` the price (the reply says it's taking a fresh look), `/search pause` → `/search list` shows Paused, `/search resume`, `/search check` twice (the second is refused with the minutes), `/search delete` with the button. Delete the test channel of a live search and confirm the creator gets the DM.
- [ ] **Step 6: Commit**

```bash
git add src/apartment_hunter/service.py src/apartment_hunter/bot tests/test_service.py tests/test_replies.py
git commit -m "Edit, pause, resume, delete and check Searches; owner alerts and allowlist"
```

---

### Task 13: Craigslist Source

**Files:**
- Create: `src/apartment_hunter/sources/craigslist.py`
- Modify: `src/apartment_hunter/bot/main.py` (register it), `src/apartment_hunter/bot/app.py` (`sources` option on `new` and `edit`)
- Test: `tests/test_craigslist.py`

**Interfaces:**
- Consumes: `Criteria`, `HomeType`, `Pets` (Task 3); `SourcePage`, `ScrapeError`, `BlockedError`, `HttpClient`, `HttpResponse` (Task 4)
- Produces: `HOUSING_TYPES = {HomeType.APARTMENT_CONDO: (1, 2, 5, 8), HomeType.HOUSE: (3, 4, 6), HomeType.TOWNHOME: (9,)}` (Craigslist's apartment, condo, flat, loft / cottage, duplex, house / townhouse); `search_url(criteria: Criteria) -> str`; `parse_search_page(html: str, scraped_at: datetime) -> list[Listing]`; `class CraigslistSource(http: HttpClient, now: Callable[[], datetime] = lambda: datetime.now(UTC))` with `key = "craigslist"`, `fetch(criteria, page) -> SourcePage` (page > 1 → `SourcePage([], None, False)` without a request; page 1 → `SourcePage(listings, None, False)`), `results_url(criteria) -> str` (= `search_url`)
- Pinned URL: `f"https://{area.craigslist_site}.craigslist.org/search/apa?"` + `urlencode` of, in order: `sort=date`, `lat`, `lon`, `search_distance` (= radius), then only those set: `min_price`, `max_price`, `min_bedrooms`, `min_bathrooms` (numbers without a trailing `.0`), `pets_dog=1` / `pets_cat=1`, and one `housing_type` per code.
- Pinned parsing: `ol.cl-static-search-results` missing → `ScrapeError("Craigslist's page layout changed, so listings couldn't be read. The bot needs an update.")`; each `li.cl-static-search-result` (skip `li.cl-static-hub-links`): url = the `a` href, listing_id = the URL's last path segment, name = `.title` text, address = `.location` text (stripped), price from `.price` ("$2,100" → 2100; missing → None), beds, baths and image None. HTTP 403/429 → `BlockedError("Craigslist is blocking requests from this computer right now. This usually clears up on its own.")`; other non-200 → `ScrapeError(f"Craigslist answered with an error (HTTP {status}).")`. The results page carries no beds or photos, so Craigslist alerts show beds as "—" and have no image. The bedroom filter is applied by Craigslist itself.

- [x] **Step 1: Save a real page as a fixture** (done 2026-11-17 from a home connection). To refresh it, re-run this from a computer that reaches Craigslist; it keeps 5 real results:

```bash
mkdir -p tests/fixtures/craigslist && uv run python - <<'EOF'
import re
from curl_cffi import requests
html = requests.get("https://chicago.craigslist.org/search/apa?sort=date", impersonate="chrome", timeout=25).text
items = re.findall(r'<li class="cl-static-search-result".*?</li>', html, re.S)[:5]
with open("tests/fixtures/craigslist/chicago_apa.html", "w", encoding="utf-8") as f:
    f.write('<html><body class="no-js"><ol class="cl-static-search-results">' + "".join(items) + "</ol></body></html>")
EOF
```

- [ ] **Step 1b: Write the failing tests** (`tests/test_craigslist.py`)

```python
ITEM = ('<li class="cl-static-search-result" title="Sunny 2BR near the park">'
        '<a href="https://www.craigslist.org/view/d/chicago-sunny-2br-near-the-park/ehuHXXepmPCU6amEth4GEd">'
        '<div class="title">Sunny 2BR near the park</div><div class="details">'
        '<div class="price">$2,100</div><div class="location">\n  Lincoln Park\n</div></div></a></li>')

def page(*items):
    return ('<html><body class="no-js"><ol class="cl-static-search-results">'
            '<li class="cl-static-hub-links"><div>see also</div></li>' + "".join(items) + "</ol></body></html>")

def test_search_url():
    c = Criteria(LINCOLN_PARK, min_price=1500, max_price=2400, min_beds=2, min_baths=1,
                 home_type=HomeType.TOWNHOME, pets=Pets.DOGS)
    assert search_url(c) == ("https://chicago.craigslist.org/search/apa?sort=date&lat=41.9237&lon=-87.6505"
                             "&search_distance=2&min_price=1500&max_price=2400&min_bedrooms=2"
                             "&min_bathrooms=1&pets_dog=1&housing_type=9")

def test_search_url_repeats_housing_types_and_skips_unset_filters():
    assert search_url(Criteria(CHICAGO, home_type=HomeType.HOUSE)) == (
        "https://chicago.craigslist.org/search/apa?sort=date&lat=41.8781&lon=-87.6298&search_distance=10"
        "&housing_type=3&housing_type=4&housing_type=6")

def test_parses_a_result():
    assert parse_search_page(page(ITEM), AT) == [Listing(
        source="craigslist", listing_id="ehuHXXepmPCU6amEth4GEd",
        url="https://www.craigslist.org/view/d/chicago-sunny-2br-near-the-park/ehuHXXepmPCU6amEth4GEd",
        name="Sunny 2BR near the park", address="Lincoln Park", price_min=2100, price_max=2100,
        beds_min=None, beds_max=None, baths_min=None, baths_max=None, image_url=None, scraped_at=AT)]

def test_missing_price_is_none():
    no_price = ITEM.replace('<div class="price">$2,100</div>', "")
    assert parse_search_page(page(no_price), AT)[0].price_min is None

def test_no_results_is_an_empty_list():
    assert parse_search_page(page(), AT) == []

def test_page_without_the_results_list_is_a_layout_change():
    with pytest.raises(ScrapeError, match="layout changed"):
        parse_search_page("<html><body>hi</body></html>", AT)

def test_page_two_makes_no_request():
    http = FakeHttp(HttpResponse(200, page(ITEM)))
    assert CraigslistSource(http, now=lambda: AT).fetch(Criteria(CHICAGO), 2) == SourcePage([], None, False)
    assert http.requested == []

def test_refused_status_is_blocked():
    with pytest.raises(BlockedError, match="Craigslist is blocking"):
        CraigslistSource(FakeHttp(HttpResponse(403, "")), now=lambda: AT).fetch(Criteria(CHICAGO), 1)

def test_parses_a_saved_real_page(fixture_html):
    listings = parse_search_page(fixture_html("craigslist/chicago_apa.html"), AT)
    assert len(listings) == 5
    assert all(l.listing_id and l.url.startswith("https://www.craigslist.org/") and l.name for l in listings)
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_craigslist.py -q` → FAIL
- [ ] **Step 3: Implement** `craigslist.py`. Register it in `main.py` (`{"zillow": ..., "craigslist": CraigslistSource(run_http)}`, `source_keys=("zillow", "craigslist")`) and add the `sources` option to `new` and `edit` (choices: "Zillow and Craigslist" → both, "Zillow only", "Craigslist only").
- [ ] **Step 4: Verify** — `uv run pytest -q && uv run ruff check && uv run mypy` → PASS
- [ ] **Step 5: Owner check (manual; a cloud session leaves it for Task 15 Step 6b)** — `/search new` in the test server now says "Checking Zillow and Craigslist…" and counts both; a Craigslist alert arrives without a photo, with beds "—".
- [ ] **Step 6: Commit**

```bash
git add src/apartment_hunter/sources/craigslist.py src/apartment_hunter/bot tests/test_craigslist.py tests/fixtures/craigslist
git commit -m "Add the Craigslist Source"
```

---

### Task 14: Docker image and CI without a browser

**Files:**
- Modify: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.github/workflows/ci.yml`

**Interfaces:**
- Produces: an image whose entrypoint is `tini -- apartment-hunter`, with state in `/app/data` (`DATA_DIR=/app/data`); compose reads `.env` and mounts `./data`.

- [ ] **Step 1: Rewrite the Dockerfile.** Keep the base image, the uv copy and the two-step `uv sync --frozen --no-dev`. Install only `tini` with apt (no chromium, xvfb, xauth or fonts). Keep the non-root `hunter` user and `VOLUME ["/app/data"]`. ENV becomes `PATH=/opt/venv/bin:$PATH DATA_DIR=/app/data`; `ENTRYPOINT ["tini", "--", "apartment-hunter"]`. Update the header comment: no browser, outbound connections only.
- [ ] **Step 2: Rewrite `docker-compose.yml`:** service `apartment-hunter`, `build: .`, `restart: unless-stopped`, `env_file: .env`, `volumes: ["./data:/app/data"]` (drop `shm_size`, the `config.toml` mount and `/etc/localtime`). In `.dockerignore`, drop the `config.toml` and `search_page1.html` lines.
- [ ] **Step 3: CI.** Keep the docker job's `docker run --rm apartment-hunter:ci --help` and rename the step to "Bot starts inside the image".
- [ ] **Step 4: Verify locally**

Run: `docker build -t apartment-hunter:dev . && docker run --rm apartment-hunter:dev --help`
Expected: the build succeeds and usage is printed. If the Docker daemon isn't running, ask the owner to start Docker Desktop, or note that CI covers it. In a cloud session, see Running in a cloud session (Docker).

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore .github/workflows/ci.yml
git commit -m "Docker image runs the bot with no browser"
```

---

### Task 15: Docs and handoff

**Files:**
- Modify: `README.md`, `GLOSSARY.md`, `TODO.md`, `CODING_STANDARDS.md`

- [ ] **Step 1: README.** Rewrite it for the bot.
  - What it does, with the spec's Discord example.
  - **For people using it:** get the invite link from whoever runs the bot, then the `/search` commands and what each status means.
  - **For the owner:** the Developer Portal checklist (application, bot, Public Bot, token into `.env`), `docker compose up -d`, the invite URL in the log, the settings table (every variable from `.env.example`; owner alerts need `OWNER_USER_ID` and the owner sharing at least one server with the bot), and hosting (Task 1's results and the decision).
  - **Troubleshooting:** each pinned error message and what to do.
  - Keep the personal-use disclaimer, now naming Zillow's and Craigslist's terms.
  - **Development:** `uv sync`, `uv run pytest`, `scripts/feasibility_check.py` for re-checking a host.
  - A short "How it works" diagram (the spec's Pieces).
  - Mention that v0.2 lives at the `v0.2-apartments-com` tag.
- [ ] **Step 2: GLOSSARY.md** — apply the spec's "Glossary changes" exactly (new: Owner, Host, Server, Source, Criteria; changed: Search, Run, Health alert; removed: Webhook, Page cap, App data). Update the one-line description at the top.
- [ ] **Step 3: TODO.md** — replace the v0.2 and v0.3 sections with "v1.0: Discord bot (done)" and an Ideas list taken from the spec's non-goals and later ideas: deeper sweeps for price drops on broad searches, pasting a Zillow URL for a drawn area, commute times, more Sources, merging duplicates across Sources. Keep the Maintenance notes, adapted (refresh Zillow and Craigslist fixtures when parsing breaks; re-run the feasibility check after host changes).
- [ ] **Step 4: CODING_STANDARDS.md** — rename "Behaviour the CLI promises" to "Behaviour the bot promises": the `/search` commands and options, the `.env` settings and the Docker image keep working as in the previous release unless a ticket says otherwise, and a diff that changes them names the change in its commit message. Change the Tests bullet's "web app ... TestClient" example to the `SearchService` seam.
- [ ] **Step 5: Final verification** — `uv run ruff check && uv run mypy && uv run pytest -q` → all pass. `git status` is clean apart from these docs.
- [ ] **Step 6: Commit**

```bash
git add README.md GLOSSARY.md TODO.md CODING_STANDARDS.md
git commit -m "Docs for the Discord bot"
```

- [ ] **Step 6b (cloud sessions): Owner checks** — the manual checks from Task 11 Step 6, Task 12 Step 5 and Task 13 Step 5, which a cloud session leaves for now. Ask the owner to set up the Discord application and `.env` as in Task 11 Step 6, then, on their computer, to update their checkout of `discord-bot` (or clone it) and run `uv run apartment-hunter`. Go through the three checks with them, fix what they find (one commit per fix, pushed), and only then move to Step 7.

- [ ] **Step 7: Handoff (ask the owner; do none of these without a yes)** — open a PR from `discord-bot` to `main`; push the `v0.2-apartments-com` tag; post the design as a GitHub issue (the repo keeps specs there) and close #1 and #3–#21 with a pointer to it. In a cloud session, do these through REST (`gh pr`, `gh issue` and tag pushes are refused there): the PR with the built-in GitHub tools or `gh api repos/DanXSpace/apartment-hunter/pulls -f head=discord-bot -f base=main -f title=… -f body=…`; the tag with `gh api repos/DanXSpace/apartment-hunter/git/refs -f ref=refs/tags/v0.2-apartments-com -f sha=5fddee608c85fa8ec7676a97280d24f74a3a8236`; the issue with `gh api repos/DanXSpace/apartment-hunter/issues -f title=… -f body=…`; and each closing with a comment (`gh api repos/DanXSpace/apartment-hunter/issues/<n>/comments -f body=…`) followed by `gh api -X PATCH repos/DanXSpace/apartment-hunter/issues/<n> -f state=closed`.

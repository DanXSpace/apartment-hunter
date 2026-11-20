# Apartment Hunter as a Discord bot: design

Date: 2026-11-17 · Status: approved by the owner 2026-11-17; revised during planning (HTTP library, Craigslist fields, order of work) · Supersedes the v0.3 desktop-app spec ([#1](https://github.com/DanXSpace/apartment-hunter/issues/1))

Terms follow `GLOSSARY.md` (Search, Listing, Run, Seeding run, Alert, Health alert, Blocked, Needs attention). New terms are defined in [Glossary changes](#glossary-changes). Items marked **(my call)** were decided while writing this, not by the owner; they are collected in the [decisions log](#decisions-log) for review.

## Why

Apartment Hunter should be something the owner, their friends and eventually other people can use with no technical setup: say what kind of place you want and where, and new matches show up in your own Discord server.

Two things stand in the way today:

1. **It has to open a browser window.** apartments.com's bot protection (Akamai) only lets through a real, visible browser that opens the page itself (see the table in #1). So a Chrome window pops up for every results page, every run, which defeats the point of a background tool. This is the owner's biggest complaint.
2. **It's built for terminal users.** Setup means installing Python and uv, editing `config.toml` and `.env`, building a search on apartments.com and copying its URL, and creating a Discord webhook. The planned v0.3 desktop app softens some of this, but every friend would still install an unsigned app, keep their computer awake, and see browser windows.

apartments.com was only where the project started; the owner doesn't need it. On 2026-11-16 one plain request (no browser) per site from a home connection got listings back from **Zillow, Redfin, Craigslist, HotPads, Trulia, Rent.com and Apartment List**. **apartments.com, Realtor.com, RentCafe, Zumper and PadMapper** blocked it. Zillow's page embeds structured listing data (id, address, price, beds, baths, photo, link). So the browser can go away entirely.

## What it is

A Discord bot. The owner runs one copy on one always-on machine (the **host**). Anyone the owner gives the invite link to adds the bot to their own Discord server and creates Searches with a slash command. The bot checks Zillow and Craigslist on a schedule and posts new listings and price drops in the channel where the Search was created.

What a friend sees (illustrative numbers):

```
/search new  area: Chicago, IL   max_price: 2400   min_beds: 2   pets: dogs

  Apartment Hunter: Checking Zillow and Craigslist for "Chicago, IL · 2+ bd · up to $2,400 · dogs"…
  Apartment Hunter: ✅ Watching "Chicago, IL · 2+ bd · up to $2,400 · dogs".
                    214 places match right now. I'll post new ones and price drops in this channel.

(later, same channel)
  🏠 1840 W Grand Ave, Chicago, IL                         [photo]
     New listing on Zillow
     💰 $2,250/mo    🛏 2    🛁 1
     Chicago, IL · 2+ bd · up to $2,400 · dogs
```

## Goals

- **Nothing to install for people who use it.** Adding the bot to a server and typing one command is the whole setup.
- **Criteria are set in Discord**, not by building a search on a listing site and copying its URL.
- **Never interrupts anyone.** No browser and no windows anywhere; the bot runs unattended on the host.
- **Alerts arrive within about 15–30 minutes** of a listing appearing on a Source.
- **Keep what already works** in `SearchRunner`: seeding runs (no flood on day one), price-drop alerts, health alerts when a Search keeps failing and again when it recovers, a listing only counts as alerted once Discord accepts the message, and polite request pacing.
- **One host serves many Discord servers**, and no server can see or change another server's Searches.
- **Self-hostable**: someone technical can run their own copy from the Docker image with their own bot token.

## Non-goals (this version)

- apartments.com, or any Source that needs a browser.
- Homes for sale. This is for rentals: apartments, houses, condos and townhomes for rent. **(my call)**
- Notification channels other than Discord.
- A web dashboard or phone app. Discord is the interface.
- Drawing a custom map area. An area is a city, neighborhood or ZIP code. (Later idea: accept a pasted Zillow search URL for a drawn area.)
- Commute times, favourites, or browsing listings inside Discord.
- Merging the same place listed on both Zillow and Craigslist into one Alert.
- A public bot listed in bot directories, or accounts, billing and support for strangers.

## Anti-goals (failure even if it technically works)

- Anything that pops up on someone's screen, or needs software installed by people who only use the bot.
- An alert flood: posting every current listing when a Search is created or edited, or dumping dozens of Alerts at once after downtime.
- Silent failure: a Search that has stopped working without its channel, and the owner, hearing about it.
- Getting the host's IP address blocked by requesting too often.
- One server's Searches, channels or Alerts leaking into another server.
- Friends having to touch config files, webhooks or a terminal.

## Constraints

- **Terms of use.** Zillow's and Craigslist's terms forbid automated access, as apartments.com's do. The owner chose this approach knowing that, for personal use among friends. Keep request rates low, keep the README's personal-use disclaimer, and don't promote the bot publicly.
- **Network.** Zillow answered a single plain request from a home connection. Whether it keeps answering when polled every 15 minutes, and whether it answers a cloud server at all, is untested. This decides where the bot runs (see [Hosting](#hosting)) and is the first thing to check.
- **Request budget.** A regular Run reads only the newest page of each Source. At the default limits (25 Searches, 2 Sources, 15 minutes) that is about 200 requests an hour across both sites, one at a time.
- **Discord.** The bot needs only the `guilds` gateway intent (no message content, no privileged intents) and, in an alert channel, View Channel, Send Messages and Embed Links. The application must have *Public Bot* turned on in the Developer Portal so friends can add it themselves. An interaction must be acknowledged within 3 seconds; the reply can then be edited for up to 15 minutes.
- **Repo standards** (`CODING_STANDARDS.md`): tests at the seam with hand-written fakes, offline and deterministic; ruff and mypy in CI. This design is the "ticket says otherwise" that ends the CLI and `config.toml` promise (see [Retiring the old paths](#retiring-the-old-paths)).

## Approaches considered

| | Approach | Trade-offs | Outcome |
|---|---|---|---|
| **A** | **Discord bot on one always-on host** (a cloud server, or a computer at the owner's place) | Nothing for users to install; criteria set in Discord; less work than B. The owner hosts it for everyone; if a site blocks the host, everyone's Alerts pause; at home it only runs while that computer is on. | **Picked by the owner** |
| B | Desktop app each person installs (the v0.3 plan, minus the browser) | Nobody depends on the owner. But unsigned installers for three operating systems with scary warnings, Alerts stop when a laptop sleeps, and the most work (tray, start-at-login, packaging). | Not picked |
| C | Pass Zillow's own saved-search alert emails on to Discord | No scraping at all. But criteria are set on Zillow, not in the app, and users must set up email forwarding. | Dropped: fiddly setup, criteria outside the app |
| D | Keep apartments.com and hide the browser (fixed-up headless, hidden window, virtual display) | Keeps the original site. Per-OS work, untested on Mac and Windows, fragile against Akamai, still needs a browser. | Dropped: apartments.com isn't needed |
| E | Paid scraping API from the cloud | Someone else handles bot protection. Costs per request, and these services get blocked on these sites too (an Apify apartments.com scraper was discontinued for that reason). | Dropped: cost and fragility |

Why A: it is closest to what the owner described (set criteria in an app, get pinged in your own server, never interrupted), the people using it already live in Discord, and it is less work than B.

Scrapling's stealth browser was also asked about: it drives navigation with automation (`page.goto`) and only claims to beat Cloudflare, while Akamai rejects automation-driven navigation, which matches the owner's v0.2 test results. Scrapling stays in the project for parsing.

## How it works

### Pieces

```
Discord ⇄ Bot (slash commands, posting) ⇄ Search service ⇄ Store (SQLite)
                                               │
                              Scheduler (one Run at a time, jittered)
                                               │
                     SearchRunner → Sources (Zillow, Craigslist) → plain HTTP
```

- **Bot.** discord.py **(my call: mature, Python, fits the codebase)**. Registers the `/search` commands, turns interactions into calls on the Search service, turns results into replies, and posts Alerts. Holds no rules of its own.
- **Search service.** The seam the Bot and the tests both call: create, list, edit, pause, resume, delete and check-now a Search. Enforces limits, permissions checks and server isolation.
- **Store.** SQLite, as today, with Searches keyed by id and owned by a server.
- **Scheduler.** The existing one (sequential, ±20% jitter, run-now queue, pause), fed from the Store instead of `config.toml`.
- **SearchRunner.** The existing seeding, alert and health rules, now running every Source of a Search and posting through a Notifier for the Search's channel instead of a webhook.
- **Sources.** Zillow and Craigslist, each turning Criteria into requests and pages into Listings. Plain HTTP through curl_cffi with Chrome's TLS fingerprint (the library Scrapling's `Fetcher` runs on; installing `Fetcher` itself would pull Playwright back in), parsed with Scrapling's selector as today.

### Searches and Criteria

A Search belongs to one Discord server and posts to one channel. It stores:

- **id** (generated), **server id**, **channel id**, **created by** (Discord user id), **created at**, **paused**
- **name**, shown in Alerts and lists; defaults to a summary of the Criteria such as "Chicago, IL · 2+ bd · up to $2,400 · dogs"
- **Sources**: Zillow, Craigslist, or both (default both)
- **Criteria**:
  - **area** (required): a city, neighborhood or ZIP code. Resolved once, when the Search is created or its area changes, to a Zillow region and a Craigslist site plus radius; the resolved values are saved with the Search. While the user types, Discord's autocomplete suggests matching places. Autocomplete fires on every keystroke, so lookups are cached, start at 3 characters, and are throttled across the whole bot; when throttled, no suggestions are shown and the typed text is resolved on submit. This matters because every lookup is a request from the host. **(my call)**
  - **min price / max price** (monthly rent)
  - **min beds** (a studio counts as 0) and **min baths**
  - **home type**: any (default), apartment or condo, house, townhome
  - **pets**: no requirement (default), dogs, cats

Names are unique within a server. Identity is the id, so renaming a Search never resets it. Changing its Criteria or Sources does reset it: the next Run is a seeding run, so an edit can't flood the channel. (The glossary's "renaming starts fresh" rule goes away.)

### Commands

All under one `/search` group so they are easy to find in Discord's command list. **(my call: the glossary names the concept Search and lists "watch" as a term to avoid)**

| Command | What it does |
|---|---|
| `/search new area:… [max_price] [min_price] [min_beds] [min_baths] [home_type] [pets] [sources] [name]` | Checks the bot can post in this channel, checks limits, resolves the area, saves the Search, queues its seeding run ahead of the schedule, and edits the reply with the result: "✅ Watching … N places match right now…" or a plain-language reason it failed. An area that can't be resolved, a missing permission or a reached limit refuses the command and saves nothing. A seeding run that fails keeps the Search, marked Needs attention, and its next scheduled Run is a seeding run again. **(my call)** |
| `/search list` | This server's Searches: name, Criteria summary, channel, status (Healthy / Running / Paused / Needs attention / Not run yet), last checked, and the reason if something is wrong. |
| `/search edit search:… [any criteria] [sources] [name]` | Changes a Search. A Criteria or Sources change queues a fresh seeding run and the reply says so. A name-only change doesn't. |
| `/search pause search:…` / `/search resume search:…` | Stops or restarts Runs and Alerts without losing the Search. |
| `/search delete search:…` | Deletes the Search after a confirmation button. |
| `/search check search:…` | Queues a Run now (still one at a time) and reports what it found. At most once per 5 minutes per Search, so it can't be used to hammer a Source. **(my call)** |
| `/search help` | How it works, the limits, and what each status means. |

- The `search:` option autocompletes this server's Search names only.
- Anyone in a server can use the commands by default; server admins can restrict them with Discord's built-in Integrations settings. **(my call)**
- Replies to `list`, `edit`, `pause`, `resume`, `delete`, `check` and `help` are visible only to the person who ran them. The `new` confirmation stays visible to the channel, since that's where the Alerts will appear. **(my call)**
- A seeding run can take longer than Discord's 3-second limit, so `new`, `edit` and `check` acknowledge at once ("Checking Zillow and Craigslist…") and edit the reply when the Run finishes. If a Run is already in progress, theirs goes next.

### Runs and Alerts

- **Seeding run** (on create, and after a Criteria or Sources edit): reads up to **3 pages** of each Source, newest first, records every Listing without alerting, then reports the Source's own total match count when it gives one (Zillow does). If the Search has more matches than fit on one page, the reply suggests narrowing it (see price drops below). **(my call)**
- **Regular Run**: every **15 minutes ± 20%** per Search; the owner can change the default, users can't. **(my call)** Reads **page 1** of each Source, newest first. A Listing not seen before for this Search → **new listing** Alert. A seen Listing whose lowest price went down → **price drop** Alert. Same rules as `SearchRunner` today.
- **Price drops on broad Searches**: a regular Run only sees the newest page, so price drops are caught for every Listing only when a Search's matches fit on one page (about 40 on Zillow). For broader Searches they are caught among the newest Listings only. Deeper periodic sweeps are a later improvement. **(my call)**
- **Burst cap**: at most **10** Alerts per Search per Run. Any more are summarised in one message ("…and 14 more new places on Zillow", linking to the Source's results) and recorded as seen once Discord accepts that message. This keeps a host that was asleep overnight from flooding channels. **(my call)**
- **Listing keys**: a Search's Listings are keyed by (Source, Source's listing id). The same place on both Sources may alert twice (a non-goal to merge).
- **Delivery**: as today, a Listing is only marked seen once Discord accepts its Alert; failures retry on the next Run.
- **Health, per Source** **(my call)**: consecutive failures are counted per (Search, Source). After 3 failed Runs in a row for a Source, a ⚠️ Health alert goes to the channel with a plain-language reason and says whether the other Source is still working; a ✅ follows on recovery. The Search shows **Needs attention** after one failure, as the desktop-app design intended.
- **Owner alerts** **(my call)**: when a Source has failed in every Search that uses it for 3 Runs in a row (most likely the host is Blocked, or the site changed its pages), the bot sends the owner a direct message, once, and again on recovery. Requires the owner's Discord user id in the settings, and the owner must share at least one server with the bot for Discord to deliver the message.
- **Alert format**: today's embed (title, address, price, beds, baths, photo, link), plus "New listing on Zillow" / "Price drop on Craigslist", the Search name in the footer, and Discord's embed timestamp so each person sees their own local time.

### Sources

Both implement one interface: given resolved Criteria and a page number, return that page's Listings (newest first) and, when available, the total match count; or raise `ScrapeError` / `BlockedError` with a plain-language reason.

**Zillow** (primary; Zillow's rental listings also appear on HotPads and Trulia):

- *Area*: Zillow's own location lookup turns free text into a region (id, type, display name, map bounds). If that lookup isn't usable with plain requests, geocode the text and search by map bounds instead.
- *Request*: the rentals search for the region with the Criteria as filters, sorted newest first. Listings come from the page's embedded JSON (`listResults`: id, address, price or per-unit price range, beds, baths, photo, detail URL). A building with several units becomes one Listing with ranges, which the `Listing` model already supports.
- *Fallbacks*: if Zillow ignores a filter in a plain request, filter locally after parsing. If newest-first sorting isn't available, say so before building further, since regular Runs rely on it.
- *Blocked*: a bot-protection challenge page, or HTTP 403/429.

**Craigslist** (individual landlords):

- *Area*: the nearest Craigslist site to the resolved area, plus a search radius around the area's ZIP code or centre.
- *Request*: the apartments/housing-for-rent search for that site with the Criteria as query parameters (Craigslist supports price, bedrooms, bathrooms, housing type, cats and dogs), newest first. Listings come from the static results list (id, title, price, neighborhood, link). The static page returned about 360 results in one response during the probe.
- *Beds and photos*: the results list has neither. Craigslist's own bedroom filter narrows the results, and Craigslist alerts go out without a photo (fetching each listing would multiply requests).

**Politeness** (all Sources): one request at a time across the whole bot (Runs are already sequential); a random pause of a few seconds between requests to the same Source; the existing retry backoff on failures.

### Limits and access

All are owner settings (environment variables) with these defaults. **(my call)**

- **5 Searches per Discord server, 25 in total.** `/search new` refuses with a friendly message when either limit is reached.
- **15-minute interval** (minimum 10).
- **Who can add the bot**: anyone with the invite link by default. An optional allowlist of server ids makes the bot leave any other server it's added to, with a short message.

### Bot lifecycle

- **Removed from a server** → that server's Searches are deleted. **(my call)**
- **Alert channel deleted, or the bot can no longer post there** → the Search is paused and marked Needs attention with the reason; the bot tries to tell its creator by direct message, and `/search list` shows the reason. **(my call)**
- **Bot starts or restarts** → Searches pick up from the Store; nothing is re-seeded. Runs that came due while it was down run once, not once per missed interval, and are spread across the first interval rather than fired back to back, so a restart doesn't look like a burst of traffic. **(my call)**

### Hosting

One Docker container with outbound connections only (Discord, Zillow, Craigslist). With no browser, the image drops Chromium and the virtual display and gets much smaller. State (SQLite database, log file) lives in a mounted data directory, guarded by the existing single-instance lock.

Where it runs is settled by the **feasibility check**, the first piece of work:

1. From the owner's home connection, run the planned request pattern (a handful of realistic Searches against both Sources, every ~15 minutes) for a few hours and confirm both keep answering with Listings.
2. Send the same requests from a cloud server (for example a cheap virtual server, or a one-off GitHub Actions run; either needs the owner's go-ahead, since it uses their accounts).

A Source passes if it keeps returning Listings for the whole test; an occasional failure that clears on the next try is fine, a challenge page or block that persists is not.

Then:

- **Cloud answers** → host it there: always on, a few dollars a month.
- **Only home answers** → host it at the owner's place: Docker Desktop on the owner's Mac (Runs pause while it sleeps; the restart rule above avoids a flood), or a spare always-on computer such as an old laptop or a Raspberry Pi.
- **Home stops answering under sustained polling** → stop and revisit with the owner (fewer Searches, longer interval, or a different Source mix) before building more.

### Owner setup

A README section with a one-time checklist:

1. In the Discord Developer Portal, create an application, add a bot, turn on *Public Bot*, and copy the bot token.
2. Put the token in `.env`, plus optionally the owner's Discord user id (for owner alerts) and any limit or allowlist overrides.
3. `docker compose up -d`.
4. Share the invite link the bot logs on startup (scopes `bot applications.commands`; permissions View Channel, Send Messages, Embed Links).

Slash commands register globally on startup. A development setting registers them to one test server instead, where changes show up immediately. **(my call)**

### Retiring the old paths

**(my call; the biggest one for the owner to check)**

- First, tag the current `main` as `v0.2-apartments-com`, the same way `v0.1-kafka` was preserved.
- Remove: the apartments.com scraper, the browser fetchers (`chromium` and `stealth`), the CLI and its `config.toml` / `.env` webhook config, and the desktop app with its web UI.
- Remove from dependencies and the Docker image: Playwright, Chromium, Xvfb, FastAPI, uvicorn and Jinja. Keep Scrapling (parsing only, without its `fetchers` extra) and python-dotenv; add curl_cffi and discord.py.
- Reuse: `Listing`, `SearchRunner`'s rules, `Scheduler`, `Store` (new schema), the Discord embed builder, the data-directory lock and log file, the Docker and CI setup, and the test style.
- No migration of v0.2 databases: their Searches have no Discord server or channel and would have to be recreated in Discord anyway. **(my call)**
- The desktop-app spec (#1) and its open tickets (#3–#21) are superseded. Close them with a pointer to this design once it's published; that needs the owner's go-ahead, since it changes the public repo.
- Update `README.md`, `TODO.md`, `GLOSSARY.md` and `CODING_STANDARDS.md` (whose "Behaviour the CLI promises" section becomes a promise about the bot's commands and the Docker image).

## Testing Decisions

The pre-agreed seam list for TDD (per `CLAUDE.md`), following `CODING_STANDARDS.md`:

- **Main seam: the Search service**, built with fake Sources (scripted Listings, totals and errors), a fake Notifier per channel (records what was posted, can be made to fail or to report a missing channel), an in-memory Store and an injected clock, with background work driven through `Scheduler.run_pending`. Covers: create (seeding, limits, posting check), list, edit (re-seeding on Criteria change only), pause/resume, delete, check-now, server isolation, new-listing and price-drop Alerts, the burst cap, per-Source Health alerts and recovery, owner alerts, channel-gone and bot-removed handling, and restart behaviour.
- **Sources**: parsers tested against saved Zillow and Craigslist pages in `tests/fixtures/`; Criteria-to-request and area resolution tested as pure functions or with a fake HTTP layer.
- **Bot layer**: kept thin. Reply text and embeds are built by pure functions and tested as such. Nothing runs against Discord automatically; a short manual checklist in a test server before each release.
- Existing `SearchRunner`, `Scheduler`, `Store` and embed tests are adapted rather than dropped.
- No test touches Zillow, Craigslist or Discord (pytest already blocks non-loopback connections).

## Glossary changes

- **Owner** (new): the person who runs the bot and its host.
- **Host** (new): the one machine the bot runs on.
- **Server** (new): a Discord server the bot has been added to. Every Search belongs to one.
- **Source** (new): a listing site the bot reads (Zillow, Craigslist).
- **Criteria** (new): what a Search looks for: area, price range, beds, baths, home type, pets.
- **Search** (changed): Criteria plus the channel its Alerts go to, identified by an id; renaming no longer starts it fresh, changing its Criteria does.
- **Run** (changed): one pass over a Search: read the newest page of each of its Sources and compare with what was seen before.
- **Health alert** (changed): per Source within a Search.
- **Webhook**, **Page cap**, **App data** (removed).

## Decisions log

**Decided by the owner** (conversation, 2026-11-16 and 17):

- A browser window popping up is the dealbreaker; it must run in the background without interrupting anything.
- It's for the owner, friends and eventually others, so no terminal or config files for the people using it.
- Criteria are set in the app, and Alerts arrive in the user's own Discord server.
- apartments.com isn't required ("just the one I started with").
- Approach A: a Discord bot on one always-on host, accepting that the owner hosts it for everyone, that a block on the host pauses everyone, and that Zillow's terms forbid automated access.

**My calls, for the owner to check:**

1. Retire the CLI, `config.toml`, the apartments.com scraper, the browser fetchers and the desktop app, preserved at a `v0.2-apartments-com` tag.
2. Rentals only; no homes for sale.
3. Start with Zillow and Craigslist; other Sources later.
4. A `/search …` command group rather than `/watch`.
5. Limits: 5 Searches per server, 25 total, a 15-minute interval users can't change, and an optional server allowlist.
6. Seeding reads 3 pages per Source; regular Runs read the newest page, so price drops on broad Searches are only caught among the newest Listings.
7. At most 10 Alerts per Search per Run, with the rest summarised.
8. Health tracked per Source; the owner gets a direct message when a Source fails across every Search.
9. Anyone in a server can manage its Searches; admins can restrict that through Discord.
10. Searches are deleted when the bot is removed from a server, and paused when their channel disappears.
11. discord.py for the bot; no migration of v0.2 databases.
12. Traffic guards: area lookups cached and throttled, `/search check` at most every 5 minutes per Search, and Runs spread out after a restart.
13. A Search whose seeding run fails is kept (Needs attention) and retried; an area that can't be resolved saves nothing.

## Left to the builder

- Exact Zillow and Craigslist request formats, area resolution, and local filtering where a filter isn't honoured.
- How the asyncio bot and the threaded Scheduler talk to each other (for example, Runs in a worker thread posting through the bot's event loop).
- The Store schema and the Search service's API.
- Wording of replies, errors and embeds, in plain language like the examples above.
- The feasibility check's exact script and duration.
- Order of work, beyond: the feasibility check first; a working `/search new` → Alert path with Zillow before the other commands; Craigslist after Zillow; retiring the old paths at the start of the branch, since `main` keeps v0.2 until the branch merges.

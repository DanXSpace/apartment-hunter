# Apartment Hunter

A Discord bot that watches rental sites in the US, the UK, Germany, France, Spain, Italy, Portugal and the Netherlands. Add it to your server, tell it what you're looking for with `/search`, and new listings and price drops are posted in your channel. No browser, nothing to install if you're just using it.

```
/search new  area: Chicago, IL   max_price: 2400   min_beds: 2   pets: Small dogs

  Apartment Hunter: Checking Zillow and Craigslist for "Chicago, IL · 2+ bd · up to $2,400 · small dogs"…
  Apartment Hunter: ✅ Watching "Chicago, IL · 2+ bd · up to $2,400 · small dogs".
                    214 places match right now. I'll post new ones and price drops in this channel.

(later, same channel)
  🏠 1840 W Grand Ave, Chicago, IL                         [photo]
     New listing on Zillow
     💰 $2,250/mo    🛏 2    🛁 1
     Chicago, IL · 2+ bd · up to $2,400 · small dogs
```

- **No alert flood on day one.** A search's first check quietly notes what's already listed; after that you only hear about new places and price drops.
- **Tells you when it breaks.** If a site fails 3 checks in a row for a search, the channel gets a ⚠️ message, and a ✅ when it recovers.
- **Doesn't lose alerts.** A listing only counts as seen once Discord accepts its alert, so a Discord outage just means the alert goes out on the next check.
- **Polite by default.** One request at a time, a few seconds between requests to the same site, and each search is checked about every 15 minutes.

[![CI](https://github.com/DanXSpace/apartment-hunter/actions/workflows/ci.yml/badge.svg)](https://github.com/DanXSpace/apartment-hunter/actions/workflows/ci.yml)

## Engineering highlights

- **9 site adapters behind one interface.** Each Source turns a search's criteria into HTTP requests and parses the results into a common listing model. Countries, currencies and their sites live in one table, so adding a site or country is a new Source plus a row.
- **Layered design.** The Discord bot is a thin layer over a search service that owns the rules (limits, per-server isolation, seeding, site selection), backed by SQLite. A jittered scheduler runs one check at a time.
- **At-least-once alerting.** A listing is marked seen only after Discord accepts its alert, so outages delay alerts instead of dropping them. A search's first run quietly records what's already listed, so you never get a flood of alerts on day one.
- **Failure detection.** Per-site, per-search failure streaks raise a warning in the channel after 3 misses and announce the recovery. The owner gets a direct message when a site fails for every search.
- **354 offline tests.** Network access is disabled in the test suite (pytest-socket); Sources are tested against saved pages. CI runs ruff, mypy and pytest on Linux, macOS and Windows, and checks that the Docker image boots.
- **Measured before shipped.** Each site was kept only after a 4-hour feasibility run (16 rounds) from the target host; sites that failed were dropped and recorded below.
- **Three architectures, each simpler than the last.** v0.1 used Kafka, v0.2 drove a headless browser, and the current version makes plain HTTP requests, small enough for a spare computer or a Raspberry Pi.

**Stack:** Python 3.12, discord.py, curl_cffi, SQLite, Docker, uv, ruff, mypy, pytest, GitHub Actions.

> **Responsible use.** This is a personal project built for my own apartment search. The listing sites' terms of use generally restrict automated access, so the bot is rate-limited by default (one request at a time, a few seconds apart, each search checked about every 15 minutes) and is meant for small-scale personal use. If you run it, check those terms first; you're responsible for how you use it.

## For people using it

1. Get the invite link from whoever runs the bot, and add the bot to your Discord server.
2. In the channel where you want alerts, type `/search new` and give it an area (a city, neighborhood or postcode, like "Camden, London", "Kreuzberg, Berlin" or "60614"). Suggestions show the country, so you can tell Paris, France from Paris, TX. Everything else is optional: price range, bedrooms, bathrooms, home type, pets, which sites to check, and a name.
3. Wait for the "Watching" message, then new listings show up in that channel.

### Countries and sites

A search uses the sites of its area's country:

| Country | Sites (`sources`) | Prices in | Postcodes | Can't filter by |
|---|---|---|---|---|
| United States | Zillow, Craigslist | $ | yes | |
| United Kingdom | Rightmove, Zoopla | £ | yes | pets (Rightmove) |
| Germany | Kleinanzeigen | € | yes | bathrooms |
| France | Bien'ici | € | yes | bathrooms, pets |
| Spain | Fotocasa | € | no | |
| Italy | Idealista (idealista.it) | € | yes | |
| Portugal | Idealista (idealista.pt) | € | yes | pets |
| Netherlands | Pararius | € | no | bathrooms |

- **Sites.** Leave `sources` out and the search checks every site for its country. The option suggests "All sites" plus the sites of the area you typed; a site from another country is refused with the list of ones you can pick. If a site can't search your area, the search is created with the others and the reply says which site was left out and why; if none can, nothing is saved. Fotocasa and Pararius can't search by postcode, and they're the only sites for Spain and the Netherlands, so use a district, neighbourhood or city there. Changing a search's area to another country switches it to that country's sites, unless you name them.
- **Prices** are whole monthly amounts in the country's currency, with no conversion: `max_price: 2000` in London means £2,000 a month. UK rents shown per week are turned into monthly rents (×52/12) so they compare.
- **Bedrooms mean bedrooms everywhere.** Many European sites count rooms, the living room included. There, `min_beds: 2` asks for at least 3 rooms, and a listing's beds are shown as its rooms minus one (a studio is 0).
- **Filters a site can't apply** are passed over, not faked: the "Watching" reply and `/search check` say so ("Pararius can't filter by baths, so you'll see all of its matches.").

| Command | What it does |
|---|---|
| `/search new` | Start a search in this channel |
| `/search list` | Show this server's searches and how they're doing |
| `/search edit` | Change a search. Changing what it looks for starts it fresh, quietly; renaming doesn't |
| `/search pause` / `/search resume` | Stop and restart a search's checks |
| `/search delete` | Delete a search (asks you to confirm) |
| `/search check` | Check a search now (once every 5 minutes per search) |
| `/search help` | How it works, the limits and what each status means |

A server can have 5 searches, and the bot has room for 25 in total (the owner can change both). If the bot is removed from a server, that server's searches are deleted.

**Statuses in `/search list`**

| Status | Meaning |
|---|---|
| Healthy | The last check worked |
| Running | It's being checked right now |
| Paused | Not checked until someone resumes it |
| Needs attention | Something went wrong, like a site not answering or the bot losing access to the channel. The reason is shown next to it |
| Not run yet | Its first check hasn't happened yet |

## For the owner

You run one copy of the bot on one always-on computer (the host). Everyone else just adds it to their server.

### One-time setup

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application.
2. Open **Bot**, turn on **Public Bot** (so friends can add it themselves), and copy the bot token (Reset Token).
3. Copy `.env.example` to `.env` and paste the token as `DISCORD_TOKEN`. Keep it secret: anyone with it can run your bot.
4. Start it:

   ```bash
   docker compose up -d
   docker compose logs -f
   ```

5. The log prints an invite link (scopes `bot applications.commands`; permissions View Channel, Send Messages and Embed Links). Share that link. Slash commands are registered globally, which can take up to an hour to appear; set `DEV_GUILD_ID` to your own test server to see them at once.

The database and log file live in `./data`, so state survives restarts and rebuilds. Only one copy can run against the same data folder; a second one exits with a message saying so.

### Settings

Set these in `.env` (see [`.env.example`](.env.example)). Only `DISCORD_TOKEN` is required.

| Variable | What it does | Default |
|---|---|---|
| `DISCORD_TOKEN` | Your bot's token | required |
| `DATA_DIR` | Folder for the database and logs (the Docker image uses `/app/data`) | `data` |
| `OWNER_USER_ID` | Your Discord user id, to get a direct message when a site fails for every search that uses it (and again when it recovers). You must share at least one server with the bot for Discord to deliver it | none |
| `DEV_GUILD_ID` | A test server id: commands appear there at once instead of globally | none |
| `INTERVAL_MINUTES` | Minutes between checks of each search (±20%), minimum 10 | 15 |
| `MAX_SEARCHES_PER_SERVER` | Most searches one server may have | 5 |
| `MAX_SEARCHES_TOTAL` | Most searches across the whole bot | 25 |
| `ALLOWED_SERVERS` | Server ids separated by commas; the bot leaves any other server it's added to | any server |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` | `INFO` |

Logs also go to a rotating file in the data folder.

### Hosting

The bot only makes outbound connections (Discord, the listing sites and the place lookup), so it needs no open ports. Whether Zillow and Craigslist keep answering a given host is checked with `scripts/feasibility_check.py`; re-run it after any change of host.

Every site was chosen by a feasibility run from the owner's home internet connection: plain requests, no browser, 16 rounds over about 4 hours.

| Site | Result |
|---|---|
| Zillow, Craigslist | Passed (2026-11-19), every request answered |
| Rightmove | Passed (2026-11-19) |
| Zoopla | Passed (2026-11-19), one blocked request that cleared on the next try |
| Pararius | Passed (2026-11-19) |
| Fotocasa | Passed (2026-11-19) |
| Idealista (idealista.it, idealista.pt) | Passed (2026-11-19) |
| Bien'ici | Passed (2026-11-20) |
| Kleinanzeigen | Passed (2026-11-20), all 16 rounds answered |

Not used, because they blocked plain requests in the probes or the 4-hour run: Leboncoin, SeLoger, ImmoScout24, Funda, Idealista Spain and Immowelt (its newest-first filtered search sits behind bot protection, so Germany uses Kleinanzeigen).

A spare computer or a Raspberry Pi at home is the recommended host. Cloud servers were not tested; data-centre addresses are more likely to be blocked, so run the check there before relying on one.

### Place lookup

Areas are looked up with [Photon](https://photon.komoot.io), the OpenStreetMap search service run by komoot. It needs no key, but it's a free service with a fair-use policy, so the bot keeps its use small: suggestions start at 3 characters, answers are cached, and lookups are limited to one every 2 seconds across the whole bot, with a User-Agent naming the app. Only cities, neighborhoods and postcodes in the supported countries are offered. If Photon doesn't answer, new searches and area changes fail with "The place lookup isn't answering right now" while existing searches keep running, since each site's location for an area is worked out once and saved with the search.

## Troubleshooting

**"DISCORD_TOKEN is not set"**
The bot needs its token. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`.

**"… must be a number", "… must be server ids separated by commas" or "… must be one of DEBUG, INFO…"**
A setting in `.env` has a bad value; the message names the variable.

**"Apartment Hunter is already running with the data in …"**
Another copy is using the same data folder. Stop it first, or point `DATA_DIR` somewhere else.

**"I can't post in this channel. Give me the View Channel, Send Messages and Embed Links permissions here…"**
Give the bot those three permissions in the channel, or run the command in another channel. If a search's channel is deleted or the bot loses access later, the search is paused and shows "Needs attention"; fix the permission (or recreate the search in another channel) and use `/search resume`.

**"I couldn't find a place called … in the countries I cover"**
Use a city, neighborhood or postcode in one of the supported countries, like "Chicago, IL", "Camden, London" or "60614". Street addresses and whole states or regions aren't areas.

**"The place lookup isn't answering right now" or "I couldn't look up places right now"**
Photon, or a site's own place lookup, didn't answer. Nothing was saved; try again in a minute.

**"… was left out: …" or "I can't search … on any site"**
A site can't search the area you picked (it doesn't know the place, or it can't search by postcode, like Fotocasa and Pararius). The search uses the country's other sites; when there are none, nothing is saved. Try the neighbourhood, district or city instead.

**"… can't search …. Pick from: …"**
You picked a site from another country. Pick one of the sites listed, or leave `sources` out to use them all.

**"The bot is watching as many searches as it can right now"**
The bot's total limit is reached. Delete a search you don't need, or ask the owner to raise `MAX_SEARCHES_TOTAL`.

**"Zillow is blocking requests from this computer right now" (or another site)**
The site's bot protection turned the host away. This usually clears on its own, and the bot keeps trying. If it lasts for days, run `scripts/feasibility_check.py` on the host (for Zillow and Craigslist); a host that stays blocked may need to move (see Hosting).

**"Zillow's page layout changed, so listings couldn't be read. The bot needs an update." (or another site)**
The site changed its pages and the parser needs updating. Please [open an issue](https://github.com/DanXSpace/apartment-hunter/issues).

**"You can check … again in N minutes"**
`/search check` works once every 5 minutes per search.

**Commands don't show up**
Global commands can take up to an hour on first start. Set `DEV_GUILD_ID` to see them at once in a test server.

## How it works

```
Discord ⇄ Bot (slash commands, posting) ⇄ Search service ⇄ Store (SQLite)
                                               │
                              Scheduler (one Run at a time, jittered)
                                               │
                     SearchRunner → Sources (one per site) → plain HTTP
```

The bot is thin: it turns commands into calls on the search service and results into replies. The service owns the rules (limits, server isolation, seeding, which sites a country uses). An area is resolved once through Photon to a country, then each of that country's Sources works out what it needs to search there (its source location), which is saved with the search, so Runs make no extra lookups. Sources turn a search's criteria into plain HTTP requests (with Chrome's TLS fingerprint, through curl_cffi) and parse the pages into listings. The countries, their currencies and their sites live in one table (`src/apartment_hunter/countries.py`). There is no browser anywhere.

v0.2, the browser-based apartments.com watcher with `config.toml`, lives at the [`v0.2-apartments-com`](https://github.com/DanXSpace/apartment-hunter/tree/v0.2-apartments-com) tag, and the Kafka-based v0.1 at [`v0.1-kafka`](https://github.com/DanXSpace/apartment-hunter/tree/v0.1-kafka).

## Development

```bash
uv sync
uv run ruff check
uv run mypy
uv run pytest
```

Tests never touch a listing site, Photon or Discord. The Sources are tested against saved pages in `tests/fixtures/`; if a site changes its layout, save a fresh results page there and update the tests. To check whether a host can reach Zillow and Craigslist, run `uv run python scripts/feasibility_check.py`.

## License

[MIT](LICENSE)

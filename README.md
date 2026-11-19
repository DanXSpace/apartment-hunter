# Apartment Hunter

A Discord bot that watches Zillow and Craigslist for rentals. Add it to your server, tell it what you're looking for with `/search`, and new listings and price drops are posted in your channel. No browser, nothing to install if you're just using it.

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

> **Personal use only.** This bot reads Zillow and Craigslist search results automatically. Zillow's terms of use and Craigslist's terms of use forbid automated access. Check them before you run it, keep the polite defaults, and use it for your own apartment search and your friends'. Don't promote it publicly. You are responsible for how you use it.

## For people using it

1. Get the invite link from whoever runs the bot, and add the bot to your Discord server.
2. In the channel where you want alerts, type `/search new` and give it an area (a city, neighborhood or ZIP code). Everything else is optional: price range, bedrooms, bathrooms, home type, pets, which sites to check, and a name.
3. Wait for the "Watching" message, then new listings show up in that channel.

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

The bot only makes outbound connections (Discord, Zillow, Craigslist), so it needs no open ports. Whether Zillow and Craigslist keep answering a given host is checked with `scripts/feasibility_check.py`; re-run it after any change of host.

<!-- HOSTING: filled in after the home feasibility check -->

## Troubleshooting

**"DISCORD_TOKEN is not set"**
The bot needs its token. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`.

**"… must be a number", "… must be server ids separated by commas" or "… must be one of DEBUG, INFO…"**
A setting in `.env` has a bad value; the message names the variable.

**"Apartment Hunter is already running with the data in …"**
Another copy is using the same data folder. Stop it first, or point `DATA_DIR` somewhere else.

**"I can't post in this channel. Give me the View Channel, Send Messages and Embed Links permissions here…"**
Give the bot those three permissions in the channel, or run the command in another channel. If a search's channel is deleted or the bot loses access later, the search is paused and shows "Needs attention"; fix the permission (or recreate the search in another channel) and use `/search resume`.

**"I couldn't find a place called …"**
Use a city, neighborhood or ZIP code, like "Chicago, IL" or "60614". Street addresses and whole states aren't areas.

**"Zillow's place lookup isn't answering right now" or "I couldn't look up places right now"**
Zillow's place lookup failed. Nothing was saved; try again in a minute.

**"The bot is watching as many searches as it can right now"**
The bot's total limit is reached. Delete a search you don't need, or ask the owner to raise `MAX_SEARCHES_TOTAL`.

**"Zillow is blocking requests from this computer right now" (or Craigslist)**
The site's bot protection turned the host away. This usually clears on its own, and the bot keeps trying. If it lasts for days, run `scripts/feasibility_check.py` on the host; a host that stays blocked may need to move (see Hosting).

**"Zillow's page layout changed, so listings couldn't be read. The bot needs an update." (or Craigslist)**
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
                     SearchRunner → Sources (Zillow, Craigslist) → plain HTTP
```

The bot is thin: it turns commands into calls on the search service and results into replies. The service owns the rules (limits, server isolation, seeding). Sources turn a search's criteria into plain HTTP requests (with Chrome's TLS fingerprint, through curl_cffi) and parse the pages into listings. There is no browser anywhere.

v0.2, the browser-based apartments.com watcher with `config.toml`, lives at the [`v0.2-apartments-com`](https://github.com/DanXSpace/apartment-hunter/tree/v0.2-apartments-com) tag, and the Kafka-based v0.1 at [`v0.1-kafka`](https://github.com/DanXSpace/apartment-hunter/tree/v0.1-kafka).

## Development

```bash
uv sync
uv run ruff check
uv run mypy
uv run pytest
```

Tests never touch Zillow, Craigslist or Discord. The Sources are tested against saved pages in `tests/fixtures/`; if a site changes its layout, save a fresh results page there and update the tests. To check whether a host can reach both sites, run `uv run python scripts/feasibility_check.py`.

## License

[MIT](LICENSE)

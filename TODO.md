# Apartment Hunter — TODO

## v1.0: Discord bot (done)

A Discord bot replacing the browser-based watcher (v0.2 is kept at tag `v0.2-apartments-com`, v0.1 at `v0.1-kafka`):
`/search` commands, Zillow and Craigslist Sources over plain HTTP, SQLite state for many servers,
seeding runs, price-drop and per-Source health alerts, owner alerts, a Docker image without a browser, CI.

## Europe (spec #34)

The UK, Germany, France, Spain, Italy, Portugal and the Netherlands, with Photon as the place lookup,
per-country Sources and currencies. Open: Kleinanzeigen's 4-hour feasibility run (see the README's Hosting section).

## Ideas (not scheduled)

- Deeper periodic sweeps, so price drops are caught on broad searches (a regular Run reads only the newest page).
- Paste a Zillow search URL to watch a drawn map area.
- Commute times from a work address.
- More Sources (HotPads, Trulia and Redfin answered plain requests in the original probe).
- Merge the same place listed on both Zillow and Craigslist into one Alert.
- More notification channels than Discord.

## Maintenance

- Refresh a Source's fixtures in `tests/fixtures/` when its parsing breaks.
- Re-run `scripts/feasibility_check.py` (Zillow and Craigslist) after any change of host.

## Known limitations

- The listing sites' terms generally forbid automated access; the bot is for personal use and stays polite.
- Fotocasa and Pararius can't search by postcode.
- A regular Run reads only the newest page of each Source, so price drops on very broad searches are caught only among the newest listings.
- Craigslist alerts have no photo, and its results show no bedroom count.

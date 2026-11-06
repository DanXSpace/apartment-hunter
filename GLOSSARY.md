# Apartment Hunter

Watches apartments.com searches on someone's own machine and sends Discord alerts when new listings appear or prices drop.

## Watching

**Search**:
A named apartments.com search-results URL, watched on its own interval and alerting to one webhook. Its name is its identity: renaming it starts it fresh.
_Avoid_: query, watch, filter

**Listing**:
One result card on a search's results pages: a building or unit with its price, beds and baths ranges.
_Avoid_: apartment, property, unit (for the card as a whole)

**Run**:
One pass over a search: fetch every results page up to the page cap and compare the listings with what was seen before.
_Avoid_: scrape, job, check

**Seeding run**:
A search's first run, which records the current listings without alerting on them, so only listings that appear later are alerted.
_Avoid_: first scrape, baseline, initial import

**Page cap**:
The most results pages one run reads; hitting it means the search is too broad.

## Alerts

**Alert**:
A message sent to a search's webhook about one listing, either a **new listing** or a **price drop**.
_Avoid_: notification, ping

**Health alert**:
A message sent to the webhook when a search has failed several runs in a row (it is **broken**), and again when it recovers.
_Avoid_: error alert

**Blocked**:
A run that apartments.com's bot protection rejected; usually temporary.
_Avoid_: banned, captcha

**Webhook**:
The Discord channel address a search's alerts are sent to; each search uses the default webhook unless it names its own.

## Running

**Data directory**:
The folder holding one installation's config, database, browser profile and logs. Only one copy of Apartment Hunter runs against a data directory at a time.
_Avoid_: workspace, profile

**App data**:
The per-user data directory in the operating system's standard location, used by the desktop app and by the CLI's `--app-data` mode.

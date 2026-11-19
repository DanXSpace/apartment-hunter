# Apartment Hunter

A Discord bot that watches Zillow and Craigslist for rentals and posts new listings and price drops in the channel where a search was started.

## Watching

**Search**:
Criteria plus the channel its Alerts go to, identified by an id and belonging to one Server. Renaming it doesn't start it fresh; changing its Criteria or Sources does.
_Avoid_: query, watch, filter

**Criteria**:
What a Search looks for: Area, price range, beds, baths, home type, pets.

**Area**:
Where a Search looks: a city, neighborhood or ZIP code, resolved once to a Zillow region and the nearest Craigslist site.
_Avoid_: location, region (except Zillow's own region)

**Source**:
A listing site the bot reads (Zillow, Craigslist).
_Avoid_: site, scraper

**Listing**:
One result card from a Source: a building or unit with its price, beds and baths ranges.
_Avoid_: apartment, property, unit (for the card as a whole)

**Run**:
One pass over a Search: read the newest page of each of its Sources and compare the listings with what was seen before.
_Avoid_: scrape, job, check

**Seeding run**:
A Search's first Run (or the first after its Criteria or Sources change), which records the current listings without alerting on them, so only listings that appear later are alerted.
_Avoid_: first scrape, baseline, initial import

## Alerts

**Alert**:
A message posted in a Search's channel about one listing, either a **new listing** or a **price drop**.
_Avoid_: notification, ping

**Health alert**:
A message posted in the channel when a Source has failed several Runs in a row for a Search (it is **broken**), and again when it recovers. Counted per Source within a Search.
_Avoid_: error alert

**Owner alert**:
The direct message the Owner gets when a Source has failed in every Search that uses it, and again when it recovers in a Search that has run it.

**Blocked**:
A request a Source's bot protection rejected; usually temporary.
_Avoid_: banned, captcha

## Running

**Owner**:
The person who runs the bot and its host.

**Host**:
The one machine the bot runs on.

**Server**:
A Discord server the bot has been added to. Every Search belongs to one.

**Needs attention**:
A Search's status when its latest Run failed for a Source, or when it was paused because the bot can no longer post in its channel. It shows after one failure, before a Source is **broken** and a Health alert goes out.

**Data directory**:
The folder holding the bot's database and logs (`DATA_DIR`, `/app/data` in Docker). Only one copy of the bot runs against a data directory at a time.
_Avoid_: workspace, profile

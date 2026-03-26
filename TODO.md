# Apartment Hunter — TODO

## Vision
A CLI tool where the user pastes an apartments.com URL (with all filters pre-applied)
and the system handles everything else: scraping on a schedule, deduplicating listings,
and sending Discord alerts automatically.

```
uv run python main.py "https://www.apartments.com/..."
```

---

## In Progress / Next Up

- [ ] **Pagination** — scraper only fetches page 1
  - apartments.com uses path-based pagination: `.../2/`, `.../3/`
  - Loop until a page returns no cards

---

## Scraper Improvements

- [ ] **Scrape actual baths** — currently hardcoded to `1.0`
- [ ] **Handle multi-unit listings** — some cards represent a building with a
      price range, not a single unit. Decide whether to skip or handle them.

---

## Stretch Goals

- [ ] **Commute distance** — calculate real distance from a work address
  - Accept work address as a second CLI arg or in `.env`
  - Use Nominatim (free) or Google Maps API to calculate miles
  - Re-enable commute filter in consumer

- [ ] **Notification cooldown** — even with dedup, add a "don't re-alert
      on this listing for X days" window

---

## Done

- [x] Discord embed formatting (image, price, beds, baths, pets, commute, timestamp)
- [x] Image URL scraping
- [x] 15-minute schedule via `schedule`
- [x] Fix missing `scraped_at` field
- [x] Per-card error handling in scraper
- [x] README with architecture diagram
- [x] `.env.example`

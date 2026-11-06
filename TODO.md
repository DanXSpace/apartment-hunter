# Apartment Hunter — TODO

## Next: v0.3 desktop app

Tray icon + local web UI so non-technical users can install and use it without a terminal.
Full spec: https://github.com/DanXSpace/apartment-hunter/issues/1

## Ideas (not scheduled)

- Commute distance from a work address (Nominatim or Google Maps).
- Other notification channels (ntfy, email, Telegram) behind the Notifier interface.
- Other listing sites behind the Scraper interface.
- Refresh the parser fixture from time to time so tests track the live page layout.

## Known limitations

- apartments.com blocks headless and automation-driven browsers. Only `chromium` mode
  (browser opens the URL itself, page is read afterwards) gets through; Docker runs it in a
  virtual display.
- Searches with more than 10 result pages (~400 listings) are truncated; narrow the filters.

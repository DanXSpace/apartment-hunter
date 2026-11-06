# Apartment Hunter — TODO

## v0.2 — done

Single-process watcher replacing the Kafka pipeline (Kafka version kept at tag `v0.1-kafka`):
native-launch Chromium fetching, SQLite state, seeding, price-drop and health alerts,
`config.toml` with multiple searches, Docker (Xvfb), CI, README, MIT license.

### Still to verify

- [ ] Long-running scheduled mode (`apartment-hunter` without `--once`) over a few hours.
- [ ] `docker compose up` with real Docker (so far only tested with `podman build` / `podman run`).
- [ ] CI green on GitHub (test job and Docker build job).

## v0.3 — desktop app (next)

Tray icon + local web UI so non-technical users can install and use it without a terminal.
Full spec: https://github.com/DanXSpace/apartment-hunter/issues/1

### Early risks: check these first

- [ ] Do Chrome / Edge / Brave on **Windows** and **macOS** get past apartments.com's bot
      protection with the native-launch method? (Only tested on Linux with Chromium.)
- [ ] Off-screen / minimised browser window on Windows, macOS, Linux X11 and Linux Wayland.
- [ ] Tray icon availability (pystray) on each OS, including the no-tray fallback on GNOME.

### Milestones

- [ ] Background scheduler service (start/stop, pause per search, run now); CLI moved onto it.
- [ ] Store additions: paused searches, recent alerts.
- [ ] App-data directory (platformdirs), single-instance lock shared with the CLI, log file.
- [ ] Web UI (FastAPI + Jinja + htmx): setup wizard, dashboard, search add/edit, settings.
- [ ] Plain-language error messages and "Show details / Copy log".
- [ ] Browser detection in standard install locations on each OS.
- [ ] Tray icon (thin shell over the web UI actions) and no-tray fallback.
- [ ] Start at login (Windows / macOS / Linux autostart).
- [ ] Update-available banner (GitHub Releases check).
- [ ] PyInstaller builds in CI on tags: Windows `.exe`, macOS `.dmg`, Linux AppImage, each with a
      `--self-test` smoke run.
- [ ] README: download-and-run instructions, SmartScreen / Gatekeeper "open anyway" screenshots.

## Ideas (not scheduled)

- Commute distance from a work address (Nominatim or Google Maps).
- More notification channels (ntfy, email, Telegram) behind the Notifier interface.
- More listing sites behind the Scraper interface.
- Code signing for Windows and macOS builds, if people actually use the app.

## Maintenance

- Refresh the parser fixture (`tests/fixtures/`) when apartments.com changes its layout.
- Re-test Scrapling's stealth mode on new Scrapling releases. It's currently blocked by
  apartments.com but kept as an experimental option.

## Known limitations

- apartments.com blocks headless and automation-driven browsers. Only `chromium` mode
  (browser opens the URL itself, page is read afterwards) gets through.
- Searches with more than 10 result pages (~400 listings) are truncated; narrow the filters.

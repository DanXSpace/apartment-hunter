---
name: run-app
description: Launch apartment-hunter-app offline against a throwaway data directory. Use to smoke-test the desktop app or its web UI in a real process.
---

# Run the desktop app offline

The app scrapes apartments.com, posts to Discord and opens the user's browser. This recipe keeps all three on the machine.

1. Make an empty scratch directory `$S` (the session scratchpad, or `mktemp -d`). Write `$S/share/apartment-hunter/config.toml`:

   ```toml
   webhook_url = "https://127.0.0.1:9/offline"  # passes the https:// check, fails at once locally

   [[search]]
   name = "Downtown"
   url = "https://www.apartments.com/downtown/"
   ```

2. Launch it in the background:

   ```bash
   XDG_DATA_HOME=$S/share BROWSER=true BROWSER_EXECUTABLE=/nonexistent \
     timeout 8 .venv/bin/apartment-hunter-app > $S/app.log 2>&1 &
   ```

   - `BROWSER=true`: `webbrowser.open` runs `true` instead of opening a tab.
   - `BROWSER_EXECUTABLE=/nonexistent`: every run fails fast with "browser not found", so the search shows **Needs attention**.
   - `timeout` sends SIGTERM; the app shuts down cleanly and removes its port file.

   `XDG_DATA_HOME` redirects the app-data directory on Linux only. On macOS set `HOME=$S` (data lands in `$S/Library/Application Support/apartment-hunter`); on Windows set `WIN_PD_OVERRIDE_LOCAL_APPDATA` (see `app_data_root` in `tests/conftest.py`).

3. Done when `$S/share/apartment-hunter/port` exists. Query `http://127.0.0.1:$(cat $S/share/apartment-hunter/port)/status` (or `/`) with `curl`, then read `$S/app.log`.

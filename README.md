# Wyndham → Airbnb availability sync

Publishes one calendar feed (`.ics`) per Airbnb listing. Each feed **blocks every
night Club Wyndham can't supply that suite type**, so Airbnb only lets guests
request nights you could actually book with points.

It runs **on your Mac** (Club Wyndham's sign-in only trusts a device you've
verified with a texted code) on a launchd schedule, and publishes the feeds to
GitHub Pages, where Airbnb's "Import calendar" reads them.

```
Club Wyndham owner API ──(headless Chrome, persistent profile on your Mac)──▶ sync.py
      per-night units per suite label            │  config.yaml: listing → suite labels
                                                 │  blocks.yaml: manual blocks
                                                 ▼
               gh-pages branch: <FEED_TOKEN>/<listing>.ics ──▶ GitHub Pages ──▶ Airbnb import
```

## How it decides

For each listing and each night from today to 2 years out:

* **Open** if Wyndham shows ≥ `min_available` (default 1) units across the
  listing's labels — e.g. Midtown 45 2BR Presidential counts both
  *2 Bedroom Presidential* and *2 Bedroom Presidential (Presidential Reserve)*.
* **Blocked** otherwise, past Wyndham's ~15‑month booking window, or inside a
  `blocks.yaml` entry.

A run **publishes nothing** (the last good feeds stay live) if a resort returns
no data, nights in the next two months are missing, or a listing's suite labels
disappear from Wyndham's data (a renamed suite type would otherwise silently
block a listing forever).

Not carried over: Wyndham check-in-day and minimum-stay rules (iCal can only say
open/blocked). Because you accept by request, check those when you book.

## Setup (once, on your Mac)

Keep the repo somewhere outside Desktop/Documents/Downloads (e.g. `~/Projects`),
so the scheduled job doesn't hit macOS privacy prompts. Needs `gh` signed in.

```bash
cd ~/Projects/wyndham-airbnb-sync
git pull
./setup_mac.sh      # venv, Keychain (asks Wyndham username/password), push auth, schedule
./run_sync.sh       # first run: if asked, type the texted code; it ticks "don't ask again"
```

`setup_mac.sh` prints the 12 Airbnb import URLs (`./setup_mac.sh --urls` any time).
In GitHub: **Settings → Pages → Source: Deploy from a branch → `gh-pages` / root**.
Then, per listing in Airbnb: **Calendar → Availability → Connect calendars →
Connect another website**, paste its URL.

Credentials and the random feed-folder token live in your login Keychain under
`wyndham-airbnb-sync`; nothing secret is in the repo.

## Day to day

* Runs at 1:23, 7:23, 13:23, 19:23. A time missed while the Mac sleeps runs on wake.
* **Failures** show a macOS notification; details in
  `~/Library/Logs/wyndham-airbnb-sync.log` (plus a screenshot in `debug/`).
* **"asked for a verification code"**: run `./run_sync.sh` in Terminal and enter the code.
* **Holiday / guest-restricted dates**: add them to `blocks.yaml`.
* **New listing** (e.g. bigger Avon suites): add it to `config.yaml` using a label
  from the comment at the top, then connect its feed URL in Airbnb.
* **Changed Wyndham password**: `./setup_mac.sh --reset`.
* **Stop the schedule**: `./setup_mac.sh --remove`.

## Testing without Wyndham

```bash
.venv/bin/python sync.py --from-json tests/fixture_2026-11-12.json --today 2026-11-13 --out /tmp/feeds
.venv/bin/python -m unittest discover tests
```

## Notes

* `avon-studio-presidential` is always fully blocked: Avon has no studio inventory.
* Some hosts report that when a night disappears from an imported feed, Airbnb
  keeps it blocked (unattributed) instead of reopening it. If you see nights that
  Wyndham shows open but Airbnb still blocks, clear them by hand in Airbnb.
* This automates reading your own owner account through the same API the owner
  website uses. Review Club Wyndham's Terms of Use and keep the schedule modest.

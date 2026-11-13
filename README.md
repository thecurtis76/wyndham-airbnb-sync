# Wyndham → Airbnb availability sync

Publishes one calendar feed (`.ics`) per Airbnb listing. Each feed **blocks every
night Club Wyndham can't supply that suite type**, so Airbnb only lets guests
request nights you could actually book with points. A GitHub Actions job
refreshes the feeds 4× a day and hosts them on GitHub Pages; Airbnb's
"Import calendar" pulls from those URLs.

```
Club Wyndham owner API ──(headless Chromium, your saved session)──▶ sync.py
      per-night units per suite label            │  config.yaml: listing → suite labels
                                                 │  blocks.yaml: manual blocks
                                                 ▼
                          public/<FEED_TOKEN>/<listing>.ics  ──▶  GitHub Pages  ──▶  Airbnb import
```

## How it decides

For each listing and each night from today to 2 years out:

* **Open** if Wyndham shows ≥ `min_available` (default 1) units across the
  listing's labels — e.g. Midtown 45 2BR Presidential counts both
  *2 Bedroom Presidential* and *2 Bedroom Presidential (Presidential Reserve)*.
* **Blocked** otherwise, past Wyndham's ~15‑month booking window, or inside a
  `blocks.yaml` entry.

The job **refuses to publish** (and the last good feeds stay live) if a resort
returns no data, nights in the next two months are missing, or a listing's
suite labels disappear from Wyndham's data (a renamed suite type would otherwise
silently block a listing forever).

Not carried over: Wyndham check-in-day and minimum-stay rules (iCal can only say
open/blocked). Because you accept by request, check those when you book.

## One-time setup

1. **Create a GitHub repo** with these files (private is fine on GitHub Pro;
   a public repo exposes only code — never your credentials or session).
2. **Enable Pages:** Settings → Pages → Source: *GitHub Actions*.
3. **Save your Wyndham session** (on your Mac):
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt && python -m playwright install chromium
   python bootstrap_login.py          # sign in, enter the texted code, tick "don't ask again"
   base64 -i state.json | gh secret set WYNDHAM_STATE
   ```
4. **Add the other secrets:**
   ```bash
   gh secret set WYNDHAM_USERNAME
   gh secret set WYNDHAM_PASSWORD
   gh secret set FEED_TOKEN --body "$(openssl rand -hex 12)"
   ```
   `FEED_TOKEN` is a random folder name so the feed URLs can't be guessed.
5. **Run it once:** Actions → *Sync Wyndham availability…* → *Run workflow*.
   The log lists open-night counts per listing.
6. **Connect each Airbnb listing:** Airbnb → Calendar → listing → Availability →
   *Connect calendars* → *Connect another website*, paste
   `https://<github-user>.github.io/<repo>/<FEED_TOKEN>/<slug>.ics`
   (slugs are in `config.yaml`).

## Day to day

* **Holiday / guest-restricted dates:** add them to `blocks.yaml` and push.
* **New listing (e.g. bigger Avon suites):** add an entry to `config.yaml` using a
  label from the comment at the top of that file, then connect its feed in Airbnb.
* **"asked for a verification code" failure:** GitHub emails you when a scheduled
  run fails. Re-run step 3 on your Mac to refresh the session.

### Fallback: run on your Mac instead

If Wyndham keeps challenging logins from GitHub's servers, run the same script
on a schedule locally and push only the feeds:

```bash
python sync.py --out public   # uses state.json in this folder
```

(then publish `public/` however you like — or ask Claude to set up a launchd job).

## Testing without Wyndham

```bash
python sync.py --from-json tests/fixture_2026-11-12.json --today 2026-11-13 --out /tmp/feeds
python -m unittest discover tests
```

## Notes

* `avon-studio-presidential` is always fully blocked: Avon has no studio inventory.
* Some hosts report that when a night disappears from an imported feed, Airbnb
  keeps it blocked (unattributed) instead of reopening it. If you see nights that
  Wyndham shows open but Airbnb still blocks, clear them by hand in Airbnb.
* This automates reading your own owner account through the same API the owner
  website uses. Review Club Wyndham's Terms of Use and keep the schedule modest.

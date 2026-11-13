"""Fetch per-night suite availability from the Club Wyndham owner site.

How it works (mirrors what the owner website itself does):
  1. Open a real (headless) Chromium with a saved browser session (cookies +
     local storage), so Okta's "don't ask again on this device" is honored.
  2. If the session has expired, sign in with username/password. If Okta
     then demands a texted verification code, stop with MfaRequired - you
     re-run bootstrap_login.py on your Mac to refresh the saved session.
  3. Load a resort's monthly-calendar page and capture the auth headers the
     page attaches to its own availability request.
  4. From inside the page, call the same API the site uses:
     POST resort-operations/v3/resorts/calendar/availability
     in ~30-day windows, summing availableCount per suite label per night.
"""
from __future__ import annotations

import datetime as dt
import json
import time

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

SITE = "https://clubwyndham.wyndhamdestinations.com"
LOGIN_URL = f"{SITE}/us/en/login"
CALENDAR_URL = SITE + "/us/en/owner/resort-monthly-calendar?productId={pid}"
API = "https://api.wvc.wyndhamdestinations.com/resort-operations/v3/resorts/calendar/availability"
WINDOW_DAYS = 30            # API rejects ranges much longer than a month
PAUSE_SECONDS = 0.5         # be gentle with Wyndham's API


class WyndhamError(Exception):
    pass


class MfaRequired(WyndhamError):
    pass


_FETCH_JS = """
async ({url, headers, body}) => {
  headers = {...headers, 'x-request-timestamp': new Date().toISOString()};
  const r = await fetch(url, {method: 'POST', headers, body: JSON.stringify(body)});
  return {status: r.status, text: await r.text()};
}
"""

# Headers the browser adds itself; never replay these from fetch().
_SKIP = {"host", "content-length", "cookie", "origin", "referer", "user-agent",
         "connection", "accept-encoding", "accept-language"}


def _windows(start: dt.date, end: dt.date):
    s = start
    while s <= end:
        e = min(s + dt.timedelta(days=WINDOW_DAYS - 1), end)
        yield s, e
        s = e + dt.timedelta(days=1)


class WyndhamClient:
    def __init__(self, state_path: str, username: str | None, password: str | None,
                 consumer_type: str, headless: bool = True):
        self.state_path = state_path
        self.username, self.password = username, password
        self.consumer_type = consumer_type
        self.headless = headless
        self._headers: dict[str, str] | None = None
        self._party_id: str | None = None

    # -- session ---------------------------------------------------------
    def _on_login_page(self, page: Page) -> bool:
        return "/login" in page.url or page.locator("input[name=password]").count() > 0

    def _login(self, page: Page):
        if not (self.username and self.password):
            raise WyndhamError("Session expired and WYNDHAM_USERNAME / WYNDHAM_PASSWORD are not set.")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.locator("input[name=username]").fill(self.username)
        page.locator("input[name=password]").fill(self.password)
        page.locator("input[name=password]").press("Enter")
        try:
            page.wait_for_url("**/owner/**", timeout=45_000)
        except PWTimeout:
            body = page.inner_text("body").lower()
            if any(w in body for w in ("verification", "verify", "code", "sms", "text message")):
                raise MfaRequired(
                    "Club Wyndham asked for a verification code. Run bootstrap_login.py on your Mac "
                    "to sign in once (check 'don't ask again'), then update the WYNDHAM_STATE secret."
                )
            raise WyndhamError(f"Login did not reach the owner area (stuck at {page.url}).")

    def _capture_headers(self, page: Page, product_id: str):
        """Load a calendar page and grab the auth headers from the site's own API call."""
        with page.expect_request(lambda r: "/resorts/availability-dates" in r.url and r.method == "POST",
                                 timeout=60_000) as req_info:
            page.goto(CALENDAR_URL.format(pid=product_id), wait_until="domcontentloaded")
        req = req_info.value
        # all_headers() includes auth headers that .headers may omit.
        self._headers = {k: v for k, v in req.all_headers().items()
                         if k.lower() not in _SKIP and not k.startswith(":")}
        try:
            self._party_id = str(json.loads(req.post_data or "{}").get("partyId") or "")
        except json.JSONDecodeError:
            self._party_id = ""
        if not self._party_id or not any(k.lower() == "authorization" for k in self._headers):
            raise WyndhamError("Could not capture API credentials from the calendar page.")

    # -- data ------------------------------------------------------------
    def _fetch_window(self, page: Page, product_id: str, s: dt.date, e: dt.date) -> list[dict]:
        body = {"productId": product_id, "startDate": s.isoformat(), "endDate": e.isoformat(),
                "partyId": self._party_id, "consumerTypes": [self.consumer_type]}
        last = None
        for attempt in range(3):
            res = page.evaluate(_FETCH_JS, {"url": API, "headers": self._headers, "body": body})
            if res["status"] == 200:
                data = json.loads(res["text"])
                return data.get("calendarDays") or []
            last = f"HTTP {res['status']}: {res['text'][:300]}"
            if res["status"] in (401, 403):
                raise WyndhamError(f"Wyndham rejected the session ({last}).")
            time.sleep(2 * (attempt + 1))
        raise WyndhamError(f"{product_id} {s}..{e} failed: {last}")

    def fetch(self, resorts: dict[str, str], start: dt.date, end: dt.date) -> dict[str, dict[str, dict[str, int]]]:
        """resorts: {key: product_id}. Returns {key: {date: {label: units}}}."""
        out: dict[str, dict[str, dict[str, int]]] = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            try:
                ctx = browser.new_context(storage_state=self.state_path)
            except FileNotFoundError:
                ctx = browser.new_context()
            page = ctx.new_page()
            first_pid = next(iter(resorts.values()))
            page.goto(CALENDAR_URL.format(pid=first_pid), wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=60_000)
            if self._on_login_page(page):
                self._login(page)
            self._capture_headers(page, first_pid)

            for key, pid in resorts.items():
                nights: dict[str, dict[str, int]] = {}
                for s, e in _windows(start, end):
                    for day in self._fetch_window(page, pid, s, e):
                        counts = nights.setdefault(day["date"], {})
                        for o in day.get("inventoryOfferings") or []:
                            lbl = o.get("invenOffrngLabel") or ""
                            counts[lbl] = counts.get(lbl, 0) + int(o.get("availableCount") or 0)
                    time.sleep(PAUSE_SECONDS)
                out[key] = nights

            ctx.storage_state(path=self.state_path)   # keep the refreshed session for next run
            browser.close()
        return out

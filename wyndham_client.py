"""Fetch per-night suite availability from the Club Wyndham owner site.

How it works (mirrors what the owner website itself does):
  1. Open full Chromium (headless) with a persistent profile, so Okta's
     "don't ask again on this device" survives between runs.
  2. Sign in at the login page every run (owner sessions last ~1 hour).
     If Okta asks for a texted code and we're in Terminal, send it, prompt
     for it, and tick "don't ask again"; on a schedule, stop with MfaRequired.
  3. Load a resort's monthly-calendar page and capture the auth headers the
     page attaches to its own availability request.
  4. From inside the page, call the same API the site uses:
     POST resort-operations/v3/resorts/calendar/availability
     in ~30-day windows, summing availableCount per suite label per night.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time

import pathlib

from playwright.sync_api import Error as PWError, Page, sync_playwright

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

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")

def _chrome_major(p) -> str:
    """Chrome major version of Playwright's bundled browser (for a matching user agent)."""
    b = p.chromium.launch(headless=True, channel="chromium")
    try:
        return b.version.split(".")[0]
    finally:
        b.close()


# Signs that Okta is asking for a second factor (texted code).
_MFA_SELECTORS = ['input[name="credentials.passcode"]', 'input[name="answer"]', 'input[name="passCode"]',
                  '[data-se="factor-sms"]', '[data-se="phone_number"]', '[data-se="okta_verify-push"]']
_MFA_PHRASES = ("verification code", "verify with", "enter code", "send code", "receive a code",
                "we sent a code", "security method")

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
    def __init__(self, profile_dir: str, username: str | None, password: str | None,
                 consumer_type: str, headless: bool = True, interactive: bool = False):
        self.profile_dir = profile_dir      # persistent browser profile (keeps Okta's device trust)
        self.username, self.password = username, password
        self.consumer_type = consumer_type
        self.headless = headless
        self.interactive = interactive      # may prompt for a texted code (first run in Terminal)
        self._headers: dict[str, str] | None = None
        self._party_id: str | None = None
        self._log: list[str] = []

    # -- session ---------------------------------------------------------
    def _state(self, page: Page) -> str:
        """Classify the current page: calendar | login | mfa | error | loading."""
        if "/login" not in page.url and page.locator("#suiteType").count() > 0:
            return "calendar"
        if page.locator(", ".join(_MFA_SELECTORS)).count() > 0:
            return "mfa"
        try:
            body = page.inner_text("body", timeout=2_000).lower()
        except PWError:
            body = ""
        if any(p in body for p in _MFA_PHRASES):
            return "mfa"
        if page.locator(".o-form-error-container:not(:empty), .okta-form-infobox-error").count() > 0:
            return "error"
        if page.locator("input[name=password]").count() > 0:
            return "login"
        if "log out" in body:
            return "signed_in"
        return "loading"

    def _wait_state(self, page: Page, want: set[str], timeout_s: int) -> str:
        deadline = time.time() + timeout_s
        state = "loading"
        while time.time() < deadline:
            state = self._state(page)
            if state in want:
                return state
            page.wait_for_timeout(1_000)
        return state

    def _login(self, page: Page):
        if not (self.username and self.password):
            raise WyndhamError("Session expired and WYNDHAM_USERNAME / WYNDHAM_PASSWORD are not set.")
        print("Signing in to Club Wyndham...")
        page.locator("input[name=username]").fill(self.username)
        page.locator("input[name=password]").fill(self.password)
        page.locator("input[name=password]").press("Enter")
        # Leave the login form, then land somewhere definite.
        page.wait_for_timeout(3_000)
        state = self._wait_state(page, {"mfa", "error", "calendar", "signed_in"}, 60)
        if state == "mfa":
            self._complete_mfa(page)
            state = self._wait_state(page, {"error", "calendar", "signed_in"}, 90)
        if state == "error":
            msg = page.locator(".o-form-error-container, .okta-form-infobox-error").first.inner_text()
            raise WyndhamError(f"Sign-in was rejected: {msg.strip()[:200]}")
        if state not in ("calendar", "signed_in"):
            raise WyndhamError(f"Sign-in did not complete (at {page.url}, state={state}).")
        print("Signed in.")

    def _complete_mfa(self, page: Page):
        """Okta wants a texted code. In Terminal: send it, ask for it, trust this device."""
        if not self.interactive:
            raise MfaRequired(
                "Club Wyndham asked for a verification code. Run ./run_sync.sh once in Terminal; it will "
                "text you a code, ask you to type it, and remember this device for scheduled runs."
            )
        send = page.get_by_role("button", name=re.compile(r"send code", re.I)).or_(
            page.get_by_role("link", name=re.compile(r"send code", re.I))).or_(
            page.locator('input[type=submit][value*="code" i]'))
        if send.count():
            send.first.click()
            print("Club Wyndham is texting you a verification code.")
        box = page.locator(", ".join([
            'input[name="credentials.passcode"]', 'input[name="answer"]', 'input[name="passCode"]',
            'form.o-form input[type=text]:visible', 'form.o-form input[type=tel]:visible',
            'form.o-form input[type=number]:visible'])).first
        box.wait_for(state="visible", timeout=60_000)
        code = input("Enter the code from the text message: ").strip()
        box.fill(code)
        # Tick "don't ask again on this device" (or similar) if offered.
        for cb in page.locator("form.o-form input[type=checkbox]").all():
            try:
                cb.check(force=True)
            except PWError:
                pass
        verify = page.get_by_role("button", name=re.compile(r"verify|submit|continue|sign in", re.I)).or_(
            page.locator('form.o-form input[type=submit]'))
        if verify.count():
            verify.first.click()
        else:
            box.press("Enter")
        page.wait_for_timeout(3_000)

    def _open_calendar(self, page: Page, product_id: str):
        # Sessions last about an hour and signed-out owner pages don't always
        # redirect, so start every run at the login page.
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        state = self._wait_state(page, {"login", "signed_in", "mfa", "calendar"}, 60)
        if state == "login":
            self._login(page)
        elif state == "mfa":
            self._complete_mfa(page)
            state = self._wait_state(page, {"signed_in", "calendar"}, 90)
        elif state not in ("signed_in", "calendar"):
            raise WyndhamError(f"Login page did not load (at {page.url}, state={state}).")
        page.goto(CALENDAR_URL.format(pid=product_id), wait_until="domcontentloaded")
        state = self._wait_state(page, {"calendar", "login", "mfa"}, 60)
        if state != "calendar":
            raise WyndhamError(f"Calendar page did not load after sign-in (at {page.url}, state={state}).")

    def _capture_headers(self, page: Page, product_id: str):
        """Get the auth headers the calendar page attaches to its own availability request."""
        captured: list = []
        page.on("request", lambda r: captured.append(r) if (
            "/resorts/availability-dates" in r.url and r.method == "POST") else None)
        self._open_calendar(page, product_id)
        for _ in range(15):
            if captured:
                break
            page.wait_for_timeout(1_000)
        if not captured:
            # The page doesn't always query on load; picking a suite type makes it.
            page.select_option("#suiteType", label="1 Bedroom")
            for _ in range(30):
                if captured:
                    break
                page.wait_for_timeout(1_000)
        if not captured:
            raise WyndhamError("Calendar page never requested availability; can't capture API credentials.")
        req = captured[-1]
        # all_headers() includes auth headers that .headers may omit.
        self._headers = {k: v for k, v in req.all_headers().items()
                         if k.lower() not in _SKIP and not k.startswith(":")}
        try:
            self._party_id = str(json.loads(req.post_data or "{}").get("partyId") or "")
        except json.JSONDecodeError:
            self._party_id = ""
        if not self._party_id or not any(k.lower() == "authorization" for k in self._headers):
            raise WyndhamError("Could not capture API credentials from the calendar page.")
        print("Signed in; captured API credentials.")

    def _dump(self, page: Page, tag: str):
        """Save what the browser was showing, for the workflow's debug artifact."""
        d = pathlib.Path("debug")
        d.mkdir(exist_ok=True)
        try:
            page.screenshot(path=str(d / f"{tag}.png"), full_page=True)
            (d / f"{tag}.html").write_text(page.content())
        except PWError:
            pass
        print(f"[debug] {tag}: url={page.url} title={page.title()!r} (screenshot saved to debug/)")
        try:
            text = " ".join(page.inner_text("body", timeout=3_000).split())
        except PWError:
            text = "(could not read page text)"
        print(f"[debug] page text: {text[:800]}")
        print(f"[debug] user agent: {page.evaluate('navigator.userAgent')}")
        print(f"[debug] last {min(len(self._log), 40)} errors/failed requests:")
        for line in self._log[-40:]:
            print(f"[debug]   {line}")

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
            # channel="chromium" = full Chrome in new-headless mode (not the stripped
            # headless shell), with a normal user agent. A persistent profile keeps
            # Okta's "don't ask again on this device" between runs.
            major = _chrome_major(p)
            ctx = p.chromium.launch_persistent_context(
                self.profile_dir, headless=self.headless, channel="chromium",
                args=["--disable-blink-features=AutomationControlled"],
                user_agent=_UA.format(major=major), locale="en-US", timezone_id="America/New_York",
                viewport={"width": 1440, "height": 900})
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("console", lambda m: m.type == "error" and self._log.append(f"console: {m.text[:200]}"))
            page.on("response", lambda r: r.status >= 400 and self._log.append(
                f"HTTP {r.status} {r.request.method} {r.url.split('?')[0][:150]}"))
            page.on("requestfailed", lambda r: self._log.append(
                f"FAILED {r.method} {r.url.split('?')[0][:150]} ({r.failure})"))
            first_pid = next(iter(resorts.values()))
            try:
                self._capture_headers(page, first_pid)
            except WyndhamError:
                self._dump(page, "signin-failure")
                ctx.close()
                raise

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

            ctx.close()
        return out

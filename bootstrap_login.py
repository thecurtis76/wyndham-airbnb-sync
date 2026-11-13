#!/usr/bin/env python3
"""One-time (and occasional) sign-in on your Mac to create the saved session.

Opens a visible browser at the Club Wyndham sign-in page. Sign in yourself,
enter the texted code, and tick "don't ask again" if offered. When you reach
your owner dashboard, the session (cookies + local storage) is saved to
state.json. Upload it as the WYNDHAM_STATE secret:

    base64 -i state.json | gh secret set WYNDHAM_STATE

Re-run this whenever the cloud job fails with "asked for a verification code".
"""
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://clubwyndham.wyndhamdestinations.com/us/en/login"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(LOGIN_URL)
    print("Sign in in the browser window (including the text-message code).")
    page.wait_for_url("**/owner/**", timeout=10 * 60_000)
    page.wait_for_load_state("networkidle")
    ctx.storage_state(path="state.json")
    print("Saved state.json. Now run:  base64 -i state.json | gh secret set WYNDHAM_STATE")
    browser.close()

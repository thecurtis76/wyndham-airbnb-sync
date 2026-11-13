#!/usr/bin/env python3
"""Fetch Club Wyndham availability and write one Airbnb .ics feed per listing.

Usage:
  python sync.py --out public                      # live fetch (needs state.json / credentials)
  python sync.py --out public --from-json data.json  # build from saved data (testing)
  python sync.py --out public --save-json data.json  # also save the raw fetch

Environment:
  WYNDHAM_USERNAME, WYNDHAM_PASSWORD  used only if the saved session has expired
  WYNDHAM_STATE_PATH                  saved browser session (default: state.json)
  FEED_TOKEN                          secret path segment for the published feeds
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

import yaml

from feeds import Block, FeedError, Listing, build_all

ROOT = pathlib.Path(__file__).parent


def load_config():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    listings = [Listing(slug=l["slug"], resort=l["resort"], labels=l.get("labels") or [],
                        airbnb_title=l.get("airbnb_title", ""),
                        min_available=int(l.get("min_available", 1)),
                        allow_no_inventory=bool(l.get("allow_no_inventory", False)))
                for l in cfg["listings"]]
    slugs = [l.slug for l in listings]
    if len(slugs) != len(set(slugs)):
        sys.exit("config.yaml: listing slugs must be unique")
    for l in listings:
        if l.resort not in cfg["resorts"]:
            sys.exit(f"config.yaml: listing {l.slug} uses unknown resort {l.resort!r}")
    raw_blocks = (yaml.safe_load((ROOT / "blocks.yaml").read_text()) or {}).get("blocks") or []
    blocks = [Block(listing=str(b["listing"]), start=_date(b["start"]), end=_date(b["end"]),
                    reason=b.get("reason", "")) for b in raw_blocks]
    return cfg, listings, blocks


def _date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    ap.add_argument("--from-json", help="build feeds from a saved fetch instead of calling Wyndham")
    ap.add_argument("--save-json", help="save the raw Wyndham data here")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    ap.add_argument("--headed", action="store_true", help="show the browser while fetching")
    args = ap.parse_args()

    cfg, listings, blocks = load_config()
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    now = dt.datetime.now(tz)
    today = dt.date.fromisoformat(args.today) if args.today else now.date()
    horizon = int(cfg.get("feed_horizon_days", 730))
    resorts = {k: v["product_id"] for k, v in cfg["resorts"].items()
               if any(l.resort == k for l in listings)}

    if args.from_json:
        data = json.loads(pathlib.Path(args.from_json).read_text())
    else:
        from wyndham_client import WyndhamClient, WyndhamError
        client = WyndhamClient(
            state_path=os.environ.get("WYNDHAM_STATE_PATH", "state.json"),
            username=os.environ.get("WYNDHAM_USERNAME"),
            password=os.environ.get("WYNDHAM_PASSWORD"),
            consumer_type=cfg.get("consumer_type", "REF|CLUB WYNDHAM PLUS"),
            headless=not args.headed,
        )
        # Wyndham's window is ~15 months; ask for 16 and let the rest be blocked.
        try:
            data = client.fetch(resorts, today, today + dt.timedelta(days=490))
        except WyndhamError as e:
            sys.exit(f"ERROR: {e}")
    if args.save_json:
        pathlib.Path(args.save_json).write_text(json.dumps(data))

    try:
        feeds = build_all(listings, data, blocks, today, horizon, now)
    except FeedError as e:
        sys.exit(f"ERROR: {e}")

    token = os.environ.get("FEED_TOKEN", "").strip("/")
    out = pathlib.Path(args.out) / token if token else pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"Feeds for {today} (horizon {horizon} nights):")
    for l in listings:
        text, open_nights = feeds[l.slug]
        (out / f"{l.slug}.ics").write_text(text, newline="")
        print(f"  {l.slug:40s} {open_nights:4d} open nights")
    # Keep Pages from listing anything useful at the site root.
    (pathlib.Path(args.out) / "index.html").write_text("<!doctype html><title>.</title>\n")
    (pathlib.Path(args.out) / ".nojekyll").write_text("")
    (out / "last-sync.txt").write_text(now.isoformat() + "\n")


if __name__ == "__main__":
    main()

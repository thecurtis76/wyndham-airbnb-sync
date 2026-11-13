"""Turn Wyndham per-night availability into Airbnb-importable .ics feeds.

Availability input shape (per resort key):
    {"2027-03-07": {"1 Bedroom Deluxe": 49, "2 Bedroom Deluxe": 12}, ...}
A missing date means Wyndham returned nothing for it (outside its window).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

Availability = dict[str, dict[str, int]]   # date -> label -> units available


class FeedError(Exception):
    """Raised when data looks wrong enough that publishing would be unsafe."""


def norm(label: str) -> str:
    return " ".join(label.lower().split())


@dataclass
class Listing:
    slug: str
    resort: str
    labels: list[str]
    airbnb_title: str = ""
    min_available: int = 1
    allow_no_inventory: bool = False
    norm_labels: set[str] = field(init=False)

    def __post_init__(self):
        self.norm_labels = {norm(l) for l in self.labels}


@dataclass
class Block:
    listing: str          # slug, resort key, or "*"
    start: dt.date
    end: dt.date          # inclusive
    reason: str = ""

    def applies(self, listing: Listing) -> bool:
        return self.listing in ("*", listing.slug, listing.resort)


def units_for(listing: Listing, night: dict[str, int]) -> int:
    return sum(n for lbl, n in night.items() if norm(lbl) in listing.norm_labels)


def blocked_nights(listing: Listing, avail: Availability, today: dt.date,
                   horizon_days: int, blocks: list[Block]) -> list[dt.date]:
    out = []
    for i in range(horizon_days):
        d = today + dt.timedelta(days=i)
        night = avail.get(d.isoformat())
        is_open = night is not None and units_for(listing, night) >= listing.min_available
        if is_open and any(b.applies(listing) and b.start <= d <= b.end for b in blocks):
            is_open = False
        if not is_open:
            out.append(d)
    return out


def to_ranges(days: list[dt.date]) -> list[tuple[dt.date, dt.date]]:
    """Collapse sorted nights into (first_night, checkout_day) ranges."""
    ranges: list[tuple[dt.date, dt.date]] = []
    for d in days:
        if ranges and ranges[-1][1] == d:
            ranges[-1] = (ranges[-1][0], d + dt.timedelta(days=1))
        else:
            ranges.append((d, d + dt.timedelta(days=1)))
    return ranges


def render_ics(listing: Listing, ranges: list[tuple[dt.date, dt.date]],
               generated: dt.datetime) -> str:
    stamp = generated.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//wyndham-airbnb-sync//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Wyndham availability - {listing.slug}",
    ]
    for start, end in ranges:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{listing.slug}-{start:%Y%m%d}@wyndham-airbnb-sync",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{end:%Y%m%d}",
            "SUMMARY:Not available (Wyndham)",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def validate(listings: list[Listing], data: dict[str, Availability], today: dt.date) -> list[str]:
    """Fail closed: raise if publishing would likely open nights wrongly or block everything by mistake."""
    problems = []
    for key, avail in data.items():
        if not avail:
            problems.append(f"{key}: no availability data returned")
            continue
        near = [today + dt.timedelta(days=i) for i in range(1, 60)]
        missing = [d for d in near if d.isoformat() not in avail]
        if missing:
            problems.append(f"{key}: {len(missing)} of the next 59 nights missing from Wyndham data")
        if not any(any(v > 0 for v in n.values()) for n in avail.values()):
            problems.append(f"{key}: zero units available on every night (suspicious)")
    for l in listings:
        if l.resort not in data:
            problems.append(f"{l.slug}: resort {l.resort!r} not fetched")
            continue
        if l.allow_no_inventory:
            continue
        seen = {norm(lbl) for n in data[l.resort].values() for lbl in n}
        if not (l.norm_labels & seen):
            problems.append(
                f"{l.slug}: none of its labels {l.labels} appear in Wyndham data for {l.resort} "
                f"(labels seen: {sorted(seen)}). Wyndham may have renamed a suite type."
            )
    if problems:
        raise FeedError("Refusing to publish:\n  - " + "\n  - ".join(problems))
    return problems


def build_all(listings: list[Listing], data: dict[str, Availability], blocks: list[Block],
              today: dt.date, horizon_days: int, generated: dt.datetime) -> dict[str, tuple[str, int]]:
    """Return {slug: (ics_text, open_night_count)}."""
    validate(listings, data, today)
    feeds = {}
    for l in listings:
        blocked = blocked_nights(l, data[l.resort], today, horizon_days, blocks)
        feeds[l.slug] = (render_ics(l, to_ranges(blocked), generated), horizon_days - len(blocked))
    return feeds

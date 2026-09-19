"""Live, expanded view of every calendar: fetch each feed, expand recurrences, normalise rows."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from icalendar import Calendar

from localcal import ical
from localcal.model import Feed
from localcal.sources import fetch_events

log = logging.getLogger(__name__)
USER_AGENT = "localcal/0.1 (+https://github.com/ByteMasterPro/local-calendars)"


def load_calendar(feed: Feed) -> Calendar:
    """Every feed as an icalendar.Calendar, fetched live (external .ics or built from source)."""
    if feed.external:
        resp = requests.get(feed.feed_url, headers={"User-Agent": USER_AGENT}, timeout=60)
        resp.raise_for_status()
        return Calendar.from_ical(resp.content)
    return Calendar.from_ical(ical.render(feed, fetch_events(feed)))


def occurrences(feed: Feed, cal: Calendar, start: date, end: date) -> list[dict]:
    """Expand recurring events and normalise each occurrence to a plain dict.

    `end` is exclusive, matching recurring_ical_events.between().
    """
    tz = ZoneInfo(feed.timezone)
    rows = []
    for v in recurring_ical_events.of(cal).between(start, end):
        s = v["DTSTART"].dt
        e = v["DTEND"].dt if "DTEND" in v else s
        all_day = not isinstance(s, datetime)
        if not all_day:
            s = s.astimezone(tz) if s.tzinfo else s.replace(tzinfo=tz)
            e = e.astimezone(tz) if e.tzinfo else e.replace(tzinfo=tz)
        cats = v.get("CATEGORIES")
        rows.append({
            "calendar": feed.name,
            "slug": feed.slug,
            "kind": feed.kind,
            "uid": str(v.get("UID", "")),
            "summary": str(v.get("SUMMARY", "")).strip(),
            "start": s.isoformat(),
            "end": e.isoformat(),
            "all_day": all_day,
            "location": str(v.get("LOCATION", "")),
            "url": str(v.get("URL", "")),
            "description": str(v.get("DESCRIPTION", "")),
            "categories": [str(c) for c in cats.cats] if cats is not None else [],
            "_sort": s if isinstance(s, datetime) else datetime.combine(s, time.min, tzinfo=tz),
        })
    return rows


def gather(feeds: list[Feed], start: date, end: date) -> tuple[list[dict], int]:
    """All occurrences across feeds, sorted. Returns (rows, number_of_feeds_that_failed)."""
    rows: list[dict] = []
    errors = 0
    for feed in feeds:
        try:
            cal = load_calendar(feed)
        except Exception as exc:
            log.error("%s: could not load (%s)", feed.slug, exc)
            errors += 1
            continue
        rows.extend(occurrences(feed, cal, start, end))
    rows.sort(key=lambda r: r["_sort"])
    return rows, errors


def haystack(row: dict) -> str:
    return " ".join([row["summary"], row["description"], " ".join(row["categories"]), row["calendar"]])


def matches(row: dict, pattern: str | re.Pattern | None) -> bool:
    if not pattern:
        return False
    rx = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, re.I)
    return bool(rx.search(haystack(row)))

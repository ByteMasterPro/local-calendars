"""Live, expanded view of every calendar: fetch each feed, expand recurrences, normalise rows."""

from __future__ import annotations

import logging
import re
import time as _time
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


# Seconds to wait after each failed attempt. Vanish's host (Liquid Web) intermittently serves a
# chain that fails verification, and Flying Ace has answered with an HTML error page; both clear
# within a minute or two, so the backoff spans ~75s rather than the ~6s a tight retry gives.
RETRY_BACKOFF = (3, 10, 25, 40)


def load_calendar(feed: Feed, backoff: tuple[int, ...] = RETRY_BACKOFF) -> Calendar:
    """Every feed as an icalendar.Calendar, fetched live (external .ics or built from source).

    Third-party hosts hiccup, so transient failures are retried over a widening backoff before
    the feed is given up on. A feed that stays broken still only costs its own retries: other
    calendars are fetched independently and the digest posts without it.
    """
    last: Exception | None = None
    for attempt in range(len(backoff) + 1):
        try:
            if feed.external:
                resp = requests.get(feed.feed_url, headers={"User-Agent": USER_AGENT}, timeout=60)
                resp.raise_for_status()
                return Calendar.from_ical(resp.content)
            return Calendar.from_ical(ical.render(feed, fetch_events(feed)))
        except Exception as exc:
            last = exc
            if attempt < len(backoff):
                log.warning("%s: attempt %d failed (%s); retrying in %ds",
                            feed.slug, attempt + 1, str(exc)[:120], backoff[attempt])
                _time.sleep(backoff[attempt])
    raise last  # type: ignore[misc]


def occurrences(feed: Feed, cal: Calendar, start: date, end: date) -> list[dict]:
    """Expand recurring events and normalise each occurrence to a plain dict.

    `end` is exclusive, matching recurring_ical_events.between().
    """
    tz = ZoneInfo(feed.timezone)
    # remember which UIDs are series and when they end, so the digest can say "weekends thru Nov 8"
    series_until: dict[str, date | None] = {}
    for v in cal.walk("VEVENT"):
        if "RRULE" in v:
            until = v["RRULE"].get("UNTIL")
            u = until[0] if until else None
            if isinstance(u, datetime):                 # UNTIL is UTC on the wire; read it as local
                u = (u.astimezone(tz) if u.tzinfo else u.replace(tzinfo=tz)).date()
            series_until[str(v.get("UID", ""))] = u
    rows = []
    for v in recurring_ical_events.of(cal).between(start, end):
        s = v["DTSTART"].dt
        e = v["DTEND"].dt if "DTEND" in v else s
        all_day = not isinstance(s, datetime)
        if not all_day:
            s, e = _localize(s, tz, feed), _localize(e, tz, feed)
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
            "series": str(v.get("UID", "")) in series_until,
            "series_until": (series_until.get(str(v.get("UID", ""))) or None) and series_until[str(v.get("UID", ""))].isoformat(),
            "_sort": s if isinstance(s, datetime) else datetime.combine(s, time.min, tzinfo=tz),
        })
    return rows


def _localize(dt: datetime, tz: ZoneInfo, feed: Feed) -> datetime:
    """Express dt in the feed's timezone. If the venue tagged it with a TZID they use by mistake
    (Flying Ace enters some events as America/Halifax), keep the wall-clock time and swap the zone."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    key = getattr(dt.tzinfo, "key", None) or getattr(dt.tzinfo, "zone", None) or str(dt.tzinfo)
    if key in feed.wall_clock_tzids:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


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

"""Live, expanded view of every calendar: fetch each feed, expand recurrences, normalise rows."""

from __future__ import annotations

import logging
import re
import time as _time
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from icalendar import Calendar

from localcal import ical
from localcal.model import Feed
from localcal.sources import fetch_events

log = logging.getLogger(__name__)
USER_AGENT = "localcal/0.1 (+https://github.com/ByteMasterPro/local-calendars)"


CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"

# Seconds to wait after each failed attempt. Vanish's host (Liquid Web) intermittently serves a
# chain that fails verification, and Flying Ace has answered with an HTML error page; both clear
# within a minute or two, so the backoff spans ~75s rather than the ~6s a tight retry gives.
RETRY_BACKOFF = (3, 10, 25, 40)


def fetch_external(feed: Feed) -> bytes:
    """An external feed's raw .ics bytes, checked for being an actual calendar.

    Vanish's host has answered a feed request with a parked-domain lander page (HTTP 200,
    HTML body), so a response is only accepted once it parses as a VCALENDAR.
    """
    resp = requests.get(feed.feed_url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    Calendar.from_ical(resp.content)                 # raises on HTML or anything unparseable
    return resp.content


def cache_path(feed: Feed, cache_dir: Path | None = None) -> Path:
    return (cache_dir or CACHE_DIR) / f"{feed.slug}.ics"


def save_cache(feed: Feed, content: bytes, cache_dir: Path | None = None) -> None:
    path = cache_path(feed, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def load_calendar(feed: Feed, backoff: tuple[int, ...] = RETRY_BACKOFF, cache_dir: Path | None = None) -> Calendar:
    """Every feed as an icalendar.Calendar, fetched live (external .ics or built from source).

    Third-party hosts hiccup, so transient failures are retried over a widening backoff. If an
    external feed is still unreachable after that, the last good copy under `cache/` is used
    rather than dropping the venue from the digest; `localcal build` refreshes those daily.
    """
    last: Exception | None = None
    for attempt in range(len(backoff) + 1):
        try:
            if feed.external:
                content = fetch_external(feed)
                save_cache(feed, content, cache_dir)
                return Calendar.from_ical(content)
            return Calendar.from_ical(ical.render(feed, fetch_events(feed)))
        except Exception as exc:
            last = exc
            if attempt < len(backoff):
                log.warning("%s: attempt %d failed (%s); retrying in %ds",
                            feed.slug, attempt + 1, str(exc)[:120], backoff[attempt])
                _time.sleep(backoff[attempt])

    cached = cache_path(feed, cache_dir)
    if feed.external and cached.exists():
        age_h = (datetime.now(timezone.utc).timestamp() - cached.stat().st_mtime) / 3600
        log.warning("%s: live fetch failed (%s); using cached copy from %.0fh ago",
                    feed.slug, str(last)[:120], age_h)
        return Calendar.from_ical(cached.read_bytes())
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
            "image": _first_attach(v),
            "series": str(v.get("UID", "")) in series_until,
            "series_until": (series_until.get(str(v.get("UID", ""))) or None) and series_until[str(v.get("UID", ""))].isoformat(),
            "_sort": s if isinstance(s, datetime) else datetime.combine(s, time.min, tzinfo=tz),
        })
    return rows


def _first_attach(v) -> str:
    """The event's poster, if the feed carries one (Elfsight cover art, iCal ATTACH, Squarespace)."""
    att = v.get("ATTACH")
    if not att:
        return ""
    first = att[0] if isinstance(att, list) else att
    url = str(first)
    return url if url.startswith("http") else ""


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


def title_haystack(row: dict) -> str:
    """Title and categories only. Exclusions match on this: a venue names its discount nights
    ("50% Off Growler Fills"), while a real event may merely mention a discount among its
    attractions - White's Ferry's Leesburg Fall Festival offered 20% off wine flights, and
    matching the description threw the whole festival away."""
    return " ".join([row["summary"], " ".join(row["categories"])])


def matches(row: dict, pattern: str | re.Pattern | None) -> bool:
    if not pattern:
        return False
    rx = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, re.I)
    return bool(rx.search(haystack(row)))

"""Curated events from a YAML file, for things nobody publishes a feed for (fairs, festivals,
carnivals). Christopher and Claude both add to it.

Config:
    source:
      type: manual
      file: config/events/fairs-festivals.yaml      # relative to the repo root

File format:
    events:
      - title: Bluemont Fair
        start: 2026-09-19             # a date -> all-day; "2026-09-26 10:00" -> timed
        end: 2026-09-20               # inclusive last day for all-day events; end time for timed
        location: Bluemont Fairgrounds, 33846 Snickersville Tpke, Bluemont, VA 20135
        url: https://www.bluemontfair.org/
        description: ...
        tags: [fair, family]          # become CATEGORIES; the Discord digest matches on them
        id: bluemont-fair-2026        # optional; defaults to slug(title)-start

      - title: Temple Hall Farm Fall Days
        season: {start: 2026-09-26, end: 2026-11-03, days: [SA, SU]}   # weekly all-day on those
        ...                                                            # days; days defaults to SA,SU
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from localcal.model import Event, Feed
from localcal.text import slugify

_WEEKDAY_INDEX = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def load(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text()) or {}
    return list(raw.get("events") or [])


def parse_events(entries: list[dict], feed: Feed) -> list[Event]:
    tz = ZoneInfo(feed.timezone)
    return [_parse_entry(e, feed, tz) for e in entries]


def _parse_entry(e: dict, feed: Feed, tz: ZoneInfo) -> Event:
    title = str(e["title"]).strip()
    rrule = None
    exdates = []
    if "season" in e:
        season = e["season"]
        first, last = _as_date(season["start"]), _as_date(season["end"])
        days = [d.upper() for d in season.get("days") or ["SA", "SU"]]
        wanted = {_WEEKDAY_INDEX[d] for d in days}
        while first.weekday() not in wanted:                 # DTSTART must be an occurrence
            first += timedelta(days=1)
        start, end, all_day = first, first + timedelta(days=1), True
        rrule = {"FREQ": "WEEKLY", "BYDAY": days, "UNTIL": datetime.combine(last, time(23, 59), tzinfo=tz)}
        exdates = [_as_date(x) for x in season.get("skip") or []]
    else:
        start = _as_when(e["start"], tz)
        all_day = isinstance(start, date) and not isinstance(start, datetime)
        if "end" in e and e["end"] is not None:
            end = _as_when(e["end"], tz)
            if all_day:
                end = end + timedelta(days=1)                   # YAML end is inclusive; DTEND exclusive
        else:
            end = start + (timedelta(days=1) if all_day else timedelta(minutes=feed.default_duration_minutes))

    uid_base = e.get("id") or f"{slugify(title)}-{start_key(start)}"
    tags = [str(t) for t in e.get("tags") or []]
    desc_parts = [str(e.get("description") or "").strip(), f"More info: {e['url']}" if e.get("url") else ""]
    return Event(
        uid=f"{uid_base}@{feed.slug}",
        summary=title,
        start=start,
        end=end,
        all_day=all_day,
        description="\n\n".join(p for p in desc_parts if p),
        location=str(e.get("location") or feed.location),
        url=str(e.get("url") or feed.url),
        categories=tags,
        rrule=rrule,
        exdates=exdates,
    )


def start_key(start: datetime | date) -> str:
    return (start.date() if isinstance(start, datetime) else start).isoformat()


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v).strip()[:10])


def _as_when(v, tz: ZoneInfo) -> datetime | date:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=tz)
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if len(s) <= 10:
        return date.fromisoformat(s)
    return datetime.fromisoformat(s).replace(tzinfo=tz)

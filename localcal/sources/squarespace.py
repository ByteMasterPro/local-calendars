"""Squarespace "Events" collection adapter.

Any Squarespace events page answers `?format=json` with its items as structured data
(`upcoming` and `past` lists; ms-epoch startDate/endDate; a location block; fullUrl).
Some templates also answer `?format=ical`; when that works, prefer `feed_url` instead.

Squarespace has no recurrence support, so venues often enter a weekly series as ONE event
spanning months (e.g. Zumba Thu May 7 7pm -> Thu Sep 24 8pm). A timed span of 7+ days that
starts and ends on the same weekday is turned into a weekly RRULE at the start clock time.

Config:
    source:
      type: squarespace
      url: https://www.downtownoneloudoun.com/events
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

from localcal.model import Event, Feed
from localcal.text import html_to_text

log = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (compatible; localcal/0.1; +https://github.com/ByteMasterPro/local-calendars)"


def fetch(url: str, session: requests.Session | None = None) -> dict:
    sess = session or requests.Session()
    resp = sess.get(url, params={"format": "json"}, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_events(payload: dict, feed: Feed) -> list[Event]:
    tz = ZoneInfo(feed.timezone)
    base = feed.source.get("url") or feed.url
    out: list[Event] = []
    for item in (payload.get("upcoming") or []) + (payload.get("past") or []):
        try:
            out.append(_parse_item(item, feed, tz, base))
        except Exception as exc:
            log.warning("skipping item %s (%s): %s", item.get("id"), item.get("title"), exc)
    return out


def _parse_item(item: dict, feed: Feed, tz: ZoneInfo, base: str) -> Event:
    start = datetime.fromtimestamp(item["startDate"] // 1000, tz)
    end = datetime.fromtimestamp(item.get("endDate", item["startDate"]) // 1000, tz)
    if end <= start:
        end = start + timedelta(minutes=feed.default_duration_minutes)

    # Squarespace has no all-day flag; a midnight-to-midnight span is the closest signal.
    all_day = start.time() == datetime.min.time() and end.time() in (datetime.min.time(),) and (end - start) >= timedelta(days=1)
    if all_day:
        start, end = start.date(), end.date()

    rrule = None
    if not all_day and (end - start) >= timedelta(days=7) and start.weekday() == end.weekday():
        # months-long "event" on one weekday = a weekly series entered as a single span
        until = end
        end = start.replace(hour=end.hour, minute=end.minute)
        if end <= start:
            end = start + timedelta(minutes=feed.default_duration_minutes)
        rrule = {"FREQ": "WEEKLY", "BYDAY": [_BYDAY[start.weekday()]], "UNTIL": until}

    loc = item.get("location") or {}
    location = ", ".join(p for p in (loc.get("addressTitle"), loc.get("addressLine1"), loc.get("addressLine2")) if p) or feed.location
    url = urljoin(base, item.get("fullUrl") or "")
    excerpt = html_to_text(item.get("excerpt") or "")
    description = "\n\n".join(p for p in (excerpt, f"Details: {url}") if p)
    return Event(
        uid=f"{item['id']}@{feed.slug}",
        summary=(item.get("title") or "(untitled)").strip(),
        start=start,
        end=end,
        all_day=all_day,
        description=description,
        location=location,
        url=url,
        categories=list(item.get("categories") or []) + list(item.get("tags") or []),
        rrule=rrule,
    )


_BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

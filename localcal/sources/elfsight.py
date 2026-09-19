"""Elfsight "Event Calendar" widget adapter.

Many small-business sites (Squarespace, Wix, WordPress) embed Elfsight widgets. The
widget's data is served as public JSON from a boot endpoint keyed by the widget id,
which you can read off the host page:

    <div class="elfsight-app-5cfe5396-d62a-432e-8a83-f53cb73bb713" ...>

Config:
    source:
      type: elfsight
      widget_id: 5cfe5396-d62a-432e-8a83-f53cb73bb713
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from localcal.model import Event, Feed
from localcal.text import html_to_text as _html_to_text

log = logging.getLogger(__name__)

BOOT_URL = "https://core.service.elfsight.com/p/boot/"
USER_AGENT = "localcal/0.1 (+https://github.com/ByteMasterPro/local-calendars)"

# Elfsight repeat vocabulary -> RFC 5545 FREQ. Unknown values are logged and treated
# as one-off events rather than guessed.
_FREQ = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}
_WEEKDAYS = {"mo": "MO", "tu": "TU", "we": "WE", "th": "TH", "fr": "FR", "sa": "SA", "su": "SU"}


def fetch_settings(widget_id: str, session: requests.Session | None = None) -> dict[str, Any]:
    """Return the widget's `settings` block (events, eventTypes, locations, hosts, ...)."""
    sess = session or requests.Session()
    resp = sess.get(BOOT_URL, params={"w": widget_id}, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    widget = payload["data"]["widgets"][widget_id]
    if widget.get("status") != 1:
        raise RuntimeError(f"Elfsight widget {widget_id} returned status {widget.get('status')}")
    data = widget["data"]
    if data.get("app") != "event-calendar":
        raise RuntimeError(f"Elfsight widget {widget_id} is a {data.get('app')!r} widget, not event-calendar")
    return data["settings"]


def parse_events(settings: dict[str, Any], feed: Feed) -> list[Event]:
    types = {t["id"]: t["name"] for t in settings.get("eventTypes") or []}
    locations = {l["id"]: l for l in settings.get("locations") or []}
    default_tz = ZoneInfo(feed.timezone)
    out: list[Event] = []
    for raw in settings.get("events") or []:
        try:
            out.append(_parse_one(raw, feed, types, locations, default_tz))
        except Exception as exc:  # one bad record must not sink the whole feed
            log.warning("skipping event %s (%s): %s", raw.get("id"), raw.get("name"), exc)
    return out


def _parse_one(raw, feed: Feed, types, locations, default_tz) -> Event:
    tz = ZoneInfo(raw.get("timeZone") or feed.timezone) if raw.get("timeZone") else default_tz
    all_day = bool(raw.get("isAllDay")) or raw["start"].get("type") == "date" or not raw["start"].get("time")

    start = _to_when(raw["start"], tz, all_day)
    end = _to_when(raw.get("end") or raw["start"], tz, all_day)
    if all_day:
        if end <= start:
            end = start + timedelta(days=1)   # DTEND is exclusive for all-day events
    elif end <= start:
        end = start + timedelta(minutes=feed.default_duration_minutes)

    # Action buttons (e.g. "Get Tickets") carry the only outbound link on most events.
    links: list[tuple[str, str]] = []
    for action in raw.get("actions") or []:
        link = action.get("link") or {}
        href = link.get("value") or link.get("rawValue") or action.get("url")
        if href:
            links.append((action.get("text") or "Link", href))

    desc_lines = [_html_to_text(raw.get("description") or "")]
    desc_lines += [f"{text}: {href}" for text, href in links]
    desc_lines.append(f"Event listing: {feed.url}")
    description = "\n\n".join(line for line in desc_lines if line)

    return Event(
        uid=f"{raw['id']}@{feed.slug}",
        summary=html.unescape(raw.get("name") or "(untitled)").strip(),
        start=start,
        end=end,
        all_day=all_day,
        description=description,
        location=_location(raw, locations) or feed.location,
        url=links[0][1] if links else feed.url,
        categories=[types[t] for t in _as_list(raw.get("eventType")) if t in types],
        rrule=_rrule(raw, tz),
        exdates=[_to_when({"date": d, "time": raw["start"].get("time")}, tz, all_day)
                 for d in raw.get("exceptions") or [] if isinstance(d, str)],
    )


def _to_when(when: dict[str, Any], tz: ZoneInfo, all_day: bool) -> datetime | date:
    d = date.fromisoformat(when["date"][:10])
    if all_day:
        return d
    hh, mm = (when.get("time") or "00:00").split(":")[:2]
    return datetime.combine(d, time(int(hh), int(mm)), tzinfo=tz)


def _location(raw, locations) -> str:
    for loc_id in _as_list(raw.get("location")):
        loc = locations.get(loc_id)
        if loc:
            parts = [loc.get("name"), loc.get("address")]
            return ", ".join(p for p in parts if p)
    return ""


def _rrule(raw, tz: ZoneInfo) -> dict[str, Any] | None:
    period = raw.get("repeatPeriod") or "noRepeat"
    if period == "noRepeat":
        return None
    freq = _FREQ.get(period) or _FREQ.get(raw.get("repeatFrequency") or "")
    if not freq:
        log.warning("event %s: unknown repeatPeriod %r; treating as one-off", raw.get("id"), period)
        return None
    rule: dict[str, Any] = {"FREQ": freq, "INTERVAL": int(raw.get("repeatInterval") or 1)}
    if freq == "WEEKLY":
        days = [_WEEKDAYS[d[:2].lower()] for d in raw.get("repeatWeeklyOnDays") or [] if d[:2].lower() in _WEEKDAYS]
        if days:
            rule["BYDAY"] = days
    ends = (raw.get("repeatEnds") or "never").lower()
    if "date" in ends and raw.get("repeatEndsDate"):
        until = date.fromisoformat(str(raw["repeatEndsDate"])[:10])
        rule["UNTIL"] = datetime.combine(until, time(23, 59), tzinfo=tz)
    elif "occurrence" in ends and raw.get("repeatEndsOccurrences"):
        rule["COUNT"] = int(raw["repeatEndsOccurrences"])
    return rule


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]

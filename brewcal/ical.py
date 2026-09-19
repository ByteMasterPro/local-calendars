"""Render Event lists to RFC 5545 .ics bytes suitable for URL subscription."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from icalendar import Calendar, Event as VEvent, vDDDTypes, vRecur, vText

from brewcal import __version__
from brewcal.model import Brewery, Event


def render(brewery: Brewery, events: list[Event], now: datetime | None = None) -> bytes:
    now = now or datetime.now(timezone.utc)
    cal = Calendar()
    cal.add("PRODID", f"-//brewcal {__version__}//EN")
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("METHOD", "PUBLISH")
    cal.add("X-WR-CALNAME", brewery.name)
    cal.add("X-WR-CALDESC", brewery.description or f"Events at {brewery.name}. Source: {brewery.url}")
    cal.add("X-WR-TIMEZONE", brewery.timezone)
    # Hints for how often clients should re-fetch (Apple honours REFRESH-INTERVAL, Google ignores both).
    cal.add("REFRESH-INTERVAL", vDDDTypes(timedelta(days=1)), parameters={"VALUE": "DURATION"})
    cal.add("X-PUBLISHED-TTL", "P1D")

    for ev in sorted(events, key=lambda e: _sort_key(e.start)):
        cal.add_component(_vevent(ev, now))

    # Local datetimes carry TZID; this emits the matching VTIMEZONE so recurring events
    # keep their wall-clock time across DST changes. Bound it to the feed's own date
    # range (plus slack for open-ended RRULEs) so it is a few transitions, not 1970-2038.
    if events:
        first = min(_sort_key(e.start) for e in events).date() - timedelta(days=366)
        last = max(_sort_key(e.end) for e in events).date() + timedelta(days=3 * 366)
        cal.add_missing_timezones(first_date=first, last_date=last)
    return cal.to_ical()


def _vevent(ev: Event, now: datetime) -> VEvent:
    v = VEvent()
    v.add("UID", ev.uid)
    v.add("DTSTAMP", now)
    v.add("DTSTART", ev.start)
    v.add("DTEND", ev.end)
    v.add("SUMMARY", ev.summary)
    if ev.description:
        v.add("DESCRIPTION", ev.description)
    if ev.location:
        v.add("LOCATION", ev.location)
    if ev.url:
        v.add("URL", vText(ev.url))
    if ev.categories:
        v.add("CATEGORIES", ev.categories)
    if ev.rrule:
        v.add("RRULE", vRecur(ev.rrule))
    for ex in ev.exdates:
        v.add("EXDATE", ex)
    v.add("TRANSP", "TRANSPARENT")   # subscribed brewery events should not block free/busy
    return v


def _sort_key(when: datetime | date) -> datetime:
    if isinstance(when, datetime):
        return when.astimezone(timezone.utc)
    return datetime.combine(when, datetime.min.time(), tzinfo=timezone.utc)


def filter_window(events: list[Event], keep_past_days: int, today: date | None = None) -> list[Event]:
    """Drop events that ended more than keep_past_days ago. Recurring events are always kept."""
    today = today or date.today()
    cutoff = today - timedelta(days=keep_past_days)
    kept = []
    for ev in events:
        if ev.rrule:
            kept.append(ev)
            continue
        end_day = ev.end if isinstance(ev.end, date) and not isinstance(ev.end, datetime) else ev.end.date()
        if end_day >= cutoff:
            kept.append(ev)
    return kept


def strip_volatile(ics: bytes) -> bytes:
    """Remove DTSTAMP lines so two renders of identical events compare equal."""
    return b"\r\n".join(line for line in ics.split(b"\r\n") if not line.startswith(b"DTSTAMP"))

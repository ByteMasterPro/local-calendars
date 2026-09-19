"""Render Event lists to RFC 5545 .ics bytes suitable for URL subscription."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from icalendar import Calendar, Event as VEvent, vDDDTypes, vRecur, vText

from localcal import __version__
from localcal.model import Event, Feed


def render(feed: Feed, events: list[Event], now: datetime | None = None) -> bytes:
    now = now or datetime.now(timezone.utc)
    cal = Calendar()
    cal.add("PRODID", f"-//localcal {__version__}//EN")
    cal.add("VERSION", "2.0")
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("METHOD", "PUBLISH")
    cal.add("X-WR-CALNAME", feed.name)
    cal.add("X-WR-CALDESC", feed.description or f"Events at {feed.name}. Source: {feed.url}")
    cal.add("X-WR-TIMEZONE", feed.timezone)
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
        rule = dict(ev.rrule)
        until = rule.get("UNTIL")
        # RFC 5545: with a TZID DTSTART, UNTIL must be in UTC (a floating UNTIL is read as UTC
        # by most clients, which silently drops the last occurrence).
        if isinstance(until, datetime) and until.tzinfo is not None:
            rule["UNTIL"] = until.astimezone(timezone.utc)
        elif isinstance(until, date) and not isinstance(until, datetime) and isinstance(ev.start, datetime):
            rule["UNTIL"] = datetime.combine(until, datetime.max.time().replace(microsecond=0), tzinfo=ev.start.tzinfo).astimezone(timezone.utc)
        v.add("RRULE", vRecur(rule))
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
    return [ev for ev in events if ev.rrule or ev.end_date >= cutoff]


def parse(ics: bytes, slug_hint: str = "") -> list[Event]:
    """Read VEVENTs back into Event objects (used to accumulate feeds with a short horizon)."""
    cal = Calendar.from_ical(ics)
    out: list[Event] = []
    for v in cal.walk("VEVENT"):
        start = v["DTSTART"].dt
        end = v["DTEND"].dt if "DTEND" in v else start
        all_day = not isinstance(start, datetime)
        cats = v.get("CATEGORIES")
        out.append(Event(
            uid=str(v["UID"]),
            summary=str(v.get("SUMMARY", "")),
            start=start,
            end=end,
            all_day=all_day,
            description=str(v.get("DESCRIPTION", "")),
            location=str(v.get("LOCATION", "")),
            url=str(v.get("URL", "")),
            categories=[str(c) for c in cats.cats] if cats is not None else [],
            rrule=dict(v["RRULE"]) if "RRULE" in v else None,
            exdates=[d.dt for ex in _as_list(v.get("EXDATE")) for d in ex.dts],
        ))
    return out


def merge_accumulated(fresh: list[Event], previous: list[Event], today: date | None = None) -> list[Event]:
    """Combine a short-horizon fetch with what we published before.

    The source only shows events in [today, horizon]. Inside that window the fresh fetch is
    authoritative: anything we had that is no longer listed was cancelled. Outside it (the past)
    we keep what we had, since the source has already forgotten those events.
    """
    today = today or date.today()
    horizon = max((e.start_date for e in fresh), default=today)
    fresh_uids = {e.uid for e in fresh}
    kept = list(fresh)
    for old in previous:
        if old.uid in fresh_uids:
            continue
        if today <= old.start_date <= horizon:
            continue          # dropped upstream while still in the visible window: cancelled
        kept.append(old)
    return kept


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def strip_volatile(ics: bytes) -> bytes:
    """Remove DTSTAMP lines so two renders of identical events compare equal."""
    return b"\r\n".join(line for line in ics.split(b"\r\n") if not line.startswith(b"DTSTAMP"))

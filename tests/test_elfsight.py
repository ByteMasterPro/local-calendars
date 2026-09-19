import json
from datetime import date, datetime, timezone
from pathlib import Path

from icalendar import Calendar

from localcal import ical
from localcal.model import Feed
from localcal.sources import elfsight

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "elfsight_boot.json").read_text())
SETTINGS = FIXTURE["data"]["widgets"]["test-widget"]["data"]["settings"]

FEED = Feed(
    slug="test-brewery",
    name="Test Brewery", kind="brewery",
    url="https://example.com/events",
    location="Test Brewery, 1 Main St, Sterling, VA",
    timezone="America/New_York",
    source={"type": "elfsight", "widget_id": "test-widget"},
)


def by_name(events, name):
    return next(e for e in events if e.summary == name)


def test_parses_every_fixture_event():
    events = elfsight.parse_events(SETTINGS, FEED)
    assert len(events) == len(SETTINGS["events"])
    assert len({e.uid for e in events}) == len(events)
    assert all(e.uid.endswith("@test-brewery") for e in events)


def test_timed_event_is_local_tz_aware():
    ev = by_name(elfsight.parse_events(SETTINGS, FEED), "Honorfest")
    assert isinstance(ev.start, datetime) and ev.start.tzinfo is not None
    assert ev.start.isoformat() == "2026-09-26T11:00:00-04:00"
    assert ev.end.isoformat() == "2026-09-26T23:00:00-04:00"
    assert not ev.all_day
    assert ev.location == FEED.location
    assert "Raise a Stein" in ev.description
    assert "<div>" not in ev.description
    assert ev.description.endswith("Event listing: https://example.com/events")


def test_action_link_becomes_url_and_description_line():
    ev = by_name(elfsight.parse_events(SETTINGS, FEED), "3rd Annual Classic Lite Beer Mile")
    assert ev.url.startswith("https://www.eventbrite.com/e/")
    assert ev.categories == ["Ticketed Event"]
    assert "Get Tickets: https://www.eventbrite.com" in ev.description or "https://www.eventbrite.com" in ev.description


def test_zero_length_event_gets_default_duration():
    ev = by_name(elfsight.parse_events(SETTINGS, FEED), "Honorfest Beer Dinner")
    assert (ev.end - ev.start).total_seconds() == FEED.default_duration_minutes * 60


def test_all_day_event_uses_dates_with_exclusive_end():
    ev = by_name(elfsight.parse_events(SETTINGS, FEED), "Can Release Day")
    assert ev.all_day
    assert ev.start == date(2026, 10, 3)
    assert ev.end == date(2026, 10, 4)


def test_recurring_event_maps_to_rrule_exdate_and_named_location():
    ev = by_name(elfsight.parse_events(SETTINGS, FEED), "Trivia Night & Tacos")  # entity unescaped
    assert ev.rrule["FREQ"] == "WEEKLY"
    assert ev.rrule["BYDAY"] == ["TU"]
    assert ev.rrule["UNTIL"].date() == date(2026, 12, 15)
    assert [x.date() for x in ev.exdates] == [date(2026, 11, 24)]
    assert ev.location == "The Patio, 42604 Trade West Dr, Sterling, VA"
    assert ev.description.startswith("Free to play.\nPrizes!")


def test_render_round_trips_and_has_bounded_vtimezone():
    events = elfsight.parse_events(SETTINGS, FEED)
    ics = ical.render(FEED, events)
    cal = Calendar.from_ical(ics)
    assert cal["X-WR-CALNAME"] == "Test Brewery"
    assert cal.get_missing_tzids() == set()
    vevents = cal.walk("VEVENT")
    assert len(vevents) == len(events)
    trivia = next(v for v in vevents if v["SUMMARY"] == "Trivia Night & Tacos")
    assert "RRULE" in trivia and "EXDATE" in trivia
    tz = cal.walk("VTIMEZONE")[0]
    assert "1970" not in str(tz["COMMENT"])   # bounded to the feed's date range, not 1970-2038


def test_filter_window_keeps_recent_future_and_recurring():
    events = elfsight.parse_events(SETTINGS, FEED)
    kept = ical.filter_window(events, keep_past_days=60, today=date(2026, 9, 18))
    names = {e.summary for e in kept}
    assert "Honorfest" in names and "Can Release Day" in names and "Trivia Night & Tacos" in names
    assert "3rd Annual Classic Lite Beer Mile" in names       # 2026-08-30, within 60 days
    assert "Honorfest Beer Dinner" not in names               # 2025-09-16, long past


def test_strip_volatile_makes_repeat_renders_equal():
    events = elfsight.parse_events(SETTINGS, FEED)
    a = ical.render(FEED, events, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    b = ical.render(FEED, events, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert a != b
    assert ical.strip_volatile(a) == ical.strip_volatile(b)

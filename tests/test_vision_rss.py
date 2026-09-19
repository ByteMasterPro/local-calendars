from datetime import date, datetime
from pathlib import Path

from localcal import ical
from localcal.model import Event, Feed
from localcal.sources import vision_rss

XML = (Path(__file__).parent / "fixtures" / "leesburg_calendar_rss.xml").read_text()
FEED = Feed(slug="lb", name="Leesburg", url="https://www.leesburgva.gov/residents/calendar", location="Leesburg, VA",
            timezone="America/New_York", kind="town", source={"type": "vision_rss", "rss_url": "x"}, accumulate=True)


def by_name(events, prefix):
    return next(e for e in events if e.summary.startswith(prefix))


def test_parses_every_item_and_strips_date_from_title():
    events = vision_rss.parse_events(XML, FEED)
    assert len(events) == 27
    assert all("(" not in e.summary[-12:] or "/" not in e.summary[-12:] for e in events)
    assert len({e.uid for e in events}) == 27


def test_start_and_end_times():
    ev = by_name(vision_rss.parse_events(XML, FEED), "Leesburg Airshow")
    assert ev.start.isoformat() == "2026-09-26T11:00:00-04:00"
    assert ev.end.isoformat() == "2026-09-26T16:00:00-04:00"
    assert ev.uid == "38727@lb"
    assert ev.url == "https://www.leesburgva.gov/Home/Components/Calendar/Event/38727/"
    assert "Details: https://www.leesburgva.gov/Home/Components/Calendar/Event/38727/" in ev.description


def test_start_only_gets_default_duration():
    ev = by_name(vision_rss.parse_events(XML, FEED), "Town Council Meeting")
    assert (ev.end - ev.start).total_seconds() == FEED.default_duration_minutes * 60


def test_date_range_is_all_day_span_with_exclusive_end():
    ev = by_name(vision_rss.parse_events(XML, FEED), "Exhibit by the Lincoln Preservation Society")
    assert ev.all_day and ev.start == date(2026, 10, 1) and ev.end == date(2026, 12, 1)


def test_title_with_parentheses_before_the_date_survives():
    ev = by_name(vision_rss.parse_events(XML, FEED), "Draft Zoning Ordinance Rewrite")
    assert ev.summary.endswith("& IX (Word Usage)")
    assert ev.end.isoformat() == "2026-09-21T23:00:00-04:00"


def test_round_trip_through_ics():
    events = vision_rss.parse_events(XML, FEED)
    back = ical.parse(ical.render(FEED, events))
    assert {e.uid for e in back} == {e.uid for e in events}
    a = by_name(back, "Leesburg Airshow")
    assert a.start == by_name(events, "Leesburg Airshow").start


def _ev(uid, day):
    return Event(uid=uid, summary=uid, start=datetime(2026, 10, day, 18, tzinfo=ical.timezone.utc),
                 end=datetime(2026, 10, day, 20, tzinfo=ical.timezone.utc))


def test_merge_accumulated_keeps_past_drops_cancelled_adds_new():
    today = date(2026, 10, 10)
    previous = [_ev("past", 1), _ev("cancelled", 12), _ev("still-there", 14)]
    fresh = [_ev("still-there", 14), _ev("new", 20)]          # horizon = Oct 20
    merged = {e.uid for e in ical.merge_accumulated(fresh, previous, today=today)}
    assert merged == {"past", "still-there", "new"}

from datetime import date

from icalendar import Calendar

from localcal.cli import occurrences
from localcal.model import Feed

FEED = Feed(slug="x", name="X", url="https://x", location="", timezone="America/New_York", kind="other",
            feed_url="https://x/feed.ics")

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:test
BEGIN:VEVENT
UID:weekly@x
DTSTART;TZID=America/New_York:20260901T190000
DTEND;TZID=America/New_York:20260901T210000
RRULE:FREQ=WEEKLY;BYDAY=TU
EXDATE;TZID=America/New_York:20260915T190000
SUMMARY:Trivia Night
END:VEVENT
BEGIN:VEVENT
UID:weekly@x
RECURRENCE-ID;TZID=America/New_York:20260922T190000
DTSTART;TZID=America/New_York:20260922T190000
DTEND;TZID=America/New_York:20260922T210000
SUMMARY:Trivia Night: Oktoberfest Edition
END:VEVENT
BEGIN:VEVENT
UID:allday@x
DTSTART;VALUE=DATE:20260926
DTEND;VALUE=DATE:20260927
SUMMARY:Fall Festival
END:VEVENT
END:VCALENDAR
"""


def test_expands_recurrence_honours_exdate_and_override():
    rows = occurrences(FEED, Calendar.from_ical(ICS), date(2026, 9, 7), date(2026, 9, 30))
    by_day = {r["start"][:10]: r["summary"] for r in rows if not r["all_day"]}
    assert by_day == {
        "2026-09-08": "Trivia Night",
        "2026-09-22": "Trivia Night: Oktoberfest Edition",   # RECURRENCE-ID override wins
        "2026-09-29": "Trivia Night",
    }                                                          # 09-15 removed by EXDATE
    allday = [r for r in rows if r["all_day"]]
    assert len(allday) == 1 and allday[0]["summary"] == "Fall Festival" and allday[0]["start"] == "2026-09-26"

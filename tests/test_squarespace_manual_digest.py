from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

from localcal import digest, ical
from localcal.model import Config, Feed
from localcal.sources import manual, squarespace

TZ = "America/New_York"


def feed(slug, kind, **kw):
    return Feed(slug=slug, name=slug.title(), url="https://x", location="", timezone=TZ, kind=kind, **kw)


# ---------------------------------------------------------------- squarespace

SQ = feed("sq", "town", source={"type": "squarespace", "url": "https://example.com/events"})


def ms(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() * 1000) + 686   # sub-second noise like the real API


def test_squarespace_single_event_and_link():
    payload = {"upcoming": [{"id": "abc", "title": "Movie Night", "fullUrl": "/events/movie", "excerpt": "<p>Bring a <b>chair</b>.</p>",
                              "startDate": ms(2026, 9, 25, 0), "endDate": ms(2026, 9, 25, 2),
                              "location": {"addressTitle": "One Loudoun", "addressLine1": "20387 Exchange St", "addressLine2": "Ashburn, VA"}}]}
    [ev] = squarespace.parse_events(payload, SQ)
    assert ev.uid == "abc@sq" and ev.url == "https://example.com/events/movie"
    assert ev.start.isoformat() == "2026-09-24T20:00:00-04:00" and ev.start.microsecond == 0
    assert ev.location == "One Loudoun, 20387 Exchange St, Ashburn, VA"
    assert ev.description.startswith("Bring a chair.")
    assert ev.rrule is None


def test_squarespace_months_long_same_weekday_span_becomes_weekly_series():
    # Thu May 7 7pm -> Thu Sep 24 8pm (EDT) is how Squarespace users enter "Zumba every Thursday"
    payload = {"upcoming": [{"id": "z", "title": "Zumba", "fullUrl": "/events/z", "startDate": ms(2026, 5, 7, 23), "endDate": ms(2026, 9, 25, 0)}]}
    [ev] = squarespace.parse_events(payload, SQ)
    assert ev.start.isoformat() == "2026-05-07T19:00:00-04:00"
    assert ev.end.isoformat() == "2026-05-07T20:00:00-04:00"
    assert ev.rrule["FREQ"] == "WEEKLY" and ev.rrule["BYDAY"] == ["TH"]
    # last occurrence survives the trip through .ics (UNTIL written in UTC)
    cal = __import__("icalendar").Calendar.from_ical(ical.render(SQ, [ev]))
    import recurring_ical_events
    days = [str(v["DTSTART"].dt)[:10] for v in recurring_ical_events.of(cal).between(date(2026, 9, 1), date(2026, 10, 1))]
    assert days == ["2026-09-03", "2026-09-10", "2026-09-17", "2026-09-24"]


def test_squarespace_two_day_festival_stays_a_span():
    payload = {"upcoming": [{"id": "f", "title": "Food Fest", "fullUrl": "/events/f", "startDate": ms(2026, 10, 31, 15), "endDate": ms(2026, 11, 1, 17)}]}
    [ev] = squarespace.parse_events(payload, SQ)
    assert ev.rrule is None and ev.end.date() == date(2026, 11, 1)


# --------------------------------------------------------------------- manual

MAN = feed("fairs", "festival", source={"type": "manual", "file": "x"})


def test_manual_all_day_range_timed_and_season():
    entries = [
        {"title": "Bluemont Fair", "start": date(2026, 9, 19), "end": date(2026, 9, 20), "url": "https://b", "tags": ["fair"]},
        {"title": "Oktoberfest", "start": "2026-09-26 10:00", "end": "2026-09-26 17:00", "tags": ["german"]},
        {"title": "Pumpkin Patch", "season": {"start": date(2026, 9, 24), "end": date(2026, 11, 3)}, "description": "Open daily."},
    ]
    fair, okt, patch = manual.parse_events(entries, MAN)
    assert fair.all_day and fair.start == date(2026, 9, 19) and fair.end == date(2026, 9, 21)   # inclusive -> exclusive
    assert fair.uid == "bluemont-fair-2026-09-19@fairs" and fair.categories == ["fair"] and "More info: https://b" in fair.description
    assert okt.start.isoformat() == "2026-09-26T10:00:00-04:00" and okt.end.hour == 17
    assert patch.all_day and patch.start == date(2026, 9, 26)      # first Sat on/after Sep 24
    assert patch.rrule["FREQ"] == "WEEKLY" and patch.rrule["BYDAY"] == ["SA", "SU"]
    assert patch.rrule["UNTIL"].date() == date(2026, 11, 3)


def test_repo_fairs_file_parses_and_is_all_verified():
    root = Path(__file__).resolve().parent.parent
    entries = manual.load(root / "config" / "events" / "fairs-festivals.yaml")
    events = manual.parse_events(entries, MAN)
    assert len(events) == len(entries) >= 8
    assert all(e.get("verified") for e in entries), "every curated event needs a `verified:` date"
    assert len({e.uid for e in events}) == len(events)


# --------------------------------------------------------------------- digest

def row(summary, start, end, *, calendar="Cal", slug=None, kind="brewery", uid=None, cats=(), url="https://x/e",
        location="", all_day=False, desc="", series=False, series_until=None):
    return {"calendar": calendar, "slug": slug or calendar.lower(), "kind": kind, "uid": uid or summary, "summary": summary,
            "start": start, "end": end, "all_day": all_day, "location": location, "url": url, "description": desc,
            "categories": list(cats), "series": series, "series_until": series_until,
            "_sort": (lambda d: d if d.tzinfo else d.replace(tzinfo=timezone.utc))(datetime.fromisoformat(start))}


FEEDS = {
    "vanish": feed("vanish", "brewery", feed_url="https://v/ics", short_name="Vanish", town="Leesburg"),
    "chilly": feed("chilly", "brewery", feed_url="https://c/ics", short_name="Chilly Hollow", town="Berryville"),
    "fairs": feed("fairs", "festival", source={"type": "manual", "file": "x"}),
    "town": feed("town", "town", feed_url="https://t/ics", short_name="Town of Leesburg", town="Leesburg"),
}
CFG = Config(site={"base_url": "https://idx"}, feeds=list(FEEDS.values()), digest={
    "title": "T", "preview_limit": 5,
    "sections": {
        "fairs": {"kinds": ["festival"], "limit": 8},
        "breweries": {"kinds": ["brewery"], "limit": 8, "fill_below": 3,
                      "exclude": "karaoke|trivia|% off",
                      "seasonal": [{"months": [9, 10], "pattern": "oktober|german|prost|fest\\b"},
                                   {"months": [11, 12], "pattern": "christmas|holiday|santa"}],
                      "fallback": "live music|music"},
        "towns": {"kinds": ["town"], "limit": 2, "exclude": "council", "prioritize": "movie|parade",
                  "farmers_markets": {"label": "Farmers Markets", "pattern": "farmers market"}},
    }})
MON = date(2026, 9, 21)
SAT = date(2026, 9, 19)
NOON_SAT = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
W_MON = digest.week_window(MON, datetime(2026, 9, 21, 11, tzinfo=timezone.utc))
W_SAT = digest.week_window(SAT, NOON_SAT)


def test_week_window_is_monday_to_sunday_from_run_day():
    assert (W_MON.start, W_MON.end, W_MON.next_start, W_MON.next_end) == (MON, date(2026, 9, 27), date(2026, 9, 28), date(2026, 10, 4))
    assert (W_SAT.start, W_SAT.end, W_SAT.next_start, W_SAT.next_end) == (SAT, date(2026, 9, 20), MON, date(2026, 9, 27))


def test_format_line_matches_christophers_example():
    r = row("PROST! German Experience", "2026-09-20T13:00:00-04:00", "2026-09-20T16:00:00-04:00", calendar="vanish",
            url="https://vanishbeer.com/event/prost", desc="Prost! Rockville German Band was founded in 2010. The band performs polkas.\n\nEvent listing: https://vanishbeer.com")
    line = digest.format_line(r, SAT, FEEDS)
    assert line == ("**Sun Sep 20**, 1–4pm — [PROST! German Experience](https://vanishbeer.com/event/prost) (Vanish, Leesburg): "
                    "Prost! Rockville German Band was founded in 2010. The band performs polkas. Tomorrow.")


def test_format_line_ranges_all_day_and_link_fallback():
    r = row("2nd Annual Chillyfest", "2026-10-02", "2026-10-05", calendar="chilly", all_day=True, url="")
    assert digest.format_line(r, MON, FEEDS) == "**Fri Oct 2 – Sun Oct 4** — [2nd Annual Chillyfest](https://x) (Chilly Hollow, Berryville)"
    r = row("Honorfest", "2026-09-26T11:00:00-04:00", "2026-09-26T23:00:00-04:00", calendar="honor", slug="honor",
            location="Honor Brewing - Loudoun, 42604 Trade West Dr, Sterling, VA 20166", url="https://h")
    assert digest.format_line(r, MON, FEEDS).startswith("**Sat Sep 26**, 11am–11pm — [Honorfest](https://h) (Honor Brewing - Loudoun, Sterling)")


BREW = [
    row("Karaoke Oktoberfest Night", "2026-09-22T19:00:00-04:00", "2026-09-22T21:00:00-04:00", calendar="chilly"),   # excluded
    row("Beer Release: Oktoberfest", "2026-09-24", "2026-09-25", calendar="chilly", all_day=True),
    row("Live Music: Someone", "2026-09-25T17:00:00-04:00", "2026-09-25T20:00:00-04:00", calendar="chilly"),
    row("Honorfest", "2026-09-26T11:00:00-04:00", "2026-09-26T23:00:00-04:00", calendar="vanish"),
    row("Oktoberfest Brunch", "2026-09-27T12:00:00-04:00", "2026-09-27T14:30:00-04:00", calendar="vanish"),
    row("20% Off Beer", "2026-09-27T12:00:00-04:00", "2026-09-27T20:00:00-04:00", calendar="vanish"),               # excluded
    row("Chillyfest", "2026-10-02", "2026-10-05", calendar="chilly", all_day=True),                                # next week
    row("Live Music: Next Week Band", "2026-10-03T17:00:00-04:00", "2026-10-03T20:00:00-04:00", calendar="chilly"),  # next week, not previewed
]


def titles(lines):
    return [l.split("[")[1].split("]")[0] for l in lines if l and "[" in l and not l.startswith("**Next week")]


def nonblank(lines):
    return [l for l in lines if l]


def test_breweries_this_week_seasonal_then_seasonal_only_preview():
    c = CFG.digest["sections"]["breweries"]
    lines = digest.breweries_lines(BREW, c, W_MON, FEEDS)
    assert titles(lines) == ["Beer Release: Oktoberfest", "Honorfest", "Oktoberfest Brunch"]      # 3 seasonal -> no music fill
    assert lines[-2] == "" and lines[-1] == "**Next week (Mon Sep 28 – Sun Oct 4):** [Chillyfest](https://x/e) (Fri)"   # blank line, then seasonal-only preview


def test_breweries_music_fills_when_season_is_thin():
    c = CFG.digest["sections"]["breweries"]
    thin = [r for r in BREW if r["summary"] not in ("Honorfest", "Oktoberfest Brunch")]
    assert titles(digest.breweries_lines(thin, c, W_MON, FEEDS)) == ["Beer Release: Oktoberfest", "Live Music: Someone"]


def test_breweries_holiday_pattern_in_december():
    rows = [row("Ugly Sweater Christmas Party", "2026-12-05T18:00:00-05:00", "2026-12-05T21:00:00-05:00", calendar="vanish"),
            row("Oktoberfest Leftovers", "2026-12-06T18:00:00-05:00", "2026-12-06T21:00:00-05:00", calendar="vanish")]
    w = digest.week_window(date(2026, 11, 30), datetime(2026, 11, 30, 11, tzinfo=timezone.utc))
    lines = digest.breweries_lines(rows, CFG.digest["sections"]["breweries"], w, FEEDS)
    assert len(lines) == 1 and "Christmas" in lines[0]


def test_saturday_run_drops_finished_events_and_keeps_sunday():
    rows = [row("Morning Yoga Brew", "2026-09-19T09:00:00-04:00", "2026-09-19T10:00:00-04:00", calendar="vanish", desc="live music"),
            row("Afternoon Band", "2026-09-19T13:00:00-04:00", "2026-09-19T16:00:00-04:00", calendar="vanish", desc="live music"),
            row("PROST German Experience", "2026-09-20T13:00:00-04:00", "2026-09-20T16:00:00-04:00", calendar="vanish")]
    w = digest.week_window(SAT, datetime(2026, 9, 19, 11, 30, tzinfo=ZoneInfo("America/New_York")))
    got = titles(digest.breweries_lines(rows, CFG.digest["sections"]["breweries"], w, FEEDS))
    assert got == ["Afternoon Band", "PROST German Experience"]           # 9-10am is over; Sunday still shows


def test_fairs_this_week_ongoing_and_preview_dedupe():
    rows = [row("Bluemont Fair", "2026-09-19", "2026-09-21", calendar="fairs", kind="festival", all_day=True),
            row("State Fair", "2026-09-25", "2026-10-05", calendar="fairs", kind="festival", all_day=True),          # this week AND next
            row("Waterford Fair", "2026-10-02", "2026-10-05", calendar="fairs", kind="festival", all_day=True),      # next week
            row("Cox Farms Fall Festival", "2026-09-26", "2026-09-27", calendar="fairs", kind="festival", all_day=True, series=True, series_until="2026-11-08"),
            row("Cox Farms Fall Festival", "2026-09-27", "2026-09-28", calendar="fairs", kind="festival", all_day=True, series=True, series_until="2026-11-08"),
            row("Cox Farms Fall Festival", "2026-10-03", "2026-10-04", calendar="fairs", kind="festival", all_day=True, series=True, series_until="2026-11-08")]
    lines = nonblank(digest.fairs_lines(rows, CFG.digest["sections"]["fairs"], W_MON, FEEDS))
    assert titles(lines[:1]) == ["State Fair"]                                             # Bluemont was last week
    assert lines[1] == "**Ongoing weekends:** [Cox Farms Fall Festival](https://x/e) thru Sun Nov 8"
    assert lines[2] == "**Next week (Mon Sep 28 – Sun Oct 4):** [Waterford Fair](https://x/e) (Fri)"   # State Fair not repeated


def test_towns_prioritised_capped_markets_subsection_and_preview():
    rows = [row("Town Council Meeting", "2026-09-22T19:00:00-04:00", "2026-09-22T21:00:00-04:00", calendar="town", kind="town", desc="council"),
            row("Zumba", "2026-09-22T19:00:00-04:00", "2026-09-22T20:00:00-04:00", calendar="town", kind="town"),
            row("Movie Night", "2026-09-24T20:00:00-04:00", "2026-09-24T22:00:00-04:00", calendar="town", kind="town"),
            row("Halloween Parade", "2026-09-26T10:00:00-04:00", "2026-09-26T12:00:00-04:00", calendar="town", kind="town"),
            row("Leesburg Farmers Market", "2026-09-26T08:00:00-04:00", "2026-09-26T12:00:00-04:00", calendar="town", kind="town",
                location="Virginia Village, 30 Catoctin Cir, Leesburg, VA 20175"),
            row("Fall Jubilee", "2026-10-03T10:00:00-04:00", "2026-10-03T17:00:00-04:00", calendar="town", kind="town")]
    raw = digest.towns_lines(rows, CFG.digest["sections"]["towns"], W_MON, FEEDS)
    assert raw[2] == "" and raw[-2] == ""                                                  # blank lines before sub-list and preview
    lines = nonblank(raw)
    assert titles(lines[:2]) == ["Movie Night", "Halloween Parade"]                        # limit 2, prioritised, chronological
    assert lines[2] == "🥕 **Farmers Markets:**"
    assert lines[3] == "[Leesburg Farmers Market](https://x/e) — Sat 8am–12pm (Town of Leesburg)"   # feed short_name wins
    assert lines[4] == "**Next week (Mon Sep 28 – Sun Oct 4):** [Fall Jubilee](https://x/e) (Sat)"


def test_build_and_payloads(monkeypatch):
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: ([], 0))
    d = digest.build(CFG, list(FEEDS.values()), SAT, now=NOON_SAT)
    assert [s.label for s in d.sections] == ["Fairs, Festivals and Carnivals", "Local Breweries", "Town Activities"]
    assert (d.start, d.end, d.next_start, d.next_end) == (SAT, date(2026, 9, 20), MON, date(2026, 9, 27))
    payloads = digest.discord_payloads(d)
    assert payloads[0]["content"].startswith("📅 **T** — Sat Sep 19 to Sun Sep 20 (the rest of this week)")
    assert [p["embeds"][0]["title"] for p in payloads[1:]] == ["🎪 Fairs, Festivals and Carnivals", "🍺 Local Breweries", "🏘️ Town Activities"]


def test_cli_digest_post_path_without_webhook_returns_2(monkeypatch, capsys):
    from localcal import cli
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: ([], 0))
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert cli.digest(CFG, list(FEEDS.values()), MON, post=True) == 2
    assert "T — Mon Sep 21" in capsys.readouterr().out


def test_cli_digest_post_path_posts_each_payload(monkeypatch):
    from localcal import cli
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: ([], 0))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    sent = []
    monkeypatch.setattr(digest, "_post", lambda url, payload: sent.append((url, payload)))
    assert cli.digest(CFG, list(FEEDS.values()), MON, post=True) == 0
    assert len(sent) == 4 and all(u == "https://discord.test/hook" for u, _ in sent)


def test_manual_timed_season_becomes_weekly_timed_series():
    [ev] = manual.parse_events([{"title": "Leesburg Saturday Farmers Market",
                                 "season": {"start": date(2026, 5, 2), "end": date(2026, 10, 31), "days": ["SA"], "time": "08:00-12:00"}}], MAN)
    assert not ev.all_day and ev.start.isoformat() == "2026-05-02T08:00:00-04:00" and ev.end.hour == 12
    assert ev.rrule["BYDAY"] == ["SA"] and ev.rrule["UNTIL"].date() == date(2026, 10, 31)

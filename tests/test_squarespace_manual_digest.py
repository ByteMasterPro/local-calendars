from datetime import date, datetime, timezone
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

def row(summary, start, end, *, calendar="Cal", kind="brewery", uid=None, cats=(), url="https://x/e", location="", all_day=False, desc=""):
    return {"calendar": calendar, "slug": calendar.lower(), "kind": kind, "uid": uid or summary, "summary": summary,
            "start": start, "end": end, "all_day": all_day, "location": location, "url": url, "description": desc,
            "categories": list(cats), "_sort": datetime.fromisoformat(start)}


def test_format_rows_collapses_repeats_and_formats_spans():
    rows = [
        row("Cox Farms Fall Festival", "2026-09-26", "2026-09-27", all_day=True, location="Cox Farms, 15621 Braddock Rd"),
        row("Cox Farms Fall Festival", "2026-09-27", "2026-09-28", all_day=True, location="Cox Farms, 15621 Braddock Rd"),
        row("Hairspray", "2026-09-25T19:00:00-04:00", "2026-09-25T21:30:00-04:00", uid="h1", location="Hylton Performing Arts Center @ 10960 George Mason Circle"),
        row("Hairspray", "2026-09-26T19:00:00-04:00", "2026-09-26T21:30:00-04:00", uid="h2", location="Hylton Performing Arts Center @ 10960 George Mason Circle"),
        row("State Fair", "2026-09-25", "2026-10-05", all_day=True, location="Meadow Event Park, Doswell"),
        row("Trivia", "2026-09-22T18:30:00-04:00", "2026-09-22T21:00:00-04:00", location="42615 Trade W Dr, Sterling", calendar="Solace"),
    ]
    lines = digest.format_rows(rows)
    assert len(lines) == 4
    assert lines[0] == "**Sat Sep 26** · [Cox Farms Fall Festival](https://x/e) — Cox Farms · also Sun"
    assert lines[1].startswith("**Fri Sep 25** 7pm-9:30pm · [Hairspray](https://x/e) — Hylton Performing Arts Center · also Sat")
    assert "**Fri Sep 25** thru Oct 4 · [State Fair](https://x/e) — Meadow Event Park" in lines[2]
    assert lines[3].endswith("— Solace")            # bare street address falls back to the calendar name


def test_build_routes_rows_into_sections(monkeypatch):
    cfg = Config(site={"base_url": "https://idx"}, feeds=[], digest={
        "title": "T", "recommended": {"pattern": "oktober|german"},
        "family": {"kinds": ["festival"], "pattern": "movie night", "exclude": "council"}})
    rows = [
        row("Lovettsville Oktoberfest", "2026-09-26T10:00:00-04:00", "2026-09-26T17:00:00-04:00", kind="festival", cats=("german",)),
        row("Bluemont Fair", "2026-09-19", "2026-09-21", kind="festival", all_day=True),
        row("Movie Night", "2026-09-24T20:00:00-04:00", "2026-09-24T22:00:00-04:00", kind="town"),
        row("Town Council Meeting", "2026-09-22T19:00:00-04:00", "2026-09-22T21:00:00-04:00", kind="town", desc="council"),
        row("Live Music: Band", "2026-09-19T18:00:00-04:00", "2026-09-19T21:00:00-04:00", kind="brewery"),
        row("Summer at the Museum", "2026-08-20T08:00:00-04:00", "2026-10-22T17:00:00-04:00", kind="festival"),   # ongoing since before window
    ]
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: (rows, 0))
    d = digest.build(cfg, [], date(2026, 9, 19), 7)
    rec, fam = d.sections
    assert [l.split("[")[1].split("]")[0] for l in rec.lines] == ["Lovettsville Oktoberfest"]
    assert [l.split("[")[1].split("]")[0] for l in fam.lines] == ["Bluemont Fair", "Movie Night"]
    payloads = digest.discord_payloads(d)
    assert payloads[0]["content"].startswith("📅 **T** — Sat Sep 19 to Fri Sep 25")
    assert [p["embeds"][0]["title"] for p in payloads[1:]] == ["⭐ Recommended For You", "🎪 Other Family Events"]
    assert "Sat Sep 19 to Fri Sep 25" in digest.render_text(d)


def test_cli_digest_post_path_without_webhook_returns_2(monkeypatch, capsys):
    from localcal import cli
    cfg = Config(site={"base_url": "https://idx"}, feeds=[], digest={"title": "T"})
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: ([], 0))
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert cli.digest(cfg, [], date(2026, 9, 21), 7, post=True) == 2
    assert "T — Mon Sep 21" in capsys.readouterr().out


def test_cli_digest_post_path_posts_each_payload(monkeypatch):
    from localcal import cli
    cfg = Config(site={"base_url": "https://idx"}, feeds=[], digest={"title": "T"})
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: ([], 0))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    sent = []
    monkeypatch.setattr(digest, "_post", lambda url, payload: sent.append((url, payload)))
    assert cli.digest(cfg, [], date(2026, 9, 21), 7, post=True) == 0
    assert len(sent) == 3 and all(u == "https://discord.test/hook" for u, _ in sent)

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


PROST = row("PROST! German Experience", "2026-09-20T13:00:00-04:00", "2026-09-20T16:00:00-04:00", calendar="vanish",
            url="https://vanishbeer.com/event/prost", desc="Prost! Rockville German Band was founded in 2010. The band performs polkas.\n\nEvent listing: https://vanishbeer.com")


def test_pick_lines_groups_same_day_picks_under_one_date_header():
    okt = row("Lovettsville Oktoberfest", "2026-09-26T10:00:00-04:00", "2026-09-26T17:00:00-04:00", calendar="fairs", kind="festival",
              location="Zoldos Square, Lovettsville, VA 20180", url="https://lov", desc="German food and beer, stein hauling.")
    honor = row("Honorfest", "2026-09-26T11:00:00-04:00", "2026-09-26T23:00:00-04:00", calendar="honor", slug="honor",
                location="Honor Brewing - Loudoun, 42604 Trade West Dr, Sterling, VA 20166", url="https://h", desc="Raise a Stein!")
    assert digest.pick_lines([okt, honor], W_MON, FEEDS) == [
        "**Sat Sep 26**",
        "[Lovettsville Oktoberfest](https://lov) · 10am–5pm (Zoldos Square, Lovettsville)",
        "> German food and beer, stein hauling.",
        "",
        "[Honorfest](https://h) · 11am–11pm (Honor Brewing - Loudoun, Sterling)",
        "> Raise a Stein!",
    ]


def test_pick_lines_keeps_spans_and_lone_all_day_events_on_one_line():
    span = row("State Fair", "2026-09-25", "2026-10-05", calendar="fairs", kind="festival", all_day=True, url="https://sf", desc="Rides and midway.")
    release = row("Beer Release: Oktoberfest", "2026-09-24", "2026-09-25", calendar="chilly", all_day=True, url="")
    timed = row("Honorfest", "2026-09-26T11:00:00-04:00", "2026-09-26T23:00:00-04:00", calendar="vanish", url="https://h")
    assert digest.pick_lines([release, span, timed], W_MON, FEEDS) == [
        "**Thu Sep 24** — [Beer Release: Oktoberfest](https://x) (Chilly Hollow, Berryville)",
        "",
        "**Fri Sep 25 – Sun Oct 4** — [State Fair](https://sf) (Fairs)",
        "> Rides and midway.",
        "",
        "**Sat Sep 26**",
        "[Honorfest](https://h) · 11am–11pm (Vanish, Leesburg)",
    ]


def test_pick_lines_all_day_event_joins_a_day_that_has_timed_picks():
    allday = row("Beer Release", "2026-09-26", "2026-09-27", calendar="chilly", all_day=True, url="https://b")
    timed = row("Honorfest", "2026-09-26T11:00:00-04:00", "2026-09-26T23:00:00-04:00", calendar="vanish", url="https://h")
    lines = digest.pick_lines([allday, timed], W_MON, FEEDS)
    assert lines[0] == "**Sat Sep 26**" and lines.count("**Sat Sep 26**") == 1      # date printed once
    assert lines[1] == "[Beer Release](https://b) (Chilly Hollow, Berryville)"       # no time for an all-day pick
    assert lines[2] == "" and lines[3] == "[Honorfest](https://h) · 11am–11pm (Vanish, Leesburg)"


def test_pick_lines_today_and_tomorrow_sit_on_the_date_line():
    prost = row("PROST! German Experience", "2026-09-20T13:00:00-04:00", "2026-09-20T16:00:00-04:00", calendar="vanish",
                url="https://p", desc="Polkas and waltzes.")
    assert digest.pick_lines([prost], W_SAT, FEEDS) == [
        "**Sun Sep 20** · Tomorrow",
        "[PROST! German Experience](https://p) · 1–4pm (Vanish, Leesburg)",
        "> Polkas and waltzes.",
    ]


def test_grouped_lines_date_header_then_bullets():
    honor = row("Honorfest", "2026-09-26T11:00:00-04:00", "2026-09-26T23:00:00-04:00", calendar="honor", slug="honor",
                location="Honor Brewing - Loudoun, 42604 Trade West Dr, Sterling, VA 20166", url="https://h", desc="Raise a Stein!")
    brunch = row("Oktoberfest Brunch", "2026-09-27T12:00:00-04:00", "2026-09-27T14:30:00-04:00", calendar="vanish")
    release = row("Beer Release: Oktoberfest", "2026-09-24", "2026-09-25", calendar="chilly", all_day=True, url="")
    lines = digest.grouped_lines([brunch, honor, release], W_MON, FEEDS)
    assert lines == [
        "**Thu Sep 24**",
        "- [Beer Release: Oktoberfest](https://x) (Chilly Hollow, Berryville)",
        "\u200b\n**Sat Sep 26**",
        "- 11am–11pm — [Honorfest](https://h) (Honor Brewing - Loudoun, Sterling): Raise a Stein!",
        "\u200b\n**Sun Sep 27**",
        "- 12–2:30pm — [Oktoberfest Brunch](https://x/e) (Vanish, Leesburg)",
    ]


def test_grouped_lines_span_sits_under_first_visible_day_with_thru():
    fair = row("State Fair", "2026-09-25", "2026-10-05", calendar="fairs", kind="festival", all_day=True)
    lines = digest.grouped_lines([fair], W_MON, FEEDS)
    assert lines == ["**Fri Sep 25**", "- thru Sun Oct 4 — [State Fair](https://x/e) (Fairs)"]
    w2 = digest.week_window(date(2026, 9, 28), datetime(2026, 9, 28, 11, tzinfo=timezone.utc))
    assert digest.grouped_lines([fair], w2, FEEDS)[0] == "**Mon Sep 28** · Today"      # already running: under the run day


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


def names(rows):
    return [r["summary"] for r in rows]


def test_breweries_select_seasonal_then_seasonal_only_preview():
    B = digest.breweries_select(BREW, CFG.digest["sections"]["breweries"], W_MON)
    assert names(B["this"]) == ["Beer Release: Oktoberfest", "Honorfest", "Oktoberfest Brunch"]   # 3 seasonal -> no music fill
    assert names(B["seasonal"]) == names(B["this"])
    assert names(B["next"]) == ["Chillyfest"]                                                     # seasonal only in preview


def test_breweries_music_fills_when_season_is_thin():
    thin = [r for r in BREW if r["summary"] not in ("Honorfest", "Oktoberfest Brunch")]
    B = digest.breweries_select(thin, CFG.digest["sections"]["breweries"], W_MON)
    assert names(B["this"]) == ["Beer Release: Oktoberfest", "Live Music: Someone"]
    assert names(B["seasonal"]) == ["Beer Release: Oktoberfest"]                                 # music is not a top-pick candidate


def test_breweries_holiday_pattern_in_december():
    rows = [row("Ugly Sweater Christmas Party", "2026-12-05T18:00:00-05:00", "2026-12-05T21:00:00-05:00", calendar="vanish"),
            row("Oktoberfest Leftovers", "2026-12-06T18:00:00-05:00", "2026-12-06T21:00:00-05:00", calendar="vanish")]
    w = digest.week_window(date(2026, 11, 30), datetime(2026, 11, 30, 11, tzinfo=timezone.utc))
    assert names(digest.breweries_select(rows, CFG.digest["sections"]["breweries"], w)["this"]) == ["Ugly Sweater Christmas Party"]


def test_saturday_run_drops_finished_events_and_keeps_sunday():
    rows = [row("Morning Yoga Brew", "2026-09-19T09:00:00-04:00", "2026-09-19T10:00:00-04:00", calendar="vanish", desc="live music"),
            row("Afternoon Band", "2026-09-19T13:00:00-04:00", "2026-09-19T16:00:00-04:00", calendar="vanish", desc="live music"),
            PROST]
    w = digest.week_window(SAT, datetime(2026, 9, 19, 11, 30, tzinfo=ZoneInfo("America/New_York")))
    assert names(digest.breweries_select(rows, CFG.digest["sections"]["breweries"], w)["this"]) == ["Afternoon Band", "PROST! German Experience"]


def test_fairs_select_this_week_ongoing_and_preview_dedupe():
    rows = [row("Bluemont Fair", "2026-09-19", "2026-09-21", calendar="fairs", kind="festival", all_day=True),
            row("State Fair", "2026-09-25", "2026-10-05", calendar="fairs", kind="festival", all_day=True),          # this week AND next
            row("Waterford Fair", "2026-10-02", "2026-10-05", calendar="fairs", kind="festival", all_day=True),      # next week
            row("Cox Farms Fall Festival", "2026-09-26", "2026-09-27", calendar="fairs", kind="festival", all_day=True, series=True, series_until="2026-11-08"),
            row("Cox Farms Fall Festival", "2026-09-27", "2026-09-28", calendar="fairs", kind="festival", all_day=True, series=True, series_until="2026-11-08"),
            row("Cox Farms Fall Festival", "2026-10-03", "2026-10-04", calendar="fairs", kind="festival", all_day=True, series=True, series_until="2026-11-08")]
    F = digest.fairs_select(rows, CFG.digest["sections"]["fairs"], W_MON)
    assert names(F["this"]) == ["State Fair"]                       # Bluemont was last week
    assert names(F["ongoing"]) == ["Cox Farms Fall Festival"]
    assert names(F["next"]) == ["Waterford Fair"]                   # State Fair not repeated in the preview


def test_towns_select_prioritised_capped_markets_and_preview():
    rows = [row("Town Council Meeting", "2026-09-22T19:00:00-04:00", "2026-09-22T21:00:00-04:00", calendar="town", kind="town", desc="council"),
            row("Zumba", "2026-09-22T19:00:00-04:00", "2026-09-22T20:00:00-04:00", calendar="town", kind="town"),
            row("Movie Night", "2026-09-24T20:00:00-04:00", "2026-09-24T22:00:00-04:00", calendar="town", kind="town"),
            row("Halloween Parade", "2026-09-26T10:00:00-04:00", "2026-09-26T12:00:00-04:00", calendar="town", kind="town"),
            row("Leesburg Farmers Market", "2026-09-26T08:00:00-04:00", "2026-09-26T12:00:00-04:00", calendar="town", kind="town"),
            row("Fall Jubilee", "2026-10-03T10:00:00-04:00", "2026-10-03T17:00:00-04:00", calendar="town", kind="town")]
    T = digest.towns_select(rows, CFG.digest["sections"]["towns"], W_MON)
    assert names(T["this"]) == ["Movie Night", "Halloween Parade"]   # limit 2, prioritised, chronological
    assert names(T["markets"]) == ["Leesburg Farmers Market"]
    assert names(T["next"]) == ["Fall Jubilee"]


def test_top_picks_union_chronological_capped():
    F = {"this": [row("State Fair", "2026-09-25", "2026-10-05", calendar="fairs", kind="festival", all_day=True)]}
    B = {"seasonal": [row("Honorfest", "2026-09-26T11:00:00-04:00", "2026-09-26T23:00:00-04:00", calendar="vanish"),
                      row("Beer Release: Oktoberfest", "2026-09-24", "2026-09-25", calendar="chilly", all_day=True)]}
    T = {"this": [row("Leesburg Airshow", "2026-09-26T11:00:00-04:00", "2026-09-26T16:00:00-04:00", calendar="town", kind="town"),
                  row("Zumba", "2026-09-22T19:00:00-04:00", "2026-09-22T20:00:00-04:00", calendar="town", kind="town")]}
    picks = digest.top_picks(F, B, T, {"pattern": "air ?show|parade", "limit": 3}, W_MON)
    assert names(picks) == ["Beer Release: Oktoberfest", "State Fair", "Honorfest"]      # Zumba never; Airshow cut by limit


def test_build_and_payloads(monkeypatch):
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: ([], 0))
    d = digest.build(CFG, list(FEEDS.values()), SAT, now=NOON_SAT)
    assert [s.label for s in d.sections] == ["Fairs, Festivals and Carnivals", "Local Breweries", "Town Activities"]   # no picks -> no card
    assert (d.start, d.end, d.next_start, d.next_end) == (SAT, date(2026, 9, 20), MON, date(2026, 9, 27))
    payloads = digest.discord_payloads(d)
    assert payloads[0]["content"].startswith("📅 **T** — Sat Sep 19 to Sun Sep 20 (the rest of this week)")
    assert [p["embeds"][0]["title"] for p in payloads[1:]] == ["🎪 Fairs, Festivals and Carnivals", "🍺 Local Breweries", "🏘️ Town Activities"]


def test_build_with_picks_puts_top_picks_first(monkeypatch):
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: (BREW, 0))
    cfg = Config(site={}, feeds=list(FEEDS.values()), digest={**CFG.digest, "top_picks": {"limit": 5, "pattern": "parade"}})
    d = digest.build(cfg, list(FEEDS.values()), MON, now=datetime(2026, 9, 21, 11, tzinfo=timezone.utc))
    assert d.sections[0].label == "Top picks this week"
    assert d.sections[0].lines[0].startswith("**Thu Sep 24** — [Beer Release: Oktoberfest]")
    assert any("Honorfest" in l for l in d.sections[0].lines) and any("Honorfest" in l for l in d.sections[2].lines)   # repeated on purpose


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

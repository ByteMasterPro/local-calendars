# CLAUDE.md - Local Calendars

Christopher's subscribable local event calendars (breweries, towns, community groups near
Leesburg, VA 20176). Separate from JobHunt; lives at `~/Projects/LocalCalendars`, published from
GitHub repo `ByteMasterPro/local-calendars` via GitHub Pages.

## What it does

Daily GitHub Actions cron -> `uv run localcal build` -> fetch each source -> `docs/<slug>.ics` ->
commit -> GitHub Pages serves it. Christopher subscribes to the URLs in Apple Calendar / Google
Calendar. Venues that already publish a feed are linked, not mirrored.

## Answering "what's coming up?" questions

Christopher asks things like "what October or German events are coming up in the next 30 days?"
right here in chat. The recipe:

```bash
cd ~/Projects/LocalCalendars
uv run localcal upcoming --days 30                          # everything
uv run localcal upcoming --days 30 --grep "oktober|german|stein|fest|lager|bavarian"
uv run localcal upcoming --days 30 --json                   # when you want to filter/summarise yourself
```

Then summarise for him: group by date, name the venue, drop noise (weekly farmers markets,
council meetings) unless asked. Regex is a retrieval net, not the answer; read the titles and
descriptions and use judgement (e.g. "Chillyfest" is an Oktoberfest-style event even though
"german" isn't in the title). Note Leesburg's feed only sees ~2 weeks ahead.

## Conventions

- **Config-first.** A new calendar on a known platform is a `config/calendars.yaml` entry.
  New platforms get a small adapter in `localcal/sources/` registered in `sources/__init__.py`.
- **Structured data over scraping.** Look for the widget/platform API first: Elfsight boot JSON,
  a public Google Calendar behind an Elfsight widget, Vision CMS RSS, The Events Calendar
  `?ical=1`, CivicPlus `iCalendar.aspx`, Squarespace `?format=json`. Every current source was
  found this way; none scrape rendered HTML.
- **Link, don't mirror, feeds that already exist** (`feed_url`). Mirroring adds lag and loses
  RECURRENCE-ID overrides.
- **Stable UIDs** (`<source id>@<slug>`) so subscribers see edits, not duplicates.
- **Never publish an empty feed on a fetch failure.** `build` leaves the existing `.ics` alone
  and exits non-zero so the Actions run shows red.
- **Local times with TZID** (not UTC) so recurring events survive DST. VTIMEZONE is bounded.
- Prefer simple/direct; no over-engineering. Tests are fixture-based (`tests/fixtures/`).

## Run

```bash
uv run localcal upcoming [--days N] [--grep RX] [--only slug,slug] [--json]
uv run localcal build [--only SLUG] [--dry-run]
uv run pytest
```

## Source notes and landmines

- **Honor Brewing** (Elfsight widget `5cfe5396-...`): a "Commanders Watch Party" is entered as
  04:25-07:25 AM (their typo). We pass source data through as-is; do not "fix" upstream data.
  Fairfax location widget `620fc0b1-b6f5-4814-a54a-33d51c45bfe8` is commented out in config.
- **Chilly Hollow**: elf.site widget is fed by a public Google Calendar; we link to that.
- **Leesburg** (leesburgva.gov, Granicus/Vision CMS): HTML is behind Akamai Bot Manager (403 for
  curl, `bm-verify` challenges even in a real browser on deep links). The RSS endpoint
  `/Home/Components/RssFeeds/RssFeed/View?ctID=6&cateIDs=...` is NOT blocked. ctID=5 is News,
  6 is Calendar. Category ids are in the `<select name="eventcats_...">` on /residents/calendar.
  Horizon is ~2 weeks and no query parameter widens it, hence `accumulate: true`.
- **Historic Manassas** (WordPress + The Events Calendar): `?ical=1` works.
- **Loudoun County** (loudoun.gov, CivicPlus): iCal exists at
  `/common/modules/iCalendar/iCalendar.aspx?catID=34&feed=calendar` but "County Events" had 4
  items; not worth adding as of 2026-09-19. Sterling, Ashburn and Aldie are unincorporated, so
  there is no town calendar for them; candidates are Visit Loudoun (visitloudoun.org, Simpleview
  CMS with a JSON API) and individual breweries.
- **City of Manassas** (manassasva.gov): Revize CMS calendar at /calendar.php, no feed found yet.

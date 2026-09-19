# CLAUDE.md - Brewery Calendars

Christopher's subscribable brewery event calendars. Separate from JobHunt; lives at
`~/Projects/BreweryCalendar`, published from GitHub repo `ByteMasterPro/brewery-calendars`.

## What it does

Weekly GitHub Actions cron -> `uv run brewcal build` -> fetch each brewery's event data ->
`docs/<slug>.ics` -> commit -> GitHub Pages serves it. Christopher subscribes to the URL in
Apple Calendar and Google Calendar (he already subscribes to Solace, Flying Ace, Vanish feeds the
same way; this fills the gap for breweries with no feed).

## Conventions

- **Config-first.** A new brewery on a known platform is a `config/breweries.yaml` entry only.
  New platforms get a small adapter in `brewcal/sources/` registered in `sources/__init__.py`.
- **Link, don't mirror, feeds that already exist.** Chilly Hollow's Elfsight widget is fed by a
  public Google Calendar; the config uses `feed_url` and the index links straight to Google's
  `.ics`. A weekly mirror would only add lag and lose RECURRENCE-ID overrides.
- **Structured data over scraping.** Honor Brewing uses an Elfsight widget whose JSON boot
  endpoint has everything. Before writing an HTML scraper for a new brewery, look for the
  widget/platform API first (Elfsight, Squarespace `?format=json`, The Events Calendar
  `?ical=1`, Eventbrite, Untappd, Facebook events, etc.).
- **Stable UIDs** (`<source id>@<slug>`) so subscribers see edits, not duplicates.
- **Never publish an empty feed on a fetch failure.** `build` leaves the existing `.ics` alone
  and exits non-zero so the Actions run shows red.
- **Local times with TZID** (not UTC) so recurring events survive DST. VTIMEZONE is bounded to
  the feed's date range.
- Prefer simple/direct; no over-engineering. Tests are fixture-based (`tests/fixtures/`).

## Run

```bash
uv run brewcal list --days 30
uv run brewcal build [--brewery SLUG] [--dry-run]
uv run pytest
```

## Known quirks in Honor's data

- A "Commanders Watch Party" is entered as 04:25-07:25 AM (their typo for PM). We pass source
  data through as-is; do not "fix" upstream mistakes in code.
- "Honorfest Beer Dinner" (2025) had end == start; such events get `default_duration_minutes`.
- Honor's Fairfax/Chantilly location has its own widget (`620fc0b1-b6f5-4814-a54a-33d51c45bfe8`),
  left commented out in config until Christopher wants it.

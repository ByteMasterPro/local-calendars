# Local Calendars

Subscribable `.ics` calendar feeds for breweries, towns and community groups around Loudoun
County, VA that don't publish one of their own, plus a command line for asking "what's coming
up?" across all of them at once.

**Feeds and subscribe buttons:** https://bytemasterpro.github.io/local-calendars/

| Calendar | Subscribe URL | Kind |
|---|---|---|
| Honor Brewing - Loudoun | `https://bytemasterpro.github.io/local-calendars/honor-brewing-loudoun.ics` | rebuilt daily from their Elfsight widget |
| Chilly Hollow Brewing Co. | `https://calendar.google.com/calendar/ical/7a36777b804e9b9d04d70c58670135065f60a7e350d7b860f9e9c7686a9a7131%40group.calendar.google.com/public/basic.ics` | their own public Google Calendar (live) |
| Solace Brewing Co. in Sterling | `https://calendar.google.com/calendar/ical/c_81872c01230a1e2a19950379cffece76cfb4c99e93bfa5a74ef2c3c26715c165%40group.calendar.google.com/public/basic.ics` | their own public Google Calendar (live) |
| Vanish Farmwoods Brewery (music) | `https://vanishbeer.com/calendar/category/music/?post_type=tribe_events&ical=1&eventDisplay=list` | their own iCal export (live) |
| Flying Ace Farm | `https://flyingacefarm.com/?post_type=tribe_events&ical=1&eventDisplay=list` | their own iCal export (live) |
| Town of Leesburg - Community Events | `https://bytemasterpro.github.io/local-calendars/leesburg.ics` | rebuilt daily from the Town's calendar RSS |
| Historic Manassas Inc. | `https://historicmanassas.org/events/?ical=1` | their own iCal export (live) |

## Subscribing

- **Apple Calendar (Mac):** File > New Calendar Subscription, paste the URL. Set Auto-refresh to
  "Every day" and Location to iCloud so it appears on iPhone/iPad too.
- **Google Calendar:** Other calendars (+) > From URL, paste the URL. Google re-fetches URL
  subscriptions on its own schedule (typically 12-24 h).
- The index page has one-click `webcal://` and "Add to Google Calendar" buttons.

## Asking questions

```bash
uv run localcal upcoming                                  # next 30 days, every calendar
uv run localcal upcoming --days 14 --only leesburg,chilly-hollow
uv run localcal upcoming --grep "oktober|german|stein"    # regex over title/description/categories
uv run localcal upcoming --from 2026-12-01 --days 31 --json
```

Everything is fetched live (external feeds are downloaded, built feeds are rebuilt from their
source), recurring events are expanded with EXDATE/RECURRENCE-ID honoured, and results are
sorted by start time. `--json` is for piping into other tools.

## How it works

```
config/calendars.yaml        one entry per calendar: slug, name, kind, address, source adapter or feed_url
localcal/sources/            adapters that turn a site's event data into Event objects
localcal/ical.py             Event -> RFC 5545 .ics (TZID + bounded VTIMEZONE, RRULE, EXDATE); parse + merge
localcal/cli.py              `localcal build` writes docs/<slug>.ics + docs/index.html; `localcal upcoming`
docs/                        the published site (GitHub Pages serves this folder)
.github/workflows/build.yml  daily cron (10:00 UTC) + on push to config/localcal; commits docs/
```

Only files whose event content changed are rewritten (DTSTAMP is ignored when comparing), so
the bot only commits when a source actually changed something.

### Adapters

- **`elfsight`** - sites embedding an Elfsight "Event Calendar" widget (Squarespace/Wix/WordPress
  sites do this a lot). Find `class="elfsight-app-<uuid>"` in the page source; the uuid is the
  `widget_id`. Data comes from Elfsight's public boot endpoint as JSON, no HTML scraping.
- **`vision_rss`** - Granicus govAccess (Vision CMS) municipal sites, whose HTML is often behind
  a bot wall but whose calendar RSS is not. The date/time is parsed out of each item's title.
  Pair with `accumulate: true` because the RSS only lists ~2 weeks ahead.
- **External feeds** (`feed_url`) - when the venue already publishes an `.ics` (public Google
  Calendar, WordPress The Events Calendar `?ical=1`, CivicPlus `iCalendar.aspx`), we link to it
  instead of mirroring. `upcoming` still queries it.

## Adding a calendar

1. Look at the events page source and identify the platform (the comment block at the top of
   `config/calendars.yaml` lists the tell-tale signs and what to put in the config).
2. Add an entry. `uv run localcal upcoming --only <slug>` to verify it parses.
3. Commit and push. The workflow publishes the feed and the index page picks it up.
4. New platform: write `localcal/sources/<name>.py` exposing `fetch(feed) -> list[Event]` and
   register it in `localcal/sources/__init__.py`.

## Gotchas

- GitHub disables scheduled workflows on public repos after 60 days without repository activity.
  The bot's commits count as activity, but if things go quiet, check the Actions tab.
- Google Calendar caches URL subscriptions aggressively. If a feed looks stale in Google but the
  raw `.ics` is current, it is Google's refresh lag, not the build.
- Leesburg's RSS horizon is ~2 weeks, so "next 30 days" queries only see the Town's first two.

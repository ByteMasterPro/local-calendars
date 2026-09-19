# Brewery Calendars

Subscribable `.ics` calendar feeds for local breweries that don't publish their own.
A GitHub Actions job rebuilds every feed weekly from each brewery's events page and
publishes them via GitHub Pages.

**Feeds:** https://bytemasterpro.github.io/brewery-calendars/

| Brewery | Subscribe URL | Kind |
|---|---|---|
| Honor Brewing - Loudoun | `https://bytemasterpro.github.io/brewery-calendars/honor-brewing-loudoun.ics` | rebuilt weekly from their Elfsight widget |
| Chilly Hollow Brewing Co. | `https://calendar.google.com/calendar/ical/7a36777b804e9b9d04d70c58670135065f60a7e350d7b860f9e9c7686a9a7131%40group.calendar.google.com/public/basic.ics` | their own public Google Calendar (live) |

## Subscribing

- **Apple Calendar (Mac):** File > New Calendar Subscription, paste the URL. Set Auto-refresh to
  "Every day". Choose iCloud as the location so it appears on iPhone/iPad too.
- **Google Calendar:** Other calendars (+) > From URL, paste the URL. Google re-fetches URL
  subscriptions on its own schedule (typically 12-24 h); there is no manual refresh.
- The index page has one-click `webcal://` and "Add to Google Calendar" buttons.

## How it works

```
config/breweries.yaml      one entry per brewery: slug, name, address, source adapter + params
brewcal/sources/           adapters that turn a site's event data into Event objects
brewcal/ical.py            Event -> RFC 5545 .ics (TZID + bounded VTIMEZONE, RRULE, EXDATE)
brewcal/cli.py             `brewcal build` writes docs/<slug>.ics + docs/index.html
docs/                      the published site (GitHub Pages serves this folder)
.github/workflows/build.yml  weekly cron (Mon 10:00 UTC) + on push to config/brewcal; commits docs/
```

Only files whose event content changed are rewritten (DTSTAMP is ignored when comparing), so
the bot only commits when a brewery actually changed something.

### Adapters

- **`elfsight`** - sites embedding an Elfsight "Event Calendar" widget (Squarespace/Wix/WordPress
  sites do this a lot). Find `class="elfsight-app-<uuid>"` in the page source; the uuid is the
  `widget_id`. Data comes from Elfsight's public boot endpoint as JSON, no HTML scraping.

## Local use

```bash
uv sync
uv run brewcal list --days 30                 # eyeball upcoming events without writing anything
uv run brewcal build                          # write docs/*.ics + docs/index.html
uv run brewcal build --brewery honor-brewing-loudoun --dry-run
uv run pytest
```

## Adding a brewery

1. Look at the brewery's events page source and identify the widget/platform.
2. If the brewery already publishes a feed, do not mirror it: add an entry with `feed_url`
   (and `google_calendar_id` if it is a Google Calendar) so the index page links to it directly.
   Tell-tale: an Elfsight widget whose boot JSON has `selectedEventsProvider: "google"` is fed by
   a Google Calendar; try `https://calendar.google.com/calendar/ical/<id>/public/basic.ics`.
3. If it is Elfsight with its own events: add an entry with `source: {type: elfsight, widget_id}`.
4. Otherwise: write `brewcal/sources/<name>.py` exposing `fetch(brewery) -> list[Event]`, register
   it in `brewcal/sources/__init__.py`, and use that key as `source.type`.
5. `uv run brewcal list --brewery <slug>` to verify, commit, push. The workflow publishes the feed
   and the index page picks it up automatically.

## Gotchas

- GitHub disables scheduled workflows on public repos after 60 days without repository activity.
  The bot's weekly commit counts as activity, but if a feed goes quiet for two months, check the
  Actions tab and re-enable the workflow.
- Google Calendar caches URL subscriptions aggressively. If a feed looks stale in Google but the
  raw `.ics` is current, it is Google's refresh lag, not the build.

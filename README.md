# Local Calendars

Subscribable `.ics` calendar feeds for breweries, towns and community groups around Loudoun
County, VA that don't publish one of their own, plus a command line for asking "what's coming
up?" across all of them at once.

**Feeds and subscribe buttons:** https://bytemasterpro.github.io/local-calendars/

| Calendar | Subscribe URL | Kind |
|---|---|---|
| Honor Brewing - Loudoun | `https://bytemasterpro.github.io/local-calendars/honor-brewing-loudoun.ics` | daily from their Elfsight widget + curated from their monthly Instagram poster |
| Chilly Hollow Brewing Co. | `https://calendar.google.com/calendar/ical/7a36777b804e9b9d04d70c58670135065f60a7e350d7b860f9e9c7686a9a7131%40group.calendar.google.com/public/basic.ics` | their own public Google Calendar (live) |
| Solace Brewing Co. in Sterling | `https://calendar.google.com/calendar/ical/c_81872c01230a1e2a19950379cffece76cfb4c99e93bfa5a74ef2c3c26715c165%40group.calendar.google.com/public/basic.ics` | their own public Google Calendar (live) |
| Vanish Farmwoods Brewery (music) | `https://vanishbeer.com/calendar/category/music/?post_type=tribe_events&ical=1&eventDisplay=list` | their own iCal export (live) |
| Flying Ace Farm | `https://flyingacefarm.com/?post_type=tribe_events&ical=1&eventDisplay=list` | their own iCal export (live) |
| Town of Leesburg - Community Events | `https://bytemasterpro.github.io/local-calendars/leesburg.ics` | rebuilt daily from the Town's calendar RSS |
| Historic Manassas Inc. | `https://historicmanassas.org/events/?ical=1` | their own iCal export (live) |
| One Loudoun | `https://bytemasterpro.github.io/local-calendars/one-loudoun.ics` | rebuilt daily from their Squarespace events JSON |
| Village at Leesburg | `https://bytemasterpro.github.io/local-calendars/village-at-leesburg.ics` | curated from their Wix events page |
| White's Ferry Brewery & Winery | `https://bytemasterpro.github.io/local-calendars/whites-ferry.ics` | curated from their Instagram |
| Route 7 Brewing | `https://bytemasterpro.github.io/local-calendars/route-7.ics` | live music from wheresthemusic.us + curated |
| Fairs & Festivals near Leesburg | `https://bytemasterpro.github.io/local-calendars/fairs-festivals.ics` | hand-curated in `config/events/fairs-festivals.yaml` |
| Farmers Markets near Leesburg | `https://bytemasterpro.github.io/local-calendars/farmers-markets.ics` | hand-curated in `config/events/farmers-markets.yaml` |

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

## Weekly Discord digest

`localcal digest` posts a **Top Picks This Week** card followed by three sections every Monday,
each grouped by date (one bold date header, the day's events under it). "This week" is Monday-Sunday; run it on a
Saturday and it shows only what is left of the weekend (anything already over is dropped). Each
section lists this week's events in full, then one **Next week:** line of highlights for the
following Mon-Sun. Nothing further out appears.

- **Fairs, Festivals and Carnivals** - one-offs this week, plus one "Ongoing weekends" line for
  season-long farms (pumpkin patches, Cox Farms) so they don't repeat every week.
- **Local Breweries** - seasonal events first (Sep-Oct: Oktoberfest, German, Halloween; Nov-Dec:
  Christmas and holiday), topped up with live music only when the season is thin. Karaoke,
  trivia, discounts and watch parties are excluded. The preview is seasonal-only.
- **Town Activities** - capped at 8, meetings excluded, festivals/parades/movies/kids ranked first,
  with a 🥕 **Farmers Markets** sub-list (Leesburg Saturday, Cascades Sunday, Historic Manassas).

Each line reads `**Sun Sep 20**, 1–4pm — Title (Venue, Town): excerpt. Tomorrow.` Everything is
tunable under `digest:` in `config/calendars.yaml`.

```bash
uv run localcal digest                     # preview from today through Sunday
uv run localcal digest --from 2026-09-21   # preview as if run on that day
DISCORD_WEBHOOK_URL=... uv run localcal digest --post
```

`.github/workflows/digest.yml` posts on Mondays using the `DISCORD_WEBHOOK_URL` repository
secret. GitHub's cron queue runs hours late and occasionally drops a run, so there are four
triggers through the day; scheduled runs pass `--skip-if-posted`, which checks the channel and
exits quietly once that week's digest is up. Trigger it by hand any time with
`gh workflow run "Weekly Discord digest"` (a manual run always posts).

Before posting, the channel is **purged** (every non-pinned message deleted) so it only ever holds
the current digest. That needs a bot in the server with *Manage Messages* and *Read Message
History* on the channel, and two more secrets: `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID`
(Discord: right-click the channel > Copy Channel ID, with Developer Mode on). Without them the
digest still posts, just without the purge. Pin anything you want to survive.

## What runs when

| When | What | Where |
|---|---|---|
| Daily, 05:23 UTC | `localcal build` - rebuild every fetched feed, refresh the external-feed cache, commit `docs/` | GitHub Actions (`build.yml`) |
| Daily, 10:30 UTC | copy the published `.ics` files to the old `brewery-calendars` paths | GitHub Actions, [mirror repo](https://github.com/ByteMasterPro/brewery-calendars) |
| **Mondays**, 06:17 / 10:17 / 14:17 / 18:17 UTC | `localcal digest --post --skip-if-posted` - purge the channel and post the week. The first run posts; the rest see it and stand down | GitHub Actions (`digest.yml`) |
| **Mon & Thu, 07:40 local** | `localcal instagram` - pull each venue's public grid, write the review queue | Christopher's Mac (launchd, installed 2026-11-02) |
| **1st of each month, by hand** | read Honor's month-at-a-glance poster out of the review queue and update the curated file and overrides | a session with Claude |

GitHub's cron queue runs hours late and occasionally drops a run, which is why the digest has
four triggers and the build runs early.

## How it works

```
config/calendars.yaml        one entry per calendar: slug, name, kind, address, source adapter or feed_url
localcal/sources/            adapters that turn a site's event data into Event objects
localcal/ical.py             Event -> RFC 5545 .ics (TZID + bounded VTIMEZONE, RRULE, EXDATE); parse + merge
localcal/query.py            live fetch + recurrence expansion shared by `upcoming` and the digest
localcal/digest.py           Discord digest sections, formatting, webhook posting and channel purge
localcal/instagram.py        pulls venues' public Instagram grids (local only, never CI)
scripts/*.plist              launchd job that runs the Instagram pull Mon/Thu 07:40
localcal/cli.py              `localcal build` / `upcoming` / `digest`
config/events/               curated event lists used by the `manual` adapter
.github/workflows/digest.yml Monday Discord post, four triggers (needs DISCORD_WEBHOOK_URL, DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID)
docs/                        the published site (GitHub Pages serves this folder)
.github/workflows/build.yml  daily cron (05:23 UTC) + on push to config/localcal; commits docs/
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
- **`squarespace`** - any Squarespace events page, via `?format=json`. Months-long single events
  on one weekday (how Squarespace users enter "every Thursday") are turned into weekly series.
- **`manual`** - a curated YAML file (`config/events/*.yaml`) for fairs, festivals and carnivals
  that no one publishes a feed for. Supports one-offs, timed events and multi-week `season`
  attractions that show on chosen weekdays. Every entry carries a `verified:` date.
- **`wheresthemusic`** - a venue page on wheresthemusic.us, for places with no calendar of their
  own (Route 7 Brewing). A feed's `source` can be a list, so a venue can merge a listings site
  with a curated file.
- **Cached external feeds** - `cache/<slug>.ics` holds the last good copy of every external feed,
  refreshed by the daily build. If a venue's server is down, serves HTML instead of iCal, or fails
  TLS when the digest runs, the cached copy is used so the venue still appears in the post.
- **External feeds** (`feed_url`) - when the venue already publishes an `.ics` (public Google
  Calendar, WordPress The Events Calendar `?ical=1`, CivicPlus `iCalendar.aspx`), we link to it
  instead of mirroring. `upcoming` still queries it.

## Event artwork

Most feeds carry a poster for each event (Elfsight cover art, iCal `ATTACH`, Squarespace asset),
and venues often put details there that appear nowhere in the text. `localcal posters` downloads
them so they can be read, and anything worth saying goes into `config/events/overrides.yaml`,
which the digest prefers over the feed's blurb.

```bash
uv run localcal posters --days 30                  # into data/posters/ (gitignored)
uv run localcal posters --days 30 --missing-only   # skip events that already have an override
```

## Instagram

Some venues (Route 7, White's Ferry) announce only on Instagram. `localcal instagram` loads each
configured handle's public profile in headless Chromium, downloads new grid images to
`data/instagram/<handle>/` and writes a `review.md` queue. Nothing logs in, so captions are never
read; when a date or promo code is caption-only, a `notices:` entry in
`config/events/overrides.yaml` makes the digest say so and link the profile.

Handles watched: `honorbrewingcompany`, `rt7brewing`, `whitesferrybrew` (the `instagram:` key on
a calendar).

```bash
uv run localcal instagram                       # all configured handles, on demand
uv run localcal instagram --pending             # what is still unread (no network)
uv run localcal instagram --mark-reviewed       # after the images have been looked at
launchctl start com.kyberstorm.localcal-instagram   # run the scheduled job now
tail -20 /tmp/localcal-instagram.log            # what it did
```

`data/instagram/review.md` lists only posts **not yet read**, as checkboxes with a count, so a
new poster cannot hide among ones already seen.

**The schedule.** `scripts/com.kyberstorm.localcal-instagram.plist` runs it **Mondays and
Thursdays at 07:40 local**, before Monday's digest. It was installed and verified on 2026-11-02:

```bash
cp scripts/com.kyberstorm.localcal-instagram.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.kyberstorm.localcal-instagram.plist
```

`RunAtLoad` is false, so loading it does not fire a run; the first one is the next Mon/Thu.
Logs go to `/tmp/localcal-instagram.log`. Check it is registered with
`launchctl list | grep localcal`.

This is local-only by necessity: the grid is JavaScript-rendered and Instagram blocks datacenter
IPs, so it cannot run in GitHub Actions, and nothing ever logs in.

**The half that is not automated.** Pulling images is scheduled; *reading* them is not. Honor
publishes a month-at-a-glance schedule on the 1st and it routinely carries events their widget
never gets, so at the start of each month ask Claude to read the queue
(`data/instagram/review.md`) and update `config/events/honor-brewing.yaml` and
`config/events/overrides.yaml`.

## Adding a calendar

1. Look at the events page source and identify the platform (the comment block at the top of
   `config/calendars.yaml` lists the tell-tale signs and what to put in the config).
2. Add an entry. `uv run localcal upcoming --only <slug>` to verify it parses.
3. Commit and push. The workflow publishes the feed and the index page picks it up.
4. New platform: write `localcal/sources/<name>.py` exposing `fetch(feed) -> list[Event]` and
   register it in `localcal/sources/__init__.py`.

## Old subscription URLs

Subscriptions created before the rename point at `bytemasterpro.github.io/brewery-calendars/...`.
GitHub Pages cannot redirect those, so the [brewery-calendars](https://github.com/ByteMasterPro/brewery-calendars)
repo serves the same files at the old paths and refreshes them from here daily. Prefer the
`local-calendars` URLs for anything new; add new feed filenames to that repo as well.

## Gotchas

- GitHub disables scheduled workflows on public repos after 60 days without repository activity.
  The bot's commits count as activity, but if things go quiet, check the Actions tab.
- Google Calendar caches URL subscriptions aggressively. If a feed looks stale in Google but the
  raw `.ics` is current, it is Google's refresh lag, not the build.
- Leesburg's RSS horizon is ~2 weeks, so "next 30 days" queries only see the Town's first two.

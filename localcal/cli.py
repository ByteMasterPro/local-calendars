"""localcal command line.

    localcal build    [--only SLUG] [--dry-run]              fetch sources, write docs/<slug>.ics + docs/index.html
    localcal upcoming [--days N] [--grep REGEX] [--only SLUG,SLUG] [--json]
                                                             what's happening across ALL calendars (built + external)
    localcal digest   [--days 7] [--post]                    weekly Discord digest (Recommended / Other Family Events)
"""

from __future__ import annotations

import argparse
import os
import html
import json
import logging
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from localcal import digest as digest_mod
from localcal import ical, query
from localcal.model import KINDS, Config, Feed, load_config
from localcal.query import load_calendar, occurrences  # noqa: F401  (re-exported for tests)
from localcal.sources import fetch_events

log = logging.getLogger("localcal")
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "calendars.yaml"
DEFAULT_OUT = ROOT / "docs"
USER_AGENT = "localcal/0.1 (+https://github.com/ByteMasterPro/local-calendars)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="localcal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="fetch sources and write .ics feeds")
    b.add_argument("--only", help="comma-separated slugs")
    b.add_argument("--out", type=Path, default=DEFAULT_OUT)
    b.add_argument("--dry-run", action="store_true", help="fetch and render but write nothing")

    u = sub.add_parser("upcoming", aliases=["list"], help="print upcoming events across all calendars")
    u.add_argument("--days", type=int, default=30)
    u.add_argument("--from", dest="start", type=date.fromisoformat, default=None, help="YYYY-MM-DD (default today)")
    u.add_argument("--grep", help="case-insensitive regex matched against title, description, categories")
    u.add_argument("--only", help="comma-separated slugs")
    u.add_argument("--json", action="store_true")

    dg = sub.add_parser("digest", help="build (and with --post, send) the weekly Discord digest")
    dg.add_argument("--days", type=int, default=None, help="window length (default: digest.days in config, else 7)")
    dg.add_argument("--from", dest="start", type=date.fromisoformat, default=None)
    dg.add_argument("--post", action="store_true", help="send to $DISCORD_WEBHOOK_URL instead of printing")
    dg.add_argument("--only", help="comma-separated slugs")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)

    cfg = load_config(args.config)
    wanted = set(args.only.split(",")) if args.only else None
    feeds = [f for f in cfg.feeds if not wanted or f.slug in wanted]
    if not feeds:
        log.error("no calendar matches %r; configured: %s", args.only, [f.slug for f in cfg.feeds])
        return 2

    if args.cmd == "build":
        return build(cfg, feeds, args.out, dry_run=args.dry_run)
    start = args.start or date.today()
    if args.cmd == "digest":
        return digest(cfg, feeds, start, args.days or int(cfg.digest.get("days", 7)), post=args.post)
    return upcoming(cfg, feeds, start, start + timedelta(days=args.days), args.grep, as_json=args.json)


# --------------------------------------------------------------------------- build

def build(cfg: Config, feeds: list[Feed], out: Path, *, dry_run: bool) -> int:
    failures = 0
    for feed in feeds:
        if feed.external:
            log.info("%s: external feed (%s), nothing to build", feed.slug, feed.feed_url)
            continue
        target = out / f"{feed.slug}.ics"
        try:
            events = fetch_events(feed)
        except Exception as exc:
            # Keep last run's feed rather than publishing an empty calendar.
            log.error("%s: fetch failed, leaving existing feed untouched: %s", feed.slug, exc)
            failures += 1
            continue
        fetched = len(events)
        if feed.accumulate and target.exists():
            events = ical.merge_accumulated(events, ical.parse(target.read_bytes()))
        kept = ical.filter_window(events, feed.keep_past_days)
        rendered = ical.render(feed, kept)
        upcoming_n = sum(1 for e in kept if e.start_date >= date.today())
        changed = not target.exists() or ical.strip_volatile(target.read_bytes()) != ical.strip_volatile(rendered)
        log.info("%s: %d fetched, %d in feed (%d upcoming), %s",
                 feed.slug, fetched, len(kept), upcoming_n, "CHANGED" if changed else "unchanged")
        if changed and not dry_run:
            out.mkdir(parents=True, exist_ok=True)
            target.write_bytes(rendered)

    if not dry_run and cfg.site.get("base_url"):
        index = out / "index.html"
        page = render_index(cfg)
        if not index.exists() or index.read_text() != page:
            index.write_text(page)
            log.info("index.html updated")
    return 1 if failures else 0


# ------------------------------------------------------------------------ upcoming

def upcoming(cfg: Config, feeds: list[Feed], start: date, end: date, pattern: str | None, *, as_json: bool) -> int:
    rows, errors = query.gather(feeds, start, end)
    if pattern:
        rx = re.compile(pattern, re.I)
        rows = [r for r in rows if query.matches(r, rx)]

    if as_json:
        for r in rows:
            r.pop("_sort")
        json.dump(rows, sys.stdout, indent=1, default=str)
        print()
        return 1 if errors else 0

    print(f"{len(rows)} events, {start:%a %b %d} to {end:%a %b %d}"
          + (f", matching /{pattern}/" if pattern else "") + f", across {len(feeds)} calendars\n")
    last_day = None
    for r in rows:
        s = datetime.fromisoformat(r["start"]); e = datetime.fromisoformat(r["end"])
        day = s.date()
        if day != last_day:
            print(f"{day:%a %b %d}")
            last_day = day
        if r["all_day"]:
            span = e.date() - timedelta(days=1)
            when = "all day" if span <= day else f"through {span:%b %d}"
        elif e.date() > day:
            when = f"{_clock(s)} thru {e:%b %d}"
        else:
            when = f"{_clock(s)}-{_clock(e)}"
        print(f"  {when:<18} {r['summary'][:70]:<70}  [{r['calendar']}]")
    if errors:
        print(f"\n({errors} calendar(s) could not be loaded; see errors above)", file=sys.stderr)
    return 1 if errors else 0


def _clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").lower().replace(":00", "")


# ---------------------------------------------------------------------------- digest

def digest(cfg: Config, feeds: list[Feed], start: date, days: int, *, post: bool) -> int:
    d = digest_mod.build(cfg, feeds, start, days)
    if not post:
        print(digest_mod.render_text(d))
        return 1 if d.errors else 0
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        log.error("DISCORD_WEBHOOK_URL is not set; printing instead")
        print(digest_mod.render_text(d))
        return 2
    digest_mod.post(webhook, digest_mod.discord_payloads(d))
    log.info("digest posted: %s", ", ".join(f"{s.label}={len(s.lines)}" for s in d.sections))
    if d.errors:
        # The post itself carries the warning line; on Actions surface it as an annotation, not a failure.
        print(f"::warning::{d.errors} calendar(s) could not be loaded; digest posted without them")
    return 0


# --------------------------------------------------------------------------- index

def render_index(cfg: Config) -> str:
    title = html.escape(cfg.site.get("title", "Local Calendars"))
    sections = []
    for kind, heading in KINDS.items():
        feeds = [f for f in cfg.feeds if f.kind == kind]
        if not feeds:
            continue
        items = []
        for f in feeds:
            https_url = cfg.feed_url_for(f)
            webcal_url = https_url.replace("https://", "webcal://", 1)
            if f.google_calendar_id:
                google_url = f"https://calendar.google.com/calendar/r?cid={quote(f.google_calendar_id, safe='')}"
            else:
                google_url = f"https://calendar.google.com/calendar/r?cid={quote(webcal_url, safe='')}"
            badge = "venue's own public calendar, updates live" if f.external else "rebuilt daily from their events page"
            items.append(f"""
      <li>
        <h3>{html.escape(f.name)}</h3>
        <p class="meta">{html.escape(f.location)} &middot; <a href="{html.escape(f.url)}">events page</a> &middot; <span class="badge">{badge}</span></p>
        <p class="links">
          <a class="btn" href="{webcal_url}">Subscribe (Apple / Outlook)</a>
          <a class="btn" href="{google_url}">Add to Google Calendar</a>
          <a class="btn alt" href="{https_url}">Raw .ics</a>
        </p>
        <p class="url"><code>{https_url}</code></p>
      </li>""")
        sections.append(f"\n  <h2>{heading}</h2>\n  <ul>{''.join(items)}\n  </ul>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1d1d1f; --bg:#fff; --muted:#6e6e73; --btn:#2a8fbd; --card:#f5f5f7; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#f5f5f7; --bg:#111; --muted:#a1a1a6; --card:#1c1c1e; }} }}
  body {{ margin:0; padding:24px 16px; font:16px/1.5 -apple-system,system-ui,sans-serif; color:var(--fg); background:var(--bg); }}
  main {{ max-width:720px; margin:0 auto; }}
  h2 {{ margin:32px 0 8px; font-size:1.1rem; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }}
  ul {{ list-style:none; padding:0; margin:0; }}
  li {{ background:var(--card); border-radius:12px; padding:16px 20px; margin:12px 0; }}
  h3 {{ margin:0 0 4px; font-size:1.25rem; }}
  .meta {{ margin:0 0 12px; color:var(--muted); font-size:.9rem; }}
  .links {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 8px; }}
  .btn {{ background:var(--btn); color:#fff; text-decoration:none; padding:8px 14px; border-radius:8px; font-size:.9rem; }}
  .btn.alt {{ background:var(--muted); }}
  .url {{ margin:0; font-size:.8rem; word-break:break-all; color:var(--muted); }}
  footer {{ color:var(--muted); font-size:.8rem; margin-top:32px; }}
</style></head>
<body><main>
  <h1>{title}</h1>
  <p>Subscribable calendars for breweries, towns and community groups around Loudoun County, VA that don't publish one of their own.</p>{''.join(sections)}
  <footer>Feeds rebuild automatically every morning. Source and config: <a href="https://github.com/ByteMasterPro/local-calendars">github.com/ByteMasterPro/local-calendars</a></footer>
</main></body></html>
"""


if __name__ == "__main__":
    sys.exit(main())

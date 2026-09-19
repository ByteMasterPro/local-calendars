"""brewcal command line.

    brewcal build [--brewery SLUG] [--dry-run]   fetch every source, write docs/<slug>.ics + docs/index.html
    brewcal list  [--brewery SLUG] [--days N]    print upcoming events (sanity check without writing)
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from brewcal import ical
from brewcal.model import Brewery, Config, load_config
from brewcal.sources import fetch_events

log = logging.getLogger("brewcal")
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "breweries.yaml"
DEFAULT_OUT = ROOT / "docs"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brewcal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="fetch sources and write .ics feeds")
    b.add_argument("--brewery", help="only this slug")
    b.add_argument("--out", type=Path, default=DEFAULT_OUT)
    b.add_argument("--dry-run", action="store_true", help="fetch and render but write nothing")

    l = sub.add_parser("list", help="print upcoming events")
    l.add_argument("--brewery", help="only this slug")
    l.add_argument("--days", type=int, default=30)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    breweries = [x for x in cfg.breweries if not args.brewery or x.slug == args.brewery]
    if not breweries:
        log.error("no brewery matches %r; configured: %s", args.brewery, [x.slug for x in cfg.breweries])
        return 2

    if args.cmd == "build":
        return build(cfg, breweries, args.out, dry_run=args.dry_run)
    return list_events(breweries, args.days)


def build(cfg: Config, breweries: list[Brewery], out: Path, *, dry_run: bool) -> int:
    failures = 0
    for brewery in breweries:
        if brewery.external:
            log.info("%s: external feed (%s), nothing to build", brewery.slug, brewery.feed_url)
            continue
        try:
            events = fetch_events(brewery)
        except Exception as exc:
            # Keep last week's feed rather than publishing an empty calendar.
            log.error("%s: fetch failed, leaving existing feed untouched: %s", brewery.slug, exc)
            failures += 1
            continue
        kept = ical.filter_window(events, brewery.keep_past_days)
        rendered = ical.render(brewery, kept)
        target = out / f"{brewery.slug}.ics"
        upcoming = sum(1 for e in kept if _starts_on_or_after(e, date.today()))
        changed = not target.exists() or ical.strip_volatile(target.read_bytes()) != ical.strip_volatile(rendered)
        log.info("%s: %d events from source, %d kept (%d upcoming), %s",
                 brewery.slug, len(events), len(kept), upcoming, "CHANGED" if changed else "unchanged")
        if changed and not dry_run:
            out.mkdir(parents=True, exist_ok=True)
            target.write_bytes(rendered)

    if not dry_run and cfg.site.get("base_url"):
        index = out / "index.html"
        page = render_index(cfg).encode()
        if not index.exists() or index.read_bytes() != page:
            index.write_text(page.decode())
            log.info("index.html updated")
    return 1 if failures else 0


def list_events(breweries: list[Brewery], days: int) -> int:
    horizon = date.today() + timedelta(days=days)
    for brewery in breweries:
        if brewery.external:
            print(f"== {brewery.name}: external feed, subscribe directly: {brewery.feed_url}")
            continue
        events = fetch_events(brewery)
        print(f"== {brewery.name} ({len(events)} events in source) ==")
        for ev in sorted(events, key=lambda e: ical._sort_key(e.start)):
            day = ev.start if isinstance(ev.start, date) and not isinstance(ev.start, datetime) else ev.start.date()
            if not (date.today() <= day <= horizon):
                continue
            when = day.strftime("%a %b %d") + ("  all day" if ev.all_day else ev.start.strftime("  %I:%M %p") + ev.end.strftime("-%I:%M %p"))
            cats = f"  [{', '.join(ev.categories)}]" if ev.categories else ""
            print(f"  {when:<28} {ev.summary}{cats}")
    return 0


def _starts_on_or_after(ev, day: date) -> bool:
    start = ev.start if isinstance(ev.start, date) and not isinstance(ev.start, datetime) else ev.start.date()
    return start >= day


def render_index(cfg: Config) -> str:
    base = cfg.site["base_url"].rstrip("/")
    title = html.escape(cfg.site.get("title", "Brewery Calendars"))
    rows = []
    for b in cfg.breweries:
        https_url = b.feed_url or f"{base}/{b.slug}.ics"
        webcal_url = https_url.replace("https://", "webcal://", 1)
        if b.google_calendar_id:
            google_url = f"https://calendar.google.com/calendar/r?cid={quote(b.google_calendar_id, safe='')}"
        else:
            google_url = f"https://calendar.google.com/calendar/r?cid={quote(webcal_url, safe='')}"
        badge = ('<span class="badge">brewery\'s own public calendar, updates live</span>' if b.external
                 else '<span class="badge">rebuilt weekly from their events page</span>')
        rows.append(f"""
      <li>
        <h2>{html.escape(b.name)}</h2>
        <p class="meta">{html.escape(b.location)} &middot; <a href="{html.escape(b.url)}">events page</a> &middot; {badge}</p>
        <p class="links">
          <a class="btn" href="{webcal_url}">Subscribe (Apple / Outlook)</a>
          <a class="btn" href="{google_url}">Add to Google Calendar</a>
          <a class="btn alt" href="{https_url}">Raw .ics</a>
        </p>
        <p class="url"><code>{https_url}</code></p>
      </li>""")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1d1d1f; --bg:#fff; --muted:#6e6e73; --btn:#2a8fbd; --card:#f5f5f7; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#f5f5f7; --bg:#111; --muted:#a1a1a6; --card:#1c1c1e; }} }}
  body {{ margin:0; padding:24px 16px; font:16px/1.5 -apple-system,system-ui,sans-serif; color:var(--fg); background:var(--bg); }}
  main {{ max-width:720px; margin:0 auto; }}
  ul {{ list-style:none; padding:0; }}
  li {{ background:var(--card); border-radius:12px; padding:16px 20px; margin:16px 0; }}
  h2 {{ margin:0 0 4px; font-size:1.25rem; }}
  .meta {{ margin:0 0 12px; color:var(--muted); font-size:.9rem; }}
  .links {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 8px; }}
  .btn {{ background:var(--btn); color:#fff; text-decoration:none; padding:8px 14px; border-radius:8px; font-size:.9rem; }}
  .btn.alt {{ background:var(--muted); }}
  .url {{ margin:0; font-size:.8rem; word-break:break-all; color:var(--muted); }}
  .badge {{ font-size:.8rem; }}
  footer {{ color:var(--muted); font-size:.8rem; margin-top:32px; }}
</style></head>
<body><main>
  <h1>{title}</h1>
  <p>Subscribable calendars for breweries that don't publish their own. Feeds are rebuilt automatically from each brewery's events page.</p>
  <ul>{''.join(rows)}
  </ul>
  <footer>Feeds rebuild automatically every Monday. Source and config: <a href="https://github.com/ByteMasterPro/brewery-calendars">github.com/ByteMasterPro/brewery-calendars</a></footer>
</main></body></html>
"""


if __name__ == "__main__":
    sys.exit(main())

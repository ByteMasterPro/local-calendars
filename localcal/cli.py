"""localcal command line.

    localcal build    [--only SLUG] [--dry-run]              fetch sources, write docs/<slug>.ics + docs/index.html
    localcal upcoming [--days N] [--grep REGEX] [--only SLUG,SLUG] [--json]
                                                             what's happening across ALL calendars (built + external)
    localcal digest   [--from DATE] [--post]                 weekly Discord digest (this week + next-week highlights)
    localcal posters  [--days N] [--only SLUG] [--out DIR]   download event artwork for review
    localcal instagram [--only SLUG]                         pull venues' public Instagram grids (local only)
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

import requests

from localcal import digest as digest_mod
from localcal import instagram as ig_mod
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

    ps = sub.add_parser("posters", help="download upcoming events' artwork so their details can be read")
    ps.add_argument("--days", type=int, default=30)
    ps.add_argument("--only", help="comma-separated slugs")
    ps.add_argument("--out", type=Path, default=ROOT / "data" / "posters")
    ps.add_argument("--missing-only", action="store_true", help="skip events that already have an override")
    ps.add_argument("--from", dest="start", type=date.fromisoformat, default=None)

    ig = sub.add_parser("instagram", help="pull venues' public Instagram grids (needs a browser; never runs in CI)")
    ig.add_argument("--only", help="comma-separated slugs")
    ig.add_argument("--out", type=Path, default=ROOT / "data" / "instagram")

    dg = sub.add_parser("digest", help="build (and with --post, send) the weekly Discord digest")
    dg.add_argument("--from", dest="start", type=date.fromisoformat, default=None,
                    help="pretend it is this day (the window runs from here to Sunday)")
    dg.add_argument("--post", action="store_true", help="send to $DISCORD_WEBHOOK_URL instead of printing")
    dg.add_argument("--skip-if-posted", action="store_true",
                    help="do nothing if a digest already went out this week (for backup schedules)")
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
    start = getattr(args, "start", None) or date.today()
    if args.cmd == "instagram":
        return instagram(feeds, args.out)

    if args.cmd == "posters":
        return posters(cfg, feeds, start, start + timedelta(days=args.days), args.out, missing_only=args.missing_only)
    if args.cmd == "digest":
        return digest(cfg, feeds, start, post=args.post, skip_if_posted=args.skip_if_posted)
    return upcoming(cfg, feeds, start, start + timedelta(days=args.days), args.grep, as_json=args.json)


# --------------------------------------------------------------------------- build

def build(cfg: Config, feeds: list[Feed], out: Path, *, dry_run: bool) -> int:
    failures = 0
    for feed in feeds:
        if feed.external:
            # Nothing to publish (subscribers read the venue's own feed), but keep a local copy
            # so the digest still has their events when the venue's server misbehaves.
            try:
                content = query.fetch_external(feed)
                if not dry_run:
                    query.save_cache(feed, content)
                log.info("%s: external feed cached (%d bytes)", feed.slug, len(content))
            except Exception as exc:
                log.warning("%s: external feed unreachable, keeping last cached copy: %s", feed.slug, str(exc)[:120])
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


# ------------------------------------------------------------------------- instagram

def instagram(feeds: list[Feed], out: Path) -> int:
    """Pull each venue's public Instagram grid. Runs on Christopher's Mac under launchd, never
    in CI: the grid needs a real browser and Instagram blocks datacenter IPs. Nothing logs in,
    so captions stay unread and anything caption-only is flagged for him to open himself."""
    handles = [(f.slug, f.instagram) for f in feeds if f.instagram]
    if not handles:
        log.error("no calendar has an `instagram:` handle configured")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    everything: list = []
    failures = 0
    for slug, handle in handles:
        try:
            posts = ig_mod.pull(handle, out)
            everything += posts
            log.info("%s: @%s has %d post(s) on file, %d worth reading",
                     slug, handle, len(posts), sum(1 for p in posts if p.flagged))
        except Exception as exc:
            log.error("%s: @%s pull failed: %s", slug, handle, str(exc)[:200])
            failures += 1
    if everything:
        log.info("review queue: %s", ig_mod.write_review(out, everything))
    return 1 if failures else 0


# --------------------------------------------------------------------------- posters

def posters(cfg: Config, feeds: list[Feed], start: date, end: date, out: Path, *, missing_only: bool) -> int:
    """Download upcoming events' artwork. Venues routinely put the real details only in the
    poster (Honor's Fall Fest lists "20 vendors, axe throwing, petting zoo" nowhere else), so
    these get read by eye and the findings go into config/events/overrides.yaml."""
    rows, errors = query.gather(feeds, start, end)
    overrides = digest_mod.load_overrides(ROOT) if missing_only else {}
    out.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    saved = skipped = 0
    for r in rows:
        if not r["image"] or r["summary"].lower() in seen:
            continue
        seen.add(r["summary"].lower())
        if missing_only and digest_mod.override_for(r, overrides):
            continue
        name = f"{r['start'][:10]}_{r['slug']}_{re.sub(r'[^a-z0-9]+', '-', r['summary'].lower()).strip('-')[:50]}"
        ext = Path(r["image"].split("?")[0]).suffix.lower()
        path = out / (name + (ext if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"))
        if path.exists():
            skipped += 1
            print(f"{path}  (have)  {r['summary']}")
            continue
        try:
            resp = requests.get(r["image"], headers={"User-Agent": query.USER_AGENT}, timeout=60)
            resp.raise_for_status()
            path.write_bytes(resp.content)
            saved += 1
            print(f"{path}  {r['start'][:10]}  {r['summary']}  [{r['calendar']}]")
        except Exception as exc:
            log.warning("%s: could not fetch artwork (%s)", r["summary"][:40], str(exc)[:100])
    log.info("%d new, %d already downloaded, into %s", saved, skipped, out)
    return 1 if errors else 0


# ---------------------------------------------------------------------------- digest

def digest(cfg: Config, feeds: list[Feed], start: date, *, post: bool, skip_if_posted: bool = False) -> int:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    bot_token, channel_id = os.environ.get("DISCORD_BOT_TOKEN"), os.environ.get("DISCORD_CHANNEL_ID")
    title = (cfg.digest or {}).get("title", "This week")

    if post and skip_if_posted:
        if not (bot_token and channel_id):
            log.warning("--skip-if-posted needs DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID; posting anyway")
        else:
            tz = ZoneInfo(feeds[0].timezone if feeds else "America/New_York")
            monday = datetime.combine(start - timedelta(days=start.weekday()), time.min, tzinfo=tz)
            hook_id = digest_mod.webhook_id(webhook or "")
            log.info("skip-if-posted: looking for a post from webhook %s since %s", hook_id or "(any app)", monday)
            try:
                already = digest_mod.posted_since(bot_token, channel_id, monday, hook_id)
            except Exception as exc:               # a read failure must not cost us the week's post
                log.warning("could not check for an existing post (%s); posting", str(exc)[:120])
                already = None
            if already:
                log.info("digest already posted %s; nothing to do", already.astimezone(tz).strftime("%a %b %d %H:%M %Z"))
                return 0
            log.info("skip-if-posted: no digest found this week; posting")

    d = digest_mod.build(cfg, feeds, start)
    if not post:
        print(digest_mod.render_text(d))
        return 1 if d.errors else 0
    if not webhook:
        log.error("DISCORD_WEBHOOK_URL is not set; printing instead")
        print(digest_mod.render_text(d))
        return 2
    # Christopher wants the channel to hold only the current digest: wipe it right before posting.
    if bot_token and channel_id:
        try:
            n = digest_mod.purge_channel(bot_token, channel_id)
            log.info("purged %d old message(s) from the channel", n)
        except Exception as exc:
            log.warning("channel purge failed (posting anyway): %s", exc)
            print("::warning::channel purge failed; digest posted on top of old messages")
    else:
        log.warning("DISCORD_BOT_TOKEN / DISCORD_CHANNEL_ID not set; skipping channel purge")
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

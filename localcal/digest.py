"""Weekly Discord digest, three sections Christopher specified.

"This week" is Monday-Sunday. The post runs Monday morning, but can run any day: it then shows
only what is left of the week (run time through Sunday), and anything already over is dropped.
Each section lists this week's events in full, then ONE "Next week:" line of highlights for the
following Mon-Sun. Nothing further out appears.

  Fairs, Festivals and Carnivals   one-offs this week + one "Ongoing weekends" line for season farms
  Local Breweries                  seasonal first (Sep-Oct Oktoberfest/German/Halloween, Nov-Dec holiday),
                                   topped up with live music only when thin; never karaoke/trivia/discounts
  Town Activities                  capped, meetings excluded, with a Farmers Markets sub-list

Every line reads:  **Sun Sep 20**, 1–4pm — [Title](url) (Venue, Town): excerpt. Tomorrow.
Settings live under `digest:` in config/calendars.yaml.
"""

from __future__ import annotations

import logging
import re
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from localcal import query
from localcal.model import Config, Feed
from localcal.text import html_to_text

log = logging.getLogger(__name__)

DISCORD_MAX_CONTENT = 2000
DISCORD_MAX_EMBED_DESC = 4000      # hard limit is 4096; leave headroom
EXCERPT_CHARS = 220
_OUR_TRAILERS = re.compile(r"^(event listing|details|calendar|more info|source|get tickets|tickets|buy tickets|rsvp|register)\s*:\s*\S+$", re.I | re.M)


@dataclass
class Section:
    key: str
    label: str
    emoji: str
    color: int
    lines: list[str] = field(default_factory=list)

    def __post_init__(self):
        # Blocks are separated by blank lines, so a section whose first block is absent (no dated
        # events this week, only an ongoing list) would otherwise open with an empty line.
        while self.lines and not self.lines[0].strip():
            self.lines.pop(0)
        while self.lines and not self.lines[-1].strip():
            self.lines.pop()


@dataclass
class Digest:
    title: str
    start: date
    end: date                 # inclusive: the Sunday closing this week
    next_start: date
    next_end: date
    sections: list[Section]
    index_url: str
    errors: int = 0


@dataclass
class Window:
    start: date               # first day shown in detail (the run day)
    end: date                 # Sunday of this week, inclusive
    next_start: date          # following Monday
    next_end: date            # following Sunday, inclusive
    now: datetime             # anything that ended before this is dropped
    preview_limit: int = 5


# ------------------------------------------------------------------------ build

def build(cfg: Config, feeds: list[Feed], start: date, days: int | None = None, now: datetime | None = None) -> Digest:
    cfg_d = cfg.digest or {}
    secs = cfg_d.get("sections") or {}
    tz = ZoneInfo(feeds[0].timezone) if feeds else ZoneInfo("America/New_York")
    w = week_window(start, now or datetime.now(tz), int(cfg_d.get("preview_limit", 5)))
    rows, errors = query.gather(feeds, w.start, w.next_end + timedelta(days=1))
    by_slug = {f.slug: f for f in feeds}
    root = Path(__file__).resolve().parent.parent
    ov = load_overrides(root)
    notices = load_notices(root)

    # Pass 1: what each section would show. Pass 2: pick the week's highlights across them for the
    # Top Picks card. Highlights also appear in full in their own section; repetition is intended.
    F = fairs_select(rows, secs["fairs"], w) if "fairs" in secs else None
    B = breweries_select(rows, secs["breweries"], w) if "breweries" in secs else None
    T = towns_select(rows, secs["towns"], w) if "towns" in secs else None
    picks = top_picks(F, B, T, cfg_d.get("top_picks") or {}, w)

    sections = []
    if picks:
        c = cfg_d.get("top_picks") or {}
        sections.append(Section("picks", c.get("label", "Top Picks This Week"), "⭐", 0xE67E22,
                                pick_lines(picks, w, by_slug, icons=c.get("icons"), overrides=ov)))
    if F:
        c = secs["fairs"]
        ongoing = []
        if F["ongoing"]:
            ongoing = ["**Ongoing weekends:**"]             # label, then one per line
            for r in F["ongoing"]:
                until = f" thru {_d(date.fromisoformat(r['series_until']))}" if r.get("series_until") else ""
                ongoing.append(f"{_link(_short_title(r['summary']), r['url'])}{until}")
        sections.append(Section("fairs", c.get("label", "Fairs, Festivals and Carnivals"), "🎪", 0x2A8FBD,
                                _blocks(_running_line(F["running"], by_slug), grouped_lines(F["this"], w, by_slug, ov),
                                        ongoing, notice_lines(notices, w.start, "festival"), _preview(F["next"], w))))
    if B:
        c = secs["breweries"]
        sections.append(Section("breweries", c.get("label", "Local Breweries"), "🍺", 0xF39C12,
                                _blocks(_running_line(B["running"], by_slug), grouped_lines(B["this"], w, by_slug, ov),
                                        notice_lines(notices, w.start, "brewery"), _preview(B["next"], w))))
    if T:
        c = secs["towns"]
        markets = []
        if T["markets"]:
            markets = [f"🥕 **{(c.get('farmers_markets') or {}).get('label', 'Farmers Markets')}:**"]
            markets += [_market_line(occ, by_slug) for occ in _group(T["markets"]).values()]
        sections.append(Section("towns", c.get("label", "Town Activities"), "🏘️", 0x27AE60,
                                _blocks(_running_line(T["running"], by_slug), grouped_lines(T["this"], w, by_slug, ov),
                                        markets, notice_lines(notices, w.start, "town"), _preview(T["next"], w))))

    return Digest(title=cfg_d.get("title", "This week"), start=w.start, end=w.end, next_start=w.next_start,
                  next_end=w.next_end, sections=sections, index_url=cfg.site.get("base_url", ""), errors=errors)


def week_window(start: date, now: datetime, preview_limit: int = 5) -> Window:
    """Monday-Sunday weeks. Run on a Saturday and you get Sat-Sun; next week is always Mon-Sun."""
    sunday = start + timedelta(days=6 - start.weekday())
    return Window(start=start, end=sunday, next_start=sunday + timedelta(days=1),
                  next_end=sunday + timedelta(days=7), now=now, preview_limit=preview_limit)


# --------------------------------------------------------------- selection

def fairs_select(rows, c, w: Window) -> dict:
    kinds = c.get("kinds", ["festival"])
    this = _select(rows, kinds=kinds, start=w.start, end=w.end, now=w.now, exclude=c.get("exclude"))
    running = _collapse([r for r in this if r.get("_ongoing")])
    this = [r for r in this if not r.get("_ongoing")]
    one_offs = _collapse([r for r in this if not r["series"]])[: int(c.get("limit", 8))]
    ongoing = _collapse([r for r in this if r["series"]])
    shown = {r["summary"].lower() for r in one_offs + ongoing + running}
    nxt = _collapse([r for r in _select(rows, kinds=kinds, start=w.next_start, end=w.next_end, now=None, exclude=c.get("exclude"))
                     if r["summary"].lower() not in shown])
    return {"this": one_offs, "ongoing": ongoing, "running": running, "next": nxt}


def breweries_select(rows, c, w: Window) -> dict:
    """This week's brewery events, ranked so the good stuff survives a long week.

    Everything that passes the exclude list is shown, up to `limit`. Order of survival when a
    week overflows: seasonal (Oktoberfest, Halloween, holiday), then the kinds Christopher asks
    for by name (festivals, movie nights, anniversaries, releases), then live music, then the
    rest. The list itself is always printed in time order.
    """
    kinds = c.get("kinds", ["brewery"])
    seasonal_rx = _season_pattern(c.get("seasonal") or [], w.start)
    special_rx = re.compile(c["prioritize"], re.I) if c.get("prioritize") else None
    music_rx = re.compile(c["fallback"], re.I) if c.get("fallback") else None
    limit = int(c.get("limit", 20))

    def rank(r) -> int:
        if seasonal_rx and query.matches(r, seasonal_rx):
            return 0
        if special_rx and query.matches(r, special_rx):
            return 1
        if music_rx and query.matches(r, music_rx):
            return 2
        return 3

    def pick(pool, cap):
        collapsed = _collapse(pool)
        kept = sorted(sorted(collapsed, key=lambda r: r["_sort"]), key=rank)[:cap]
        return sorted(kept, key=lambda r: r["_sort"]), [r for r in collapsed if rank(r) == 0]

    pool = _select(rows, kinds=kinds, start=w.start, end=w.end, now=w.now, exclude=c.get("exclude"))
    running = _collapse([r for r in pool if r.get("_ongoing")])
    this, seasonal = pick([r for r in pool if not r.get("_ongoing")], limit)
    # A month-long seasonal thing is still a highlight every week it runs, even though the
    # section mentions it in one line rather than listing it again.
    if seasonal_rx:
        seasonal = seasonal + [r for r in running if query.matches(r, seasonal_rx)]
    shown = {r["summary"].lower() for r in this + running}
    # highlights: seasonal and the named-favourite kinds, never routine music
    nxt_pool = _select(rows, kinds=kinds, start=w.next_start, end=w.next_end, now=None, exclude=c.get("exclude"))
    nxt = [r for r in _collapse(nxt_pool) if rank(r) <= 1 and r["summary"].lower() not in shown]
    nxt = sorted(nxt, key=lambda r: r["_sort"])[: w.preview_limit]
    return {"this": this, "seasonal": seasonal, "running": running, "next": nxt}


def towns_select(rows, c, w: Window) -> dict:
    kinds = c.get("kinds", ["town"])
    fm = c.get("farmers_markets") or {}
    fm_rx = re.compile(fm["pattern"], re.I) if fm.get("pattern") else None
    prio = c.get("prioritize")

    def rank(pool, cap):
        pool = _collapse(pool)
        if prio:
            pool.sort(key=lambda r: (0 if query.matches(r, prio) else 1, r["_sort"]))
        return sorted(pool[:cap], key=lambda r: r["_sort"])

    pool = _select(rows, kinds=kinds, start=w.start, end=w.end, now=w.now, exclude=c.get("exclude"))
    running = _collapse([r for r in pool if r.get("_ongoing")])
    this = [r for r in pool if not r.get("_ongoing")]
    markets = [r for r in this if fm_rx and fm_rx.search(query.haystack(r))]
    main = rank([r for r in this if r not in markets], int(c.get("limit", 8)))
    shown = {r["summary"].lower() for r in main + running}
    nxt = _select(rows, kinds=kinds, start=w.next_start, end=w.next_end, now=None, exclude=c.get("exclude"))
    nxt = rank([r for r in nxt if r["summary"].lower() not in shown and not (fm_rx and fm_rx.search(query.haystack(r)))], w.preview_limit)
    return {"this": main, "markets": markets, "running": running, "next": nxt}


def top_picks(F, B, T, c, w: Window) -> list[dict]:
    """The week's highlights across sections: a deliberately narrow card.

    Only two kinds qualify: marquee items (fair and festival one-offs, seasonal brewery events)
    and anything matching `pattern` - the things Christopher wants spotlit. Nothing is promoted
    to pad a quiet week; one or two picks is a fine week. Everything else still appears in its
    own section. Ranked before the cap so a Saturday festival is never cut for a Tuesday, then
    printed in time order.
    """
    rx = re.compile(c["pattern"], re.I) if c.get("pattern") else None
    cands: dict[tuple, tuple[int, dict]] = {}

    def offer(r, rank):
        key = _key(r)
        if key not in cands or rank < cands[key][0]:
            cands[key] = (rank, r)

    for r in (F or {}).get("this", []):
        offer(r, 0)
    for r in (B or {}).get("seasonal", []):
        offer(r, 0)
    # Long-running town programmes ("Summer at the Museum") are not highlights; they stay in
    # their section's Running now line.
    for src in ((B or {}).get("this", []), (T or {}).get("this", [])):
        for r in src:
            if rx and query.matches(r, rx):
                offer(r, 1)

    ranked = sorted(cands.values(), key=lambda pair: (pair[0], pair[1]["_sort"]))
    return sorted((r for _, r in ranked[: int(c.get("limit", 8))]), key=lambda r: r["_sort"])


# --------------------------------------------------------------- rendering

def pick_lines(rows, w: Window, by_slug, icons=None, overrides=None) -> list[str]:
    """Top Picks card. Events sharing a day sit under one date header, and events sharing the
    same multi-day run sit under one range header:

        **Sat Sep 26**
        🍺 **[Lovettsville Oktoberfest](url)** · 10am–5pm (Zoldos Square, Lovettsville)
        > German food and beer, stein hauling, Wiener Dog Races, Kinderfest...

        **Fri Oct 2 – Sun Oct 4**
        **[Waterford Fair](url)** (Waterford Foundation)
        > Three-day fall festival in the historic village...

        **Thu Oct 1 – Fri Oct 30**
        **[Halloweem Pop-Up Bar](url)** (Honor Brewing, Sterling)

    An all-day event on a day with no timed picks keeps the one-line form, since its date line
    would otherwise carry nothing:

        **Thu Sep 24** — **[Beer Release: Oktoberfest](url)** (Chilly Hollow, Berryville)
    """
    rows = sorted(rows, key=lambda r: r["_sort"])
    # A day gets a header only if something that day carries a time; otherwise its all-day
    # events read better inline, and the date never appears twice for the same day.
    timed_days = {_first_day(r) for r in rows if not r["all_day"] and not _is_span(r)}
    # Likewise a date range gets a header only when more than one pick runs exactly that range.
    span_runs: dict[tuple, list] = {}
    for r in rows:
        if _is_span(r):
            span_runs.setdefault(_run(r), []).append(r)

    out: list[str] = []
    cur_day: date | None = None
    done: set[int] = set()
    for r in rows:
        if id(r) in done:
            continue
        s_dt = datetime.fromisoformat(r["start"])
        day = s_dt.date()

        if _is_span(r):
            first, last = _run(r)
            if out:
                out.append("")
            out.append(f"**{_d(first)} – {_d(last)}**{_rel(first, w)}")
            for i, peer in enumerate(span_runs[_run(r)]):      # everything on exactly this run
                done.add(id(peer))
                if i:
                    out.append("")
                out.extend(_title_and_excerpt(peer, by_slug, icons, overrides))
            cur_day = None                      # a later pick on this day re-prints its header
            continue

        if out:
            out.append("")
        if day not in timed_days:
            e_dt = datetime.fromisoformat(r["end"])
            when = f"**{_d(day)}**" + ("" if r["all_day"] else f", {_span(s_dt, e_dt)}")
            out.append(f"{when} — {_icon(r, icons)}**{_link(r['summary'], _url(r, by_slug))}** ({_where(r, by_slug)}){_rel(day, w)}")
            _append_excerpt(out, r, overrides)
            cur_day = None
        else:
            if day != cur_day:
                out.append(f"**{_d(day)}**{_rel(day, w)}")
                cur_day = day
            out.extend(_title_and_excerpt(r, by_slug, icons, overrides))
    return out


def _title_and_excerpt(r, by_slug, icons=None, overrides=None) -> list[str]:
    """`🎃 **Title** · time (Venue)` plus the excerpt, for an event under a date or range header."""
    when = ""
    if not r["all_day"]:
        s_dt, e_dt = datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])
        when = f" · from {_clock(s_dt)}" if _is_span(r) else f" · {_span(s_dt, e_dt)}"
    lines = [f"{_icon(r, icons)}**{_link(r['summary'], _url(r, by_slug))}**{when} ({_where(r, by_slug)})"]
    _append_excerpt(lines, r, overrides)
    return lines


def _append_excerpt(lines: list[str], r, overrides=None) -> None:
    excerpt = blurb(r, overrides)
    if excerpt:
        lines.append(f"> {excerpt}")


def blurb(r, overrides=None) -> str:
    """What we say about an event: a curated override (read off its poster) wins over the feed."""
    o = override_for(r, overrides) if overrides else None
    if o and o.get("details"):
        return re.sub(r"\s+", " ", str(o["details"])).strip()
    return _excerpt(r["description"])


def _icon(r, icons) -> str:
    """A themed emoji for a pick, or "". The title decides; the description is the tiebreaker,
    so a gig whose blurb mentions pumpkins does not become a pumpkin event."""
    if not icons:
        return ""
    for haystack in (r["summary"], r["description"]):
        for entry in icons:
            if re.search(entry["match"], haystack or "", re.I):
                return f"{entry['icon']} "
    return ""


def _url(r, by_slug) -> str:
    feed = by_slug.get(r["slug"])
    return r["url"] or (feed.url if feed else "")


def _run(r) -> tuple:
    """(first day, last day) of a multi-day event, as shown."""
    e = datetime.fromisoformat(r["end"])
    last = e.date() - timedelta(days=1) if r["all_day"] else e.date()
    return (_first_day(r), last)


def _is_span(r) -> bool:
    e = datetime.fromisoformat(r["end"])
    last = e.date() - timedelta(days=1) if r["all_day"] else e.date()
    return last > _first_day(r)


def _rel(day: date, w: Window) -> str:
    if day == w.now.date():
        return " · Today"
    if day == w.now.date() + timedelta(days=1):
        return " · Tomorrow"
    return ""


def grouped_lines(rows, w: Window, by_slug, overrides=None) -> list[str]:
    """Date header once, then the day's events as a list beneath it:

        **Sat Sep 26** · Tomorrow
        - [Honorfest](url) · 11am–11pm (Honor Brewing, Sterling): excerpt

    Multi-day events sit under their first visible day with "thru <last day>"."""
    lines: list[str] = []
    last = None
    for r in sorted(rows, key=lambda r: (max(_first_day(r), w.start), r["_sort"])):
        day = max(_first_day(r), w.start)
        if day != last:
            lines.append(("" if last is None else "\u200b\n") + f"**{_d(day)}**{_rel(day, w)}")   # zero-width line = spacing in Discord
            last = day
        lines.append("- " + item_text(r, by_slug, overrides))
    return lines


def item_text(r, by_slug, overrides=None) -> str:
    s = datetime.fromisoformat(r["start"]); e = datetime.fromisoformat(r["end"])
    last = e.date() - timedelta(days=1) if r["all_day"] else e.date()
    if last > s.date():
        when = f"thru {_d(last)}" + ("" if r["all_day"] else f", from {_clock(s)}")
    elif r["all_day"]:
        when = ""
    else:
        when = _span(s, e)
    more = r.get("_more") or []
    also = " (also " + ", ".join(f"{d:%a}" for d in more[:3]) + (f" +{len(more) - 3}" if len(more) > 3 else "") + ")" if more else ""
    feed = by_slug.get(r["slug"])
    url = r["url"] or (feed.url if feed else "")
    title = _link(r["summary"], url)
    head = f"{title} · {when}" if when else title          # same shape as Top Picks: title, then time
    excerpt = blurb(r, overrides)
    return f"{head} ({_where(r, by_slug)})" + (f": {excerpt}" if excerpt else "") + also


def _first_day(r) -> date:
    return datetime.fromisoformat(r["start"]).date()


def _key(r) -> tuple:
    return (r["summary"].lower(), r["calendar"])


def _blocks(*blocks: list[str]) -> list[str]:
    """Join the parts of a section, a blank line between each, skipping the empty ones."""
    out: list[str] = []
    for block in blocks:
        if not block:
            continue
        if out:
            out.append("")
        out.extend(block)
    return out


def _running_line(rows, by_slug) -> list[str]:
    """One line for things that have been running since before this week and continue past it,
    so a month-long pop-up bar is mentioned without being re-listed in full every Monday."""
    if not rows:
        return []
    return ["**Running now:**"] + [              # label, then one per line, as with ongoing weekends
        f"{_link(_short_title(r['summary']), _url(r, by_slug))} thru {_d(_run(r)[1])}" for r in rows]


def _preview(rows, w: Window) -> list[str]:
    """One compact line of next week's highlights: title (day) · title (day)."""
    if not rows:
        return []
    bits = [f"{_link(_short_title(r['summary']), r['url'])} ({datetime.fromisoformat(r['start']):%a})" for r in rows[: w.preview_limit]]
    return [f"**Next week ({_d(w.next_start)} – {_d(w.next_end)}):** " + " · ".join(bits)]


def _market_line(occ, by_slug) -> str:
    """Leesburg Saturday Farmers Market · Sat 8am–12pm, Sun 9am–12pm (Virginia Village, Leesburg)"""
    first = occ[0]
    times = []
    for o in occ:
        s = datetime.fromisoformat(o["start"]); e = datetime.fromisoformat(o["end"])
        times.append(f"{s:%a}" + ("" if o["all_day"] else f" {_span(s, e)}"))
    return f"- {_link(first['summary'], first['url'])} · {', '.join(times)} ({_where(first, by_slug)})"


# --------------------------------------------------------------------- helpers

def _select(rows, *, kinds, start, end, now, exclude):
    """Rows of the given kinds overlapping [start, end] (inclusive days), minus noise.

    `now` (a tz-aware datetime, or None) drops anything already finished on the run day.
    Multi-week things that began before the window (a month-long pop-up bar, a museum programme)
    are tagged `_ongoing` so a section can mention them in one line instead of listing them in
    full every week; a fair that started a few days ago and is still running stays in as normal.
    """
    ex = re.compile(exclude, re.I) if exclude else None
    out = []
    for r in rows:
        if r["kind"] not in kinds:
            continue
        s_dt = datetime.fromisoformat(r["start"]); e_dt = datetime.fromisoformat(r["end"])
        s, e = s_dt.date(), (e_dt.date() - timedelta(days=1) if r["all_day"] else e_dt.date())
        if e < start or s > end:
            continue
        if now is not None and not r["all_day"] and e_dt <= now:
            continue                                    # already over today
        if ex and ex.search(query.title_haystack(r)):
            continue        # title only: see query.title_haystack
        if s < start and (e - s).days > 14 and not r["series"]:
            r = {**r, "_ongoing": True}                 # began before this week and runs for weeks
        out.append(r)
    return out


def _group(rows) -> dict[tuple, list]:
    grouped: dict[tuple, list] = {}
    for r in sorted(rows, key=lambda r: r["_sort"]):
        grouped.setdefault((r["summary"].lower(), r["calendar"]), []).append(r)
    return grouped


def _collapse(rows):
    """One row per (title, calendar), keeping the earliest occurrence and counting the rest."""
    out = []
    for occ in _group(rows).values():
        first = dict(occ[0])
        first["_more"] = [datetime.fromisoformat(o["start"]) for o in occ[1:]]
        out.append(first)
    return out


def _season_pattern(seasons, start: date):
    for s in seasons:
        if start.month in [int(m) for m in s.get("months", [])]:
            return re.compile(s["pattern"], re.I)
    return None


def _where(r, by_slug) -> str:
    feed = by_slug.get(r["slug"])
    loc = r.get("location", "")
    town = (feed.town if feed and feed.town else "") or _town_from(loc)
    if feed and feed.short_name:
        venue = feed.short_name
    else:
        first = re.split(r",| @ | \(", loc, maxsplit=1)[0].strip()
        if first and not first[0].isdigit():
            venue = first
        elif feed and feed.source and feed.source.get("type") == "manual":
            venue = ""                                  # curated list: the town says it all
        else:
            venue = feed.name if feed else r["calendar"]
    if venue and town and town.lower() not in venue.lower():
        return f"{venue}, {town}"
    return venue or town or (feed.name if feed else r["calendar"])


def _town_from(location: str) -> str:
    m = re.search(r",\s*([A-Za-z .'-]+?),?\s+VA\b", location)
    return m.group(1).strip() if m else ""


def load_overrides(root) -> list[dict]:
    """config/events/overrides.yaml: details read off event artwork, keyed by a title pattern."""
    import yaml

    path = Path(root) / "config" / "events" / "overrides.yaml"
    if not path.exists():
        return []
    return list((yaml.safe_load(path.read_text()) or {}).get("events") or [])


def load_notices(root) -> list[dict]:
    """config/events/overrides.yaml `notices:`: offers and announcements spotted on a venue's
    Instagram whose detail (a date, a promo code) is caption-only, so Christopher is pointed at
    the app rather than given a guess."""
    import yaml

    path = Path(root) / "config" / "events" / "overrides.yaml"
    if not path.exists():
        return []
    return list((yaml.safe_load(path.read_text()) or {}).get("notices") or [])


def notice_lines(notices, today: date, kind: str) -> list[str]:
    """The live notices for one section, as a block."""
    live = []
    for n in notices or []:
        if (n.get("kind") or "brewery") != kind:
            continue
        until = n.get("until")
        if until and date.fromisoformat(str(until)[:10]) < today:
            continue
        text = re.sub(r"\s+", " ", str(n["text"])).strip()
        where = f"**{n['venue']}:** " if n.get("venue") else ""
        handle = f" ({_link('@' + n['instagram'], 'https://www.instagram.com/' + n['instagram'] + '/')})" if n.get("instagram") else ""
        live.append(f"{where}{text}{handle}")
    return ["📣 **Heads up:**"] + live if live else []


def override_for(row, overrides) -> dict | None:
    """The first override whose `match` hits this event's title, and whose `calendar` (if given)
    matches too. An `on` date pins it to one occurrence of a repeating title."""
    for o in overrides or []:
        if not re.search(o["match"], row["summary"], re.I):
            continue
        if o.get("calendar") and o["calendar"].lower() not in row["calendar"].lower():
            continue
        if o.get("on") and str(o["on"]) != row["start"][:10]:
            continue
        return o
    return None


def _excerpt(desc: str) -> str:
    text = html_to_text(desc or "")
    text = _OUR_TRAILERS.sub("", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip(" .-–")
    if not text:
        return ""
    if len(text) <= EXCERPT_CHARS:
        return text if text[-1] in ".!?" else text + "."
    cut = text[:EXCERPT_CHARS]
    # prefer ending at a sentence, else a word
    m = re.search(r"^(.{80,}?[.!?])\s", cut)
    return (m.group(1) if m else cut.rsplit(" ", 1)[0] + "…")


def _short_title(t: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*$", "", t)


def _d(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def _clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").lower().replace(":00", "")


def _span(s: datetime, e: datetime) -> str:
    a, b = _clock(s), _clock(e)
    if a[-2:] == b[-2:]:                       # "1–4pm" not "1pm–4pm"
        a = a[:-2]
    return f"{a}–{b}"


def _link(text: str, url: str) -> str:
    text = text.replace("[", "(").replace("]", ")")
    return f"[{text}]({url})" if url.startswith("http") else text


# -------------------------------------------------------------------- render

def render_text(d: Digest) -> str:
    out = [f"{d.title} — {_d(d.start)} to {_d(d.end)}", ""]
    for s in d.sections:
        out.append(f"## {s.emoji} {s.label}")
        out.extend(s.lines or ["(nothing this week)"])
        out.append("")
    if d.index_url:
        out.append(f"All calendars: {d.index_url}")
    if d.errors:
        out.append(f"({d.errors} calendar(s) could not be loaded)")
    return "\n".join(out)


def discord_payloads(d: Digest) -> list[dict]:
    """Header message + one embed per section (split if a section is long)."""
    header = f"📅 **{d.title}** — {_d(d.start)} to {_d(d.end)}" + (" (the rest of this week)" if d.start.weekday() > 0 else "")
    if d.index_url:
        header += f"\nAll calendars and subscribe links: <{d.index_url}>"
    payloads = [{"content": header[:DISCORD_MAX_CONTENT]}]
    for s in d.sections:
        chunks = _chunk(s.lines or ["Nothing matched this week."], DISCORD_MAX_EMBED_DESC)
        for i, chunk in enumerate(chunks):
            title = f"{s.emoji} {s.label}" + (f" ({i + 1}/{len(chunks)})" if len(chunks) > 1 else "")
            payloads.append({"embeds": [{"title": title, "description": chunk, "color": s.color}]})
    if d.errors:
        payloads.append({"content": f"⚠️ {d.errors} calendar(s) could not be loaded this run."})
    return payloads


def post(webhook_url: str, payloads: list[dict]) -> None:
    for payload in payloads:
        _post(webhook_url, payload)


DISCORD_API = "https://discord.com/api/v10"


def purge_channel(bot_token: str, channel_id: str) -> int:
    """Delete every non-pinned message in the channel so the new digest is the only thing there.

    Needs a bot in the server with Manage Messages + Read Message History on the channel. The
    bulk-delete endpoint only accepts messages younger than 14 days; older ones go one by one.
    Same approach as JobHunt's newsletter purge.
    """
    headers = {"Authorization": f"Bot {bot_token}"}
    deleted = 0
    for _ in range(20):                                   # safety cap: 2,000 messages
        resp = _discord(requests.get, f"{DISCORD_API}/channels/{channel_id}/messages", headers=headers, params={"limit": 100})
        msgs = [m for m in resp.json() if not m.get("pinned")]
        if not msgs:
            break
        young = [m["id"] for m in msgs if _snowflake_age_days(m["id"]) < 13.5]
        old = [m["id"] for m in msgs if m["id"] not in set(young)]
        if len(young) >= 2:
            _discord(requests.post, f"{DISCORD_API}/channels/{channel_id}/messages/bulk-delete", headers=headers, json={"messages": young})
            deleted += len(young)
        elif len(young) == 1:
            _discord(requests.delete, f"{DISCORD_API}/channels/{channel_id}/messages/{young[0]}", headers=headers)
            deleted += 1
        for mid in old:
            _discord(requests.delete, f"{DISCORD_API}/channels/{channel_id}/messages/{mid}", headers=headers)
            deleted += 1
            _time.sleep(0.4)                               # individual deletes are rate-limited
        if len(resp.json()) < 100:
            break
    return deleted


def _discord(method, url, **kw):
    """One Discord REST call with 429 handling; raises on other errors."""
    for _ in range(5):
        resp = method(url, timeout=30, **kw)
        if resp.status_code == 429:
            try:
                wait = float(resp.json().get("retry_after", 1.0))
            except Exception:
                wait = 1.0
            _time.sleep(min(wait + 0.1, 5.0))
            continue
        if resp.status_code >= 300:
            raise RuntimeError(f"Discord API {resp.status_code} on {url.split('/v10')[-1]}: {resp.text[:200]}")
        return resp
    raise RuntimeError("Discord API still rate-limited after 5 retries")


def webhook_id(webhook_url: str) -> str | None:
    """The numeric id out of https://discord.com/api/webhooks/<id>/<token> (never the token)."""
    m = re.search(r"/webhooks/(\d+)/", webhook_url or "")
    return m.group(1) if m else None


def posted_since(bot_token: str, channel_id: str, since: datetime, our_webhook_id: str | None) -> datetime | None:
    """When this digest's webhook last posted in the channel at or after `since`, if it did.

    GitHub drops or delays scheduled runs by hours, so the digest has several Monday triggers;
    this lets the later ones stand down once one has done the job.

    Matching is by `webhook_id`, not message text: reading message content over the API needs
    Discord's privileged Message Content intent, which this bot does not have, so `content` and
    `embeds` come back empty. The channel is purged before each post, so anything still there
    from our webhook is this week's digest.
    """
    resp = _discord(requests.get, f"{DISCORD_API}/channels/{channel_id}/messages",
                    headers={"Authorization": f"Bot {bot_token}"}, params={"limit": 50})
    msgs = resp.json()
    for m in msgs:                                         # newest first
        if our_webhook_id and m.get("webhook_id") != our_webhook_id:
            continue
        if not our_webhook_id and not m.get("webhook_id"):
            continue                                       # unknown webhook: accept any app post
        ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
        if ts >= since:
            return ts
    return None


def _snowflake_age_days(message_id: str) -> float:
    ts_ms = (int(message_id) >> 22) + 1420070400000
    return (datetime.now(timezone.utc).timestamp() - ts_ms / 1000) / 86400


def _post(webhook_url: str, payload: dict) -> None:
    for _ in range(5):
        resp = requests.post(webhook_url, json=payload, timeout=30)
        if resp.status_code == 429:                       # honour Discord's retry_after
            try:
                wait = float(resp.json().get("retry_after", 1.0))
            except Exception:
                wait = 1.0
            _time.sleep(min(wait + 0.1, 5.0))
            continue
        if resp.status_code >= 300:
            raise RuntimeError(f"Discord webhook failed: HTTP {resp.status_code} {resp.text[:300]}")
        _time.sleep(0.4)
        return
    raise RuntimeError("Discord webhook still rate-limited after 5 retries")


def _chunk(lines: list[str], limit: int) -> list[str]:
    chunks, cur = [], ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks

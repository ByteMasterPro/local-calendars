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

    # Pass 1: what each section would show. Pass 2: pick the week's highlights across them for the
    # Top Picks card. Highlights also appear in full in their own section; repetition is intended.
    F = fairs_select(rows, secs["fairs"], w) if "fairs" in secs else None
    B = breweries_select(rows, secs["breweries"], w) if "breweries" in secs else None
    T = towns_select(rows, secs["towns"], w) if "towns" in secs else None
    picks = top_picks(F, B, T, cfg_d.get("top_picks") or {}, w)

    sections = []
    if picks:
        c = cfg_d.get("top_picks") or {}
        sections.append(Section("picks", c.get("label", "Top picks this week"), "⭐", 0xE67E22, pick_lines(picks, w, by_slug)))
    if F:
        c = secs["fairs"]
        lines = grouped_lines(F["this"], w, by_slug)
        if F["ongoing"]:
            bits = []
            for r in F["ongoing"]:
                until = f" thru {_d(date.fromisoformat(r['series_until']))}" if r.get("series_until") else ""
                bits.append(f"{_link(_short_title(r['summary']), r['url'])}{until}")
            lines += ["", "**Ongoing weekends:** " + " · ".join(bits)]
        sections.append(Section("fairs", c.get("label", "Fairs, Festivals and Carnivals"), "🎪", 0x2A8FBD, lines + _preview(F["next"], w)))
    if B:
        c = secs["breweries"]
        sections.append(Section("breweries", c.get("label", "Local Breweries"), "🍺", 0xF39C12,
                                grouped_lines(B["this"], w, by_slug) + _preview(B["next"], w)))
    if T:
        c = secs["towns"]
        lines = grouped_lines(T["this"], w, by_slug)
        if T["markets"]:
            lines += ["", f"🥕 **{(c.get('farmers_markets') or {}).get('label', 'Farmers Markets')}:**"]
            lines += [_market_line(occ, by_slug) for occ in _group(T["markets"]).values()]
        sections.append(Section("towns", c.get("label", "Town Activities"), "🏘️", 0x27AE60, lines + _preview(T["next"], w)))

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
    one_offs = _collapse([r for r in this if not r["series"]])[: int(c.get("limit", 8))]
    ongoing = _collapse([r for r in this if r["series"]])
    shown = {r["summary"].lower() for r in one_offs + ongoing}
    nxt = _collapse([r for r in _select(rows, kinds=kinds, start=w.next_start, end=w.next_end, now=None, exclude=c.get("exclude"))
                     if r["summary"].lower() not in shown])
    return {"this": one_offs, "ongoing": ongoing, "next": nxt}


def breweries_select(rows, c, w: Window) -> dict:
    kinds = c.get("kinds", ["brewery"])
    seasonal_rx = _season_pattern(c.get("seasonal") or [], w.start)
    limit, fill_below = int(c.get("limit", 8)), int(c.get("fill_below", 3))

    def pick(pool, cap, fill_below):
        seasonal = _collapse([r for r in pool if query.matches(r, seasonal_rx)]) if seasonal_rx else []
        chosen = list(seasonal)
        # Music is the fallback when the season isn't giving us much, not a permanent top-up.
        if len(chosen) < fill_below and c.get("fallback"):
            seen = {_key(r) for r in chosen}
            fill = [r for r in _collapse([r for r in pool if query.matches(r, c["fallback"])]) if _key(r) not in seen]
            chosen = sorted(chosen + fill[: cap - len(chosen)], key=lambda r: r["_sort"])
        return chosen[:cap], seasonal

    this, seasonal = pick(_select(rows, kinds=kinds, start=w.start, end=w.end, now=w.now, exclude=c.get("exclude")), limit, fill_below)
    shown = {r["summary"].lower() for r in this}
    # highlights: seasonal only; music only if the following week has nothing seasonal at all
    nxt, _ = pick(_select(rows, kinds=kinds, start=w.next_start, end=w.next_end, now=None, exclude=c.get("exclude")), w.preview_limit, 1)
    nxt = [r for r in nxt if r["summary"].lower() not in shown]
    return {"this": this, "seasonal": seasonal, "next": nxt}


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

    this = _select(rows, kinds=kinds, start=w.start, end=w.end, now=w.now, exclude=c.get("exclude"))
    markets = [r for r in this if fm_rx and fm_rx.search(query.haystack(r))]
    main = rank([r for r in this if r not in markets], int(c.get("limit", 8)))
    shown = {r["summary"].lower() for r in main}
    nxt = _select(rows, kinds=kinds, start=w.next_start, end=w.next_end, now=None, exclude=c.get("exclude"))
    nxt = rank([r for r in nxt if r["summary"].lower() not in shown and not (fm_rx and fm_rx.search(query.haystack(r)))], w.preview_limit)
    return {"this": main, "markets": markets, "next": nxt}


def top_picks(F, B, T, c, w: Window) -> list[dict]:
    """The week's highlights across sections: fair one-offs, seasonal brewery events, and town
    items matching the top-pick pattern (parades, airshows, festivals). Chronological, capped."""
    rx = re.compile(c["pattern"], re.I) if c.get("pattern") else None
    cands: dict[tuple, dict] = {}
    for r in (F or {}).get("this", []):
        cands.setdefault(_key(r), r)
    for r in (B or {}).get("seasonal", []):
        cands.setdefault(_key(r), r)
    for r in (T or {}).get("this", []):
        if rx and query.matches(r, rx):
            cands.setdefault(_key(r), r)
    picks = sorted(cands.values(), key=lambda r: r["_sort"])
    return picks[: int(c.get("limit", 5))]


# --------------------------------------------------------------- rendering

def pick_lines(rows, w: Window, by_slug) -> list[str]:
    """Top Picks card: one event per block, date line first, excerpt quoted beneath it.

        **Sat Sep 26**, 10am–5pm — [Lovettsville Oktoberfest](url) (Zoldos Square, Lovettsville)
        > German food and beer, stein hauling, Wiener Dog Races, Kinderfest...
    """
    out: list[str] = []
    for r in rows:
        s_dt = datetime.fromisoformat(r["start"]); e_dt = datetime.fromisoformat(r["end"])
        last = e_dt.date() - timedelta(days=1) if r["all_day"] else e_dt.date()
        if last > s_dt.date():
            when = f"**{_d(s_dt.date())} – {_d(last)}**" + ("" if r["all_day"] else f", from {_clock(s_dt)}")
        elif r["all_day"]:
            when = f"**{_d(s_dt.date())}**"
        else:
            when = f"**{_d(s_dt.date())}**, {_span(s_dt, e_dt)}"
        rel = " · Today" if s_dt.date() == w.now.date() else " · Tomorrow" if s_dt.date() == w.now.date() + timedelta(days=1) else ""
        feed = by_slug.get(r["slug"])
        url = r["url"] or (feed.url if feed else "")
        if out:
            out.append("")
        out.append(f"{when} — {_link(r['summary'], url)} ({_where(r, by_slug)}){rel}")
        excerpt = _excerpt(r["description"])
        if excerpt:
            out.append(f"> {excerpt}")
    return out


def grouped_lines(rows, w: Window, by_slug) -> list[str]:
    """Date header once, then the day's events as a list beneath it:

        **Sat Sep 26** · Tomorrow
        - 11am–11pm — [Honorfest](url) (Honor Brewing, Sterling): excerpt

    Multi-day events sit under their first visible day with "thru <last day>"."""
    lines: list[str] = []
    last = None
    for r in sorted(rows, key=lambda r: (max(_first_day(r), w.start), r["_sort"])):
        day = max(_first_day(r), w.start)
        if day != last:
            rel = " · Today" if day == w.now.date() else " · Tomorrow" if day == w.now.date() + timedelta(days=1) else ""
            lines.append(("" if last is None else "\u200b\n") + f"**{_d(day)}**{rel}")   # zero-width line = spacing in Discord
            last = day
        lines.append("- " + item_text(r, by_slug))
    return lines


def item_text(r, by_slug) -> str:
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
    head = f"{when} — {title}" if when else title
    excerpt = _excerpt(r["description"])
    return f"{head} ({_where(r, by_slug)})" + (f": {excerpt}" if excerpt else "") + also


def _first_day(r) -> date:
    return datetime.fromisoformat(r["start"]).date()


def _key(r) -> tuple:
    return (r["summary"].lower(), r["calendar"])


def _preview(rows, w: Window) -> list[str]:
    """One compact line of next week's highlights: title (day) · title (day)."""
    if not rows:
        return []
    bits = [f"{_link(_short_title(r['summary']), r['url'])} ({datetime.fromisoformat(r['start']):%a})" for r in rows[: w.preview_limit]]
    return ["", f"**Next week ({_d(w.next_start)} – {_d(w.next_end)}):** " + " · ".join(bits)]   # blank line separates it


def _market_line(occ, by_slug) -> str:
    """Leesburg Saturday Farmers Market — Sat 8am–12pm, Sun 9am–12pm (Virginia Village, Leesburg)"""
    first = occ[0]
    times = []
    for o in occ:
        s = datetime.fromisoformat(o["start"]); e = datetime.fromisoformat(o["end"])
        times.append(f"{s:%a}" + ("" if o["all_day"] else f" {_span(s, e)}"))
    return f"- {_link(first['summary'], first['url'])} — {', '.join(times)} ({_where(first, by_slug)})"


# --------------------------------------------------------------------- helpers

def _select(rows, *, kinds, start, end, now, exclude):
    """Rows of the given kinds overlapping [start, end] (inclusive days), minus noise.

    `now` (a tz-aware datetime, or None) drops anything already finished on the run day.
    Multi-week things that began before the window (exhibits, programs) are left out; a fair
    that started a few days ago and is still running stays in.
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
        if s < start and (e - s).days > 14 and not r["series"]:
            continue                                    # long-running thing that began weeks ago
        if now is not None and not r["all_day"] and e_dt <= now:
            continue                                    # already over today
        if ex and ex.search(query.haystack(r)):
            continue
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

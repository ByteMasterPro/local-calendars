"""Weekly Discord digest, three sections Christopher specified:

  Fairs, Festivals and Carnivals   next 5 upcoming one-offs (any date) + one "ongoing weekends" line
  Local Breweries                  seasonal first (Sep-Oct Oktoberfest/German/Halloween, Nov-Dec holiday),
                                   topped up with live music; never karaoke/trivia/discount noise
  Town Activities                  this week, capped, meetings and farmers markets excluded

Every line reads:  **Sun Sep 20**, 1–4pm — [Title](url) (Venue, Town): excerpt. Tomorrow.
Settings live under `digest:` in config/calendars.yaml.
"""

from __future__ import annotations

import logging
import re
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

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
    end: date                 # inclusive last day of the "this week" window
    sections: list[Section]
    index_url: str
    errors: int = 0


# ------------------------------------------------------------------------ build

def build(cfg: Config, feeds: list[Feed], start: date, days: int | None = None) -> Digest:
    cfg_d = cfg.digest or {}
    secs = cfg_d.get("sections") or {}
    week_days = days or int(cfg_d.get("days", 7))
    horizon = max([week_days] + [int(v.get("days", 0)) for v in secs.values()] + [int(secs.get("fairs", {}).get("horizon_days", 0))])
    rows, errors = query.gather(feeds, start, start + timedelta(days=horizon))
    by_slug = {f.slug: f for f in feeds}

    sections = []
    if "fairs" in secs:
        c = secs["fairs"]
        sections.append(Section("fairs", c.get("label", "Fairs, Festivals and Carnivals"), "🎪", 0x2A8FBD,
                                fairs_lines(rows, c, start, by_slug)))
    if "breweries" in secs:
        c = secs["breweries"]
        sections.append(Section("breweries", c.get("label", "Local Breweries"), "🍺", 0xF39C12,
                                breweries_lines(rows, c, start, by_slug)))
    if "towns" in secs:
        c = secs["towns"]
        sections.append(Section("towns", c.get("label", "Town Activities"), "🏘️", 0x27AE60,
                                towns_lines(rows, c, start, by_slug)))
    return Digest(title=cfg_d.get("title", "This week"), start=start, end=start + timedelta(days=week_days - 1),
                  sections=sections, index_url=cfg.site.get("base_url", ""), errors=errors)


def fairs_lines(rows, c, start, by_slug) -> list[str]:
    horizon = start + timedelta(days=int(c.get("horizon_days", 120)))
    pool = _select(rows, kinds=c.get("kinds", ["festival"]), start=start, end=horizon, exclude=c.get("exclude"))
    one_offs = _collapse([r for r in pool if not r["series"]])
    ongoing = _collapse([r for r in pool if r["series"]])
    lines = [format_line(r, start, by_slug) for r in one_offs[: int(c.get("limit", 5))]]
    if ongoing:
        bits = []
        for r in ongoing:
            until = f" thru {_d(date.fromisoformat(r['series_until']))}" if r.get("series_until") else ""
            bits.append(f"{_link(_short_title(r['summary']), r['url'])}{until}")
        lines.append("**Ongoing weekends:** " + " · ".join(bits))
    return lines


def breweries_lines(rows, c, start, by_slug) -> list[str]:
    end = start + timedelta(days=int(c.get("days", 14)))
    pool = _select(rows, kinds=c.get("kinds", ["brewery"]), start=start, end=end, exclude=c.get("exclude"))
    seasonal_rx = _season_pattern(c.get("seasonal") or [], start)
    picked = _collapse([r for r in pool if query.matches(r, seasonal_rx)]) if seasonal_rx else []
    limit = int(c.get("limit", 8))
    # Music is the fallback when the season isn't giving us much, not a permanent top-up.
    if len(picked) < int(c.get("fill_below", 3)) and c.get("fallback"):
        seen = {(r["summary"].lower(), r["calendar"]) for r in picked}
        fill = [r for r in _collapse([r for r in pool if query.matches(r, c["fallback"])])
                if (r["summary"].lower(), r["calendar"]) not in seen]
        picked = sorted(picked + fill[: limit - len(picked)], key=lambda r: r["_sort"])
    return [format_line(r, start, by_slug) for r in picked[:limit]]


def towns_lines(rows, c, start, by_slug) -> list[str]:
    end = start + timedelta(days=int(c.get("days", 7)))
    pool = _collapse(_select(rows, kinds=c.get("kinds", ["town"]), start=start, end=end, exclude=c.get("exclude")))
    prio = c.get("prioritize")
    if prio:
        pool.sort(key=lambda r: (0 if query.matches(r, prio) else 1, r["_sort"]))
    picked = sorted(pool[: int(c.get("limit", 8))], key=lambda r: r["_sort"])
    return [format_line(r, start, by_slug) for r in picked]


# --------------------------------------------------------------------- helpers

def _select(rows, *, kinds, start, end, exclude):
    ex = re.compile(exclude, re.I) if exclude else None
    out = []
    for r in rows:
        if r["kind"] not in kinds:
            continue
        s = datetime.fromisoformat(r["start"]).date()
        e = datetime.fromisoformat(r["end"]).date()
        if e < start or s >= end:
            continue
        if s < start and (e - s).days > 3 and not r["series"]:
            continue                                    # long-running thing that began weeks ago
        if ex and ex.search(query.haystack(r)):
            continue
        out.append(r)
    return out


def _collapse(rows):
    """One row per (title, calendar), keeping the earliest occurrence and counting the rest."""
    grouped: dict[tuple, list] = {}
    for r in sorted(rows, key=lambda r: r["_sort"]):
        grouped.setdefault((r["summary"].lower(), r["calendar"]), []).append(r)
    out = []
    for occ in grouped.values():
        first = dict(occ[0])
        first["_more"] = [datetime.fromisoformat(o["start"]) for o in occ[1:]]
        out.append(first)
    return out


def _season_pattern(seasons, start: date):
    for s in seasons:
        if start.month in [int(m) for m in s.get("months", [])]:
            return re.compile(s["pattern"], re.I)
    return None


def format_line(r, today: date, by_slug: dict[str, Feed]) -> str:
    s = datetime.fromisoformat(r["start"]); e = datetime.fromisoformat(r["end"])
    last = e.date() - timedelta(days=1) if r["all_day"] else e.date()
    if last > s.date():
        when = f"**{_d(s.date())} – {_d(last)}**"
        if not r["all_day"]:
            when += f", from {_clock(s)}"
    elif r["all_day"]:
        when = f"**{_d(s.date())}**"
    else:
        when = f"**{_d(s.date())}**, {_span(s, e)}"
    more = r.get("_more") or []
    if more:
        when += " (also " + ", ".join(f"{d:%a}" for d in more[:3]) + (f" +{len(more) - 3}" if len(more) > 3 else "") + ")"
    where = _where(r, by_slug)
    excerpt = _excerpt(r["description"])
    tail = f": {excerpt}" if excerpt else ""
    rel = " Today." if s.date() == today else " Tomorrow." if s.date() == today + timedelta(days=1) else ""
    feed = by_slug.get(r["slug"])
    url = r["url"] or (feed.url if feed else "")
    return f"{when} — {_link(r['summary'], url)} ({where}){tail}{rel}"


def _where(r, by_slug) -> str:
    feed = by_slug.get(r["slug"])
    loc = r.get("location", "")
    town = (feed.town if feed and feed.town else "") or _town_from(loc)
    if feed and feed.short_name:
        venue = feed.short_name
    else:
        first = re.split(r",| @ | \(", loc, maxsplit=1)[0].strip()
        venue = first if first and not first[0].isdigit() else (feed.name if feed else r["calendar"])
    if town and town.lower() not in venue.lower():
        return f"{venue}, {town}"
    return venue


def _town_from(location: str) -> str:
    m = re.search(r",\s*([A-Za-z .'-]+?),?\s+VA\b", location)
    return m.group(1).strip() if m else ""


def _excerpt(desc: str) -> str:
    text = html_to_text(desc or "")
    text = _OUR_TRAILERS.sub("", text)
    text = re.sub(r"https?://\S+", "", text)
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
    header = f"📅 **{d.title}** — {_d(d.start)} to {_d(d.end)}"
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

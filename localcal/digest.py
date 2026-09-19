"""Weekly Discord digest: "Recommended For You" + "Other Family Events", built from the same
live query `localcal upcoming` uses. Settings live under `digest:` in config/calendars.yaml.
"""

from __future__ import annotations

import json
import logging
import re
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import requests

from localcal.model import Config, Feed
from localcal import query

log = logging.getLogger(__name__)

DISCORD_MAX_CONTENT = 2000
DISCORD_MAX_EMBED_DESC = 4000      # hard limit is 4096; leave headroom
COLORS = {"recommended": 0xF39C12, "family": 0x2A8FBD}


@dataclass
class Section:
    key: str
    label: str
    lines: list[str] = field(default_factory=list)


@dataclass
class Digest:
    title: str
    start: date
    end: date                 # inclusive last day shown
    sections: list[Section]
    index_url: str
    errors: int = 0


def build(cfg: Config, feeds: list[Feed], start: date, days: int) -> Digest:
    settings = cfg.digest or {}
    end_excl = start + timedelta(days=days)
    rows, errors = query.gather(feeds, start, end_excl)

    rec_cfg = settings.get("recommended") or {}
    fam_cfg = settings.get("family") or {}
    rec_rx = re.compile(rec_cfg["pattern"], re.I) if rec_cfg.get("pattern") else None
    fam_rx = re.compile(fam_cfg["pattern"], re.I) if fam_cfg.get("pattern") else None
    excl_rx = re.compile(fam_cfg["exclude"], re.I) if fam_cfg.get("exclude") else None
    fam_kinds = set(fam_cfg.get("kinds") or [])

    recommended, family = [], []
    for r in rows:
        if excl_rx and excl_rx.search(query.haystack(r)):
            continue
        if _ongoing_since_before(r, start):
            continue          # months-long exhibit/program that began weeks ago: not "this week" news
        if query.matches(r, rec_rx):
            recommended.append(r)
        elif r["kind"] in fam_kinds or query.matches(r, fam_rx):
            family.append(r)

    sections = [
        Section("recommended", rec_cfg.get("label", "Recommended For You"), format_rows(recommended)),
        Section("family", fam_cfg.get("label", "Other Family Events"), format_rows(family)),
    ]
    return Digest(
        title=settings.get("title", "This week"),
        start=start,
        end=end_excl - timedelta(days=1),
        sections=sections,
        index_url=cfg.site.get("base_url", ""),
        errors=errors,
    )


def format_rows(rows: list[dict]) -> list[str]:
    """One line per event. Recurring/season events that hit several days in the window collapse
    into one line ("Sat Sep 26 · also Sun") so a pumpkin patch doesn't eat the whole post."""
    grouped: dict[tuple, list[dict]] = {}
    for r in rows:
        grouped.setdefault((r["summary"].lower(), r["calendar"]), []).append(r)   # same show, several nights

    lines = []
    for occ in grouped.values():
        first = occ[0]
        s = datetime.fromisoformat(first["start"]); e = datetime.fromisoformat(first["end"])
        day = f"**{s:%a %b} {s.day}**"
        if first["all_day"]:
            last = e.date() - timedelta(days=1)
            when = "" if last <= s.date() else f" thru {last:%b} {last.day}"
        elif e.date() > s.date():
            when = f" {_clock(s)} thru {e:%b} {e.day}"
        else:
            when = f" {_clock(s)}-{_clock(e)}"
        extra = ""
        if len(occ) > 1:
            others = [datetime.fromisoformat(o["start"]) for o in occ[1:]]
            extra = " · also " + ", ".join(f"{d:%a}" for d in others[:3]) + (f" +{len(others) - 3}" if len(others) > 3 else "")
        title = _link(first["summary"], first["url"])
        lines.append(f"{day}{when} · {title} — {_venue(first)}{extra}")
    return lines


def render_text(d: Digest) -> str:
    out = [f"{d.title} — {d.start:%a %b} {d.start.day} to {d.end:%a %b} {d.end.day}", ""]
    for s in d.sections:
        out.append(f"## {s.label}")
        out.extend(s.lines or ["(nothing this week)"])
        out.append("")
    if d.index_url:
        out.append(f"All calendars: {d.index_url}")
    if d.errors:
        out.append(f"({d.errors} calendar(s) could not be loaded)")
    return "\n".join(out)


def discord_payloads(d: Digest) -> list[dict]:
    """Header message + one embed per section (split if a section is long)."""
    header = f"📅 **{d.title}** — {d.start:%a %b} {d.start.day} to {d.end:%a %b} {d.end.day}"
    if d.index_url:
        header += f"\nAll calendars and subscribe links: <{d.index_url}>"
    payloads = [{"content": header[:DISCORD_MAX_CONTENT]}]
    emoji = {"recommended": "⭐", "family": "🎪"}
    for s in d.sections:
        chunks = _chunk(s.lines or ["Nothing matched this week."], DISCORD_MAX_EMBED_DESC)
        for i, chunk in enumerate(chunks):
            title = f"{emoji.get(s.key, '')} {s.label}" + (f" ({i + 1}/{len(chunks)})" if len(chunks) > 1 else "")
            payloads.append({"embeds": [{"title": title.strip(), "description": chunk, "color": COLORS.get(s.key, 0x95A5A6)}]})
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


def _ongoing_since_before(row: dict, window_start: date) -> bool:
    s = datetime.fromisoformat(row["start"]); e = datetime.fromisoformat(row["end"])
    return s.date() < window_start and (e.date() - s.date()).days > 3


def _clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").lower().replace(":00", "")


def _link(text: str, url: str) -> str:
    text = text.replace("[", "(").replace("]", ")")
    return f"[{text}]({url})" if url.startswith("http") else text


def _venue(row: dict) -> str:
    first = re.split(r",| @ | \(", row["location"], maxsplit=1)[0].strip()
    if not first or first[0].isdigit():          # bare street address: the calendar name is the venue
        return row["calendar"]
    return first

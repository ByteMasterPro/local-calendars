"""wheresthemusic.us venue pages.

A WordPress directory of DC-area live music. A venue page lists its upcoming shows with the
day, month/year, artist, genres and start time, which is more than some venues publish
themselves: Route 7 Brewing has no calendar of its own at all.

Config:
    source:
      type: wheresthemusic
      url: https://wheresthemusic.us/venue/route-7-brewing/
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

from localcal.model import Event, Feed
from localcal.text import slugify

log = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (compatible; localcal/0.1; +https://github.com/ByteMasterPro/local-calendars)"

# Each listing renders as a run of tag-separated cells: 05 | Dec 2026 | Felix Pickles |
# Blues | Country | ... | 6:00 PM
_ENTRY = re.compile(
    r"\|\s*(?P<day>\d{1,2})\s*\|\s*(?P<month>[A-Z][a-z]{2})\s+(?P<year>\d{4})\s*\|"
    r"\s*(?P<title>[^|]{2,80}?)\s*\|(?P<mid>.*?)(?P<time>\d{1,2}:\d{2}\s*[AP]M)\s*(?=\|)",   # lookahead: the delimiter starts the next listing
    re.S,
)


def fetch(url: str, session: requests.Session | None = None) -> str:
    sess = session or requests.Session()
    resp = sess.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_events(page: str, feed: Feed) -> list[Event]:
    i = page.lower().find("upcoming events")
    if i < 0:
        log.warning("%s: no upcoming-events block on the page", feed.slug)
        return []
    # The listing ends where the page's scripts begin.
    chunk = page[i : i + 20000].split("<script")[0]
    text = html.unescape(re.sub(r"<[^>]+>", "|", chunk))
    # Tag stripping leaves empty cells wherever markup was nested or indented; drop them so a
    # listing is a predictable run of day | month year | artist | genres... | time.
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\|(\s*\|)+", "|", text)

    tz = ZoneInfo(feed.timezone)
    out: list[Event] = []
    for m in _ENTRY.finditer(text):
        try:
            out.append(_event(m, feed, tz))
        except Exception as exc:
            log.warning("%s: skipping listing %r (%s)", feed.slug, m.group("title")[:40], exc)
    return out


def _event(m, feed: Feed, tz: ZoneInfo) -> Event:
    day = datetime.strptime(f"{m['day']} {m['month']} {m['year']}", "%d %b %Y").date()
    hh, mm, ampm = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", m["time"], re.I).groups()
    hour = int(hh) % 12 + (12 if ampm.upper() == "PM" else 0)
    start = datetime.combine(day, time(hour, int(mm)), tzinfo=tz)
    title = " ".join(m["title"].split())
    genres = [g.strip() for g in m["mid"].split("|") if g.strip() and not re.match(r"^\d", g.strip())]
    return Event(
        uid=f"wtm-{slugify(title)}-{day.isoformat()}@{feed.slug}",
        summary=f"Live Music: {title}",
        start=start,
        end=start + timedelta(minutes=feed.default_duration_minutes),
        description="\n\n".join(p for p in (", ".join(genres), f"Listing: {feed.source_url()}") if p),
        location=feed.location,
        url=feed.url,
        categories=["Live Music"],
    )

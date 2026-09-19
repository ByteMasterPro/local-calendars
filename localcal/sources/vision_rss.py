"""Granicus govAccess (Vision CMS) calendar RSS adapter.

Many town/city sites run this CMS (event pages look like /Home/Components/Calendar/Event/<id>/).
The HTML is often behind a bot wall, but the RSS endpoint is not:

    https://<site>/Home/Components/RssFeeds/RssFeed/View?ctID=6&cateIDs=<comma-separated category ids>

ctID=6 is the Calendar content type (5 is News). cateIDs=0 means all categories; the ids
come from the category <select> on the site's calendar page. Each item title ends with the
event's date/time in parentheses, which is the only place the RSS carries it:

    "Leesburg Airshow (09/26/2026 11:00 AM - 4:00 PM)"
    "Town Council Meeting (09/22/2026 7:00 PM)"
    "Exhibit by the Lincoln Preservation Society (10/01/2026 - 11/30/2026)"

The feed only lists the next ~2 weeks, so pair this adapter with `accumulate: true`.

Config:
    source:
      type: vision_rss
      rss_url: https://www.leesburgva.gov/Home/Components/RssFeeds/RssFeed/View?ctID=6&cateIDs=60,7
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests

from localcal.model import Event, Feed

log = logging.getLogger(__name__)
USER_AGENT = "localcal/0.1 (+https://github.com/ByteMasterPro/local-calendars)"

_DATE = r"(\d{1,2}/\d{1,2}/\d{4})"
_TIME = r"(\d{1,2}:\d{2}\s*[AP]M)"
# "(date [time] [- [date] [time]])" at the very end of the title
_WHEN = re.compile(
    rf"\s*\(\s*{_DATE}(?:\s+{_TIME})?(?:\s*-\s*(?:{_DATE})?\s*(?:{_TIME})?)?\s*\)\s*$", re.I
)
_EVENT_ID = re.compile(r"/Calendar/Event/(\d+)")
_TAGS = re.compile(r"<[^>]+>")


def fetch(rss_url: str, session: requests.Session | None = None) -> str:
    sess = session or requests.Session()
    resp = sess.get(rss_url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, text/xml"}, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_events(xml_text: str, feed: Feed) -> list[Event]:
    tz = ZoneInfo(feed.timezone)
    root = ET.fromstring(xml_text)
    out: list[Event] = []
    for item in root.iter("item"):
        try:
            ev = _parse_item(item, feed, tz)
            if ev:
                out.append(ev)
        except Exception as exc:
            log.warning("skipping item %r: %s", (item.findtext("title") or "")[:60], exc)
    return out


def _parse_item(item: ET.Element, feed: Feed, tz: ZoneInfo) -> Event | None:
    raw_title = html.unescape(item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    m = _WHEN.search(raw_title)
    if not m:
        log.warning("no date in title, skipping: %r", raw_title)
        return None
    title = raw_title[: m.start()].strip()
    d1, t1, d2, t2 = m.groups()

    start_day = _day(d1)
    end_day = _day(d2) if d2 else start_day
    all_day = not t1
    if all_day:
        start: datetime | date = start_day
        end: datetime | date = end_day + timedelta(days=1)          # DTEND exclusive
    else:
        start = datetime.combine(start_day, _clock(t1), tzinfo=tz)
        if t2:
            end = datetime.combine(end_day, _clock(t2), tzinfo=tz)
            if end <= start:                                            # "11:00 PM - 1:00 AM"
                end += timedelta(days=1)
        else:
            end = start + timedelta(minutes=feed.default_duration_minutes)

    id_match = _EVENT_ID.search(link)
    uid = f"{id_match.group(1) if id_match else abs(hash((title, str(start))))}@{feed.slug}"

    excerpt = html.unescape(_TAGS.sub("", html.unescape(item.findtext("description") or ""))).strip()
    excerpt = re.sub(r"\s+", " ", excerpt)
    parts = [excerpt, f"Details: {link}" if link else "", f"Calendar: {feed.url}"]

    return Event(
        uid=uid,
        summary=title,
        start=start,
        end=end,
        all_day=all_day,
        description="\n\n".join(p for p in parts if p),
        location=feed.location,
        url=link or feed.url,
    )


def _day(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


def _clock(s: str) -> time:
    return datetime.strptime(s.replace(" ", "").upper(), "%I:%M%p").time()

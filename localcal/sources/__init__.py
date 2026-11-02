"""Source adapter registry. Add a new adapter module here and reference its key in
config/calendars.yaml under `source.type`.

Each adapter exposes: fetch(feed) -> list[Event]
"""

from __future__ import annotations

import logging
from typing import Callable

from localcal.model import Event, Feed

log = logging.getLogger(__name__)


def _elfsight(feed: Feed, src: dict) -> list[Event]:
    from localcal.sources import elfsight

    return elfsight.parse_events(elfsight.fetch_settings(src["widget_id"]), feed)


def _vision_rss(feed: Feed, src: dict) -> list[Event]:
    from localcal.sources import vision_rss

    return vision_rss.parse_events(vision_rss.fetch(src["rss_url"]), feed)


def _squarespace(feed: Feed, src: dict) -> list[Event]:
    from localcal.sources import squarespace

    return squarespace.parse_events(squarespace.fetch(src["url"]), feed)


def _manual(feed: Feed, src: dict) -> list[Event]:
    from pathlib import Path

    from localcal.sources import manual

    root = Path(__file__).resolve().parent.parent.parent
    return manual.parse_events(manual.load(root / src["file"]), feed)


def _wheresthemusic(feed: Feed, src: dict) -> list[Event]:
    from localcal.sources import wheresthemusic

    return wheresthemusic.parse_events(wheresthemusic.fetch(src["url"]), feed)


REGISTRY: dict[str, Callable[[Feed, dict], list[Event]]] = {
    "elfsight": _elfsight,
    "vision_rss": _vision_rss,
    "squarespace": _squarespace,
    "manual": _manual,
    "wheresthemusic": _wheresthemusic,
}


def fetch_events(feed: Feed) -> list[Event]:
    """Every source on the feed, merged. A later source does not displace an earlier one's event
    with the same uid.

    One source failing does not sink the others: Route 7 merges a listings site with a curated
    file, and a read timeout on the site should not also lose the curated events. If every source
    fails, the error is raised so the caller keeps the existing feed.
    """
    seen: set[str] = set()
    out: list[Event] = []
    failures: list[Exception] = []
    for src in feed.sources:
        kind = src.get("type")
        try:
            adapter = REGISTRY[kind]
        except KeyError:
            raise SystemExit(f"{feed.slug}: unknown source type {kind!r}; known: {sorted(REGISTRY)}")
        try:
            events = adapter(feed, src)
        except Exception as exc:
            log.warning("%s: source %s failed (%s)", feed.slug, kind, str(exc)[:160])
            failures.append(exc)
            continue
        for ev in events:
            if ev.uid in seen:
                continue
            seen.add(ev.uid)
            out.append(ev)
    if failures and not out:
        raise failures[0]
    return out

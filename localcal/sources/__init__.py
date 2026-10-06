"""Source adapter registry. Add a new adapter module here and reference its key in
config/calendars.yaml under `source.type`.

Each adapter exposes: fetch(feed) -> list[Event]
"""

from __future__ import annotations

from typing import Callable

from localcal.model import Event, Feed


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
    """Every source on the feed, merged. A later source does not displace an earlier one's
    event with the same uid."""
    seen: set[str] = set()
    out: list[Event] = []
    for src in feed.sources:
        kind = src.get("type")
        try:
            adapter = REGISTRY[kind]
        except KeyError:
            raise SystemExit(f"{feed.slug}: unknown source type {kind!r}; known: {sorted(REGISTRY)}")
        for ev in adapter(feed, src):
            if ev.uid in seen:
                continue
            seen.add(ev.uid)
            out.append(ev)
    return out

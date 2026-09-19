"""Source adapter registry. Add a new adapter module here and reference its key in
config/calendars.yaml under `source.type`.

Each adapter exposes: fetch(feed) -> list[Event]
"""

from __future__ import annotations

from typing import Callable

from localcal.model import Event, Feed


def _elfsight(feed: Feed) -> list[Event]:
    from localcal.sources import elfsight

    settings = elfsight.fetch_settings(feed.source["widget_id"])
    return elfsight.parse_events(settings, feed)


def _vision_rss(feed: Feed) -> list[Event]:
    from localcal.sources import vision_rss

    xml = vision_rss.fetch(feed.source["rss_url"])
    return vision_rss.parse_events(xml, feed)


def _squarespace(feed: Feed) -> list[Event]:
    from localcal.sources import squarespace

    return squarespace.parse_events(squarespace.fetch(feed.source["url"]), feed)


def _manual(feed: Feed) -> list[Event]:
    from pathlib import Path

    from localcal.sources import manual

    root = Path(__file__).resolve().parent.parent.parent
    return manual.parse_events(manual.load(root / feed.source["file"]), feed)


REGISTRY: dict[str, Callable[[Feed], list[Event]]] = {
    "elfsight": _elfsight,
    "vision_rss": _vision_rss,
    "squarespace": _squarespace,
    "manual": _manual,
}


def fetch_events(feed: Feed) -> list[Event]:
    kind = feed.source.get("type")
    try:
        adapter = REGISTRY[kind]
    except KeyError:
        raise SystemExit(f"{feed.slug}: unknown source type {kind!r}; known: {sorted(REGISTRY)}")
    return adapter(feed)

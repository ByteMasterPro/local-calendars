"""Source adapter registry. Add a new adapter module here and reference its key in
config/breweries.yaml under `source.type`.

Each adapter exposes: fetch(brewery) -> list[Event]
"""

from __future__ import annotations

from typing import Callable

from brewcal.model import Brewery, Event


def _elfsight(brewery: Brewery) -> list[Event]:
    from brewcal.sources import elfsight

    settings = elfsight.fetch_settings(brewery.source["widget_id"])
    return elfsight.parse_events(settings, brewery)


REGISTRY: dict[str, Callable[[Brewery], list[Event]]] = {
    "elfsight": _elfsight,
}


def fetch_events(brewery: Brewery) -> list[Event]:
    kind = brewery.source.get("type")
    try:
        adapter = REGISTRY[kind]
    except KeyError:
        raise SystemExit(f"{brewery.slug}: unknown source type {kind!r}; known: {sorted(REGISTRY)}")
    return adapter(brewery)

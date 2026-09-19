"""Source-agnostic data model. Every source adapter produces a list of Event."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Event:
    uid: str                      # stable across runs so subscribers see updates, not duplicates
    summary: str
    start: datetime | date        # tz-aware datetime, or a date for all-day events
    end: datetime | date
    all_day: bool = False
    description: str = ""
    location: str = ""
    url: str = ""
    categories: list[str] = field(default_factory=list)
    rrule: dict[str, Any] | None = None          # icalendar-style RRULE dict, e.g. {"FREQ": "WEEKLY"}
    exdates: list[datetime | date] = field(default_factory=list)


@dataclass
class Brewery:
    slug: str                     # becomes the .ics filename
    name: str                     # X-WR-CALNAME shown in calendar apps
    url: str                      # public events page (goes into event URL/description)
    location: str                 # default LOCATION for every event
    timezone: str
    source: dict[str, Any] | None = None   # {"type": "elfsight", "widget_id": "..."} etc.
    feed_url: str = ""            # set instead of `source` when the brewery already publishes an .ics
    google_calendar_id: str = ""  # optional; makes the "Add to Google Calendar" link a native subscribe
    keep_past_days: int = 60
    default_duration_minutes: int = 120
    description: str = ""

    def __post_init__(self):
        if bool(self.source) == bool(self.feed_url):
            raise SystemExit(f"{self.slug}: set exactly one of `source` or `feed_url`")

    @property
    def external(self) -> bool:
        return bool(self.feed_url)


@dataclass
class Config:
    site: dict[str, Any]          # {"title": ..., "base_url": ...} used for the index page
    breweries: list[Brewery]


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text()) or {}
    defaults = raw.get("defaults") or {}
    breweries = [Brewery(**{**defaults, **entry}) for entry in raw.get("breweries") or []]
    slugs = [b.slug for b in breweries]
    if len(slugs) != len(set(slugs)):
        raise SystemExit(f"duplicate brewery slugs in {path}: {slugs}")
    return Config(site=raw.get("site") or {}, breweries=breweries)

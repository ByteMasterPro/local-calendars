"""Source-agnostic data model. Every source adapter produces a list of Event."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

KINDS = {"brewery": "Breweries", "town": "Towns & Community", "festival": "Fairs & Festivals", "other": "Other"}


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

    @property
    def start_date(self) -> date:
        return self.start.date() if isinstance(self.start, datetime) else self.start

    @property
    def end_date(self) -> date:
        return self.end.date() if isinstance(self.end, datetime) else self.end


@dataclass
class Feed:
    """One calendar we publish or link to. One entry under `calendars:` in the config."""

    slug: str                     # becomes the .ics filename
    name: str                     # X-WR-CALNAME shown in calendar apps
    url: str                      # public events page (goes into event URL/description)
    location: str                 # default LOCATION for every event
    timezone: str
    kind: str = "other"           # groups the index page; see KINDS
    short_name: str = ""          # "(Vanish, Leesburg)" in the digest; defaults to name
    town: str = ""                # ditto; defaults to the town parsed from `location`
    source: dict[str, Any] | None = None   # {"type": "elfsight", "widget_id": "..."} etc.
    feed_url: str = ""            # set instead of `source` when the venue already publishes an .ics
    google_calendar_id: str = ""  # optional; makes the "Add to Google Calendar" link a native subscribe
    accumulate: bool = False      # source only shows a short window: merge into the published feed
    wall_clock_tzids: list[str] = field(default_factory=list)   # TZIDs the venue uses by mistake;
                                  # their wall-clock times are re-read as `timezone` (Flying Ace)
    keep_past_days: int = 60
    default_duration_minutes: int = 120
    description: str = ""

    def __post_init__(self):
        if bool(self.source) == bool(self.feed_url):
            raise SystemExit(f"{self.slug}: set exactly one of `source` or `feed_url`")
        if self.kind not in KINDS:
            raise SystemExit(f"{self.slug}: kind must be one of {sorted(KINDS)}")

    @property
    def external(self) -> bool:
        return bool(self.feed_url)


@dataclass
class Config:
    site: dict[str, Any]          # {"title": ..., "base_url": ...} used for the index page
    feeds: list[Feed]
    digest: dict[str, Any] = field(default_factory=dict)   # Discord digest settings, see config

    def feed_url_for(self, feed: Feed) -> str:
        """The URL a subscriber should use for this feed."""
        return feed.feed_url or f"{self.site['base_url'].rstrip('/')}/{feed.slug}.ics"


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text()) or {}
    defaults = raw.get("defaults") or {}
    feeds = [Feed(**{**defaults, **entry}) for entry in raw.get("calendars") or []]
    slugs = [f.slug for f in feeds]
    if len(slugs) != len(set(slugs)):
        raise SystemExit(f"duplicate slugs in {path}: {slugs}")
    return Config(site=raw.get("site") or {}, feeds=feeds, digest=raw.get("digest") or {})

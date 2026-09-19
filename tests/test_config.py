from pathlib import Path

import pytest

from brewcal.cli import render_index
from brewcal.model import Brewery, Config, load_config

ROOT = Path(__file__).resolve().parent.parent


def test_repo_config_loads_and_has_both_kinds():
    cfg = load_config(ROOT / "config" / "breweries.yaml")
    by_slug = {b.slug: b for b in cfg.breweries}
    assert not by_slug["honor-brewing-loudoun"].external
    assert by_slug["chilly-hollow"].external
    assert by_slug["chilly-hollow"].feed_url.endswith("/public/basic.ics")


def test_brewery_requires_exactly_one_of_source_or_feed_url():
    common = dict(slug="x", name="X", url="https://x", location="", timezone="America/New_York")
    with pytest.raises(SystemExit):
        Brewery(**common)
    with pytest.raises(SystemExit):
        Brewery(**common, source={"type": "elfsight"}, feed_url="https://x/a.ics")


def test_index_links_external_feed_directly():
    cfg = Config(
        site={"title": "T", "base_url": "https://example.github.io/feeds"},
        breweries=[
            Brewery(slug="a", name="A", url="https://a", location="", timezone="America/New_York",
                    source={"type": "elfsight", "widget_id": "w"}),
            Brewery(slug="b", name="B", url="https://b", location="", timezone="America/New_York",
                    feed_url="https://calendar.google.com/calendar/ical/abc/public/basic.ics",
                    google_calendar_id="abc@group.calendar.google.com"),
        ],
    )
    page = render_index(cfg)
    assert "https://example.github.io/feeds/a.ics" in page
    assert "webcal://calendar.google.com/calendar/ical/abc/public/basic.ics" in page
    assert "cid=abc%40group.calendar.google.com" in page
    assert "feeds/b.ics" not in page

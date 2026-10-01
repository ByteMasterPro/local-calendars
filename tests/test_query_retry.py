from localcal import query
from localcal.model import Feed


def test_load_calendar_retries_then_succeeds(monkeypatch, tmp_path):
    feed = Feed(slug="x", name="X", url="https://x", location="", timezone="America/New_York", kind="other",
                feed_url="https://x/feed.ics")
    calls = {"n": 0}

    class Resp:
        content = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nEND:VCALENDAR\r\n"
        def raise_for_status(self): pass

    def fake_get(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("tls hiccup")
        return Resp()

    waits = []
    monkeypatch.setattr(query.requests, "get", fake_get)
    monkeypatch.setattr(query._time, "sleep", waits.append)
    cal = query.load_calendar(feed, cache_dir=tmp_path)
    assert calls["n"] == 3 and cal["PRODID"] == "t"
    assert waits == list(query.RETRY_BACKOFF[:2])          # widening backoff, not a tight loop


def test_load_calendar_gives_up_after_attempts(monkeypatch, tmp_path):
    feed = Feed(slug="x", name="X", url="https://x", location="", timezone="America/New_York", kind="other",
                feed_url="https://x/feed.ics")
    monkeypatch.setattr(query.requests, "get", lambda url, **kw: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr(query._time, "sleep", lambda s: None)
    import pytest
    with pytest.raises(ConnectionError):
        query.load_calendar(feed, backoff=(1,), cache_dir=tmp_path)


def test_backoff_spans_at_least_a_minute():
    assert sum(query.RETRY_BACKOFF) >= 60          # long enough to outlast a host's bad-chain window


ICS = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nEND:VCALENDAR\r\n"
LANDER = b'<!DOCTYPE html><html><head><script>window.onload=function(){window.location.href="/lander"}</script></head></html>'


def ext_feed():
    return Feed(slug="vanish", name="Vanish", url="https://v", location="", timezone="America/New_York",
                kind="brewery", feed_url="https://v/feed.ics")


class Ok:
    def __init__(self, content): self.content = content
    def raise_for_status(self): pass


def test_fetch_external_rejects_a_parked_domain_lander_page(monkeypatch):
    monkeypatch.setattr(query.requests, "get", lambda url, **kw: Ok(LANDER))
    import pytest
    with pytest.raises(ValueError):
        query.fetch_external(ext_feed())


def test_live_fetch_refreshes_the_cache(monkeypatch, tmp_path):
    feed = ext_feed()
    monkeypatch.setattr(query.requests, "get", lambda url, **kw: Ok(ICS))
    cal = query.load_calendar(feed, cache_dir=tmp_path)
    assert cal["PRODID"] == "t"
    assert (tmp_path / "vanish.ics").read_bytes() == ICS


def test_falls_back_to_cached_copy_when_the_host_misbehaves(monkeypatch, tmp_path):
    feed = ext_feed()
    (tmp_path / "vanish.ics").write_bytes(ICS)
    monkeypatch.setattr(query.requests, "get", lambda url, **kw: Ok(LANDER))   # server answers garbage
    monkeypatch.setattr(query._time, "sleep", lambda s: None)
    cal = query.load_calendar(feed, backoff=(1, 1), cache_dir=tmp_path)
    assert cal["PRODID"] == "t"                                                # venue still in the digest


def test_no_cache_means_the_feed_still_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(query.requests, "get", lambda url, **kw: Ok(LANDER))
    monkeypatch.setattr(query._time, "sleep", lambda s: None)
    import pytest
    with pytest.raises(Exception):
        query.load_calendar(ext_feed(), backoff=(1,), cache_dir=tmp_path)

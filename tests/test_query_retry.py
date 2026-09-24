from localcal import query
from localcal.model import Feed


def test_load_calendar_retries_then_succeeds(monkeypatch):
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
    cal = query.load_calendar(feed)
    assert calls["n"] == 3 and cal["PRODID"] == "t"
    assert waits == list(query.RETRY_BACKOFF[:2])          # widening backoff, not a tight loop


def test_load_calendar_gives_up_after_attempts(monkeypatch):
    feed = Feed(slug="x", name="X", url="https://x", location="", timezone="America/New_York", kind="other",
                feed_url="https://x/feed.ics")
    monkeypatch.setattr(query.requests, "get", lambda url, **kw: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr(query._time, "sleep", lambda s: None)
    import pytest
    with pytest.raises(ConnectionError):
        query.load_calendar(feed, backoff=(1,))


def test_backoff_spans_at_least_a_minute():
    assert sum(query.RETRY_BACKOFF) >= 60          # long enough to outlast a host's bad-chain window

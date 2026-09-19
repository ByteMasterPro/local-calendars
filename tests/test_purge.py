from datetime import datetime, timezone

from localcal import digest


def snowflake(days_ago: float) -> str:
    ts_ms = int((datetime.now(timezone.utc).timestamp() - days_ago * 86400) * 1000)
    return str((ts_ms - 1420070400000) << 22)


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.text = status, body, ""
    def json(self):
        return self._body


def test_purge_bulk_deletes_young_and_singly_deletes_old_skipping_pinned(monkeypatch):
    msgs = [{"id": snowflake(1)}, {"id": snowflake(2)}, {"id": snowflake(20)}, {"id": snowflake(3), "pinned": True}]
    calls = {"get": 0, "bulk": [], "single": []}

    def fake_get(url, **kw):
        calls["get"] += 1
        return Resp(200, msgs if calls["get"] == 1 else [])
    def fake_post(url, **kw):
        assert url.endswith("/messages/bulk-delete")
        calls["bulk"].append(kw["json"]["messages"]); return Resp(204)
    def fake_delete(url, **kw):
        calls["single"].append(url.rsplit("/", 1)[-1]); return Resp(204)

    monkeypatch.setattr(digest.requests, "get", fake_get)
    monkeypatch.setattr(digest.requests, "post", fake_post)
    monkeypatch.setattr(digest.requests, "delete", fake_delete)
    monkeypatch.setattr(digest._time, "sleep", lambda s: None)
    n = digest.purge_channel("tok", "123")
    assert n == 3
    assert calls["bulk"] == [[msgs[0]["id"], msgs[1]["id"]]]           # two young -> one bulk call
    assert calls["single"] == [msgs[2]["id"]]                           # the 20-day-old one, individually
    assert calls["get"] == 1                                            # fewer than 100 messages -> one page


def test_cli_purges_before_posting_when_bot_configured(monkeypatch):
    from datetime import date
    from localcal import cli
    from localcal.model import Config
    cfg = Config(site={}, feeds=[], digest={"title": "T"})
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: ([], 0))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    order = []
    monkeypatch.setattr(digest, "purge_channel", lambda t, c: order.append("purge") or 4)
    monkeypatch.setattr(digest, "_post", lambda url, payload: order.append("post"))
    assert cli.digest(cfg, [], date(2026, 9, 21), post=True) == 0
    assert order[0] == "purge" and order.count("post") >= 1


def test_cli_posts_anyway_if_purge_fails(monkeypatch, capsys):
    from datetime import date
    from localcal import cli
    from localcal.model import Config
    cfg = Config(site={}, feeds=[], digest={"title": "T"})
    monkeypatch.setattr(digest.query, "gather", lambda feeds, s, e: ([], 0))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    def boom(t, c): raise RuntimeError("Discord API 403")
    monkeypatch.setattr(digest, "purge_channel", boom)
    sent = []
    monkeypatch.setattr(digest, "_post", lambda url, payload: sent.append(payload))
    assert cli.digest(cfg, [], date(2026, 9, 21), post=True) == 0
    assert sent and "::warning::channel purge failed" in capsys.readouterr().out

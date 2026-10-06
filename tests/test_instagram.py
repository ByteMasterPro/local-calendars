import json
from pathlib import Path

from localcal import instagram as ig


class Resp:
    def __init__(self, content=b"\xff\xd8binary"): self.content = content
    def raise_for_status(self): pass


class Sess:
    def __init__(self): self.calls = []
    def get(self, url, **kw):
        self.calls.append(url)
        return Resp()


GRID = [
    {"alt": "Photo by Whites Ferry on October 03, 2026. May be an image of text.",
     "url": "https://scontent.cdninstagram.com/v/t51/aaa_n.jpg?stp=dst-jpg&oh=sig"},
    {"alt": "Photo by Whites Ferry on October 01, 2026. May be an image of text that says '20% OFF'.",
     "url": "https://scontent.cdninstagram.com/v/t51/bbb_n.jpg?stp=dst-jpg&oh=sig"},
]


def test_pull_downloads_posts_and_writes_a_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(ig, "fetch_grid", lambda handle, **kw: GRID)
    sess = Sess()
    posts = ig.pull("whitesferrybrew", tmp_path, session=sess)
    assert [p.posted for p in posts] == ["2026-10-03", "2026-10-01"]        # newest first
    files = sorted(p.name for p in (tmp_path / "whitesferrybrew").glob("*.jpg"))
    assert files == ["2026-10-01_bbb_n.jpg", "2026-10-03_aaa_n.jpg"]
    manifest = json.loads((tmp_path / "whitesferrybrew" / "manifest.json").read_text())
    assert len(manifest) == 2 and len(sess.calls) == 2


def test_pull_skips_what_is_already_downloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(ig, "fetch_grid", lambda handle, **kw: GRID)
    ig.pull("whitesferrybrew", tmp_path, session=Sess())
    second = Sess()
    posts = ig.pull("whitesferrybrew", tmp_path, session=second)
    assert second.calls == [] and len(posts) == 2                            # nothing re-fetched


def test_a_signed_url_change_does_not_redownload(monkeypatch, tmp_path):
    monkeypatch.setattr(ig, "fetch_grid", lambda handle, **kw: GRID)
    ig.pull("whitesferrybrew", tmp_path, session=Sess())
    resigned = [{"alt": g["alt"], "url": g["url"].replace("oh=sig", "oh=newsig")} for g in GRID]
    monkeypatch.setattr(ig, "fetch_grid", lambda handle, **kw: resigned)
    sess = Sess()
    ig.pull("whitesferrybrew", tmp_path, session=sess)
    assert sess.calls == []            # the url before the query string is the identity


def test_offers_are_starred_but_every_post_is_queued(monkeypatch, tmp_path):
    monkeypatch.setattr(ig, "fetch_grid", lambda handle, **kw: GRID)
    posts = ig.pull("whitesferrybrew", tmp_path, session=Sess())
    assert [p.flagged for p in posts] == [False, True]                       # "20% OFF" earns a star
    body = ig.write_review(tmp_path, posts).read_text()
    assert body.count("- ") >= 2 and "⭐" in body
    assert "2026-10-03" in body and "2026-10-01" in body                     # the unstarred one too


def test_undated_posts_still_download(monkeypatch, tmp_path):
    monkeypatch.setattr(ig, "fetch_grid", lambda handle, **kw: [{"alt": "no date here", "url": "https://x.cdninstagram.com/v/zzz_n.jpg"}])
    [post] = ig.pull("h", tmp_path, session=Sess())
    assert post.posted == "" and post.file.startswith("undated_")

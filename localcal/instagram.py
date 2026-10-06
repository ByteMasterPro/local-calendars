"""Pull a venue's public Instagram grid so its posters can be read.

Some venues announce only on Instagram (White's Ferry, Route 7). The logged-out profile page
renders the recent grid, and the thumbnails are real CDN urls, so the posters can be collected
without an account. Two hard limits, both deliberate:

- **Never log in.** Captions, which is where venues put dates and promo codes, are only visible
  to an account. Scraping them with Christopher's login would risk his account and break
  Instagram's terms, so a post whose detail is caption-only gets flagged for him to open the
  app himself rather than guessed at.
- **Never in CI.** The grid is rendered by JavaScript, so this needs a real browser, and
  Instagram blocks datacenter IPs. This runs from his Mac under launchd, not GitHub Actions.

Output per handle, under `data/instagram/<handle>/`: the images, plus `manifest.json` recording
each post's date, alt text (Instagram's own OCR of the poster, when it has one) and local path.
`review.md` lists every post newest-first as a queue to look at with real eyes. Instagram's alt
text is only sometimes an OCR of the poster, so a keyword hit earns a star but its absence proves
nothing: White's Ferry's "Leesburg Fall Festival" poster carries alt text of just "image of text".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

PROFILE_URL = "https://www.instagram.com/{handle}/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# Reads the grid: every CDN image that is not the avatar or a story highlight, at the largest
# size the page offers.
EXTRACT_JS = """
() => {
  const imgs = [...document.querySelectorAll('img')].filter(i =>
    ((i.src || '') + (i.srcset || '')).includes('cdninstagram') &&
    !/profile picture|highlight story/i.test(i.alt || ''));
  const largest = i => {
    const set = (i.srcset || '').split(',').map(x => x.trim().split(' ')).filter(a => a[0]);
    const best = set.sort((a, b) => parseInt(b[1] || 0) - parseInt(a[1] || 0))[0];
    return best ? best[0] : i.src;
  };
  return imgs.map(i => ({alt: i.alt || '', url: largest(i)}));
}
"""

# Earns a star in the queue. Alt text is Instagram's own OCR and is often absent, so this
# prioritises rather than filters: every post is listed either way.
WORTH_READING = re.compile(
    r"\d+\s*%\s*off|promo|\bcode\b|bogo|free\b|special|details in caption|link in bio|"
    r"festival|fest\b|live music|trivia|karaoke|movie|market|vendors|party|release|tasting|"
    r"hallowe|pumpkin|oktober|christmas|holiday|santa|new year",
    re.I,
)
_DATE = re.compile(r"on (?P<m>[A-Z][a-z]+) (?P<d>\d{1,2}), (?P<y>\d{4})")


@dataclass
class Post:
    handle: str
    posted: str          # YYYY-MM-DD, from the alt text; "" when Instagram omits it
    alt: str
    file: str
    url: str
    flagged: bool


def fetch_grid(handle: str, timeout_ms: int = 30000) -> list[dict]:
    """The profile's visible grid, as [{alt, url}]. Needs a real browser."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA, viewport={"width": 1280, "height": 2200})
            page.goto(PROFILE_URL.format(handle=handle), timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)                 # the grid fills in after first paint
            return page.evaluate(EXTRACT_JS)
        finally:
            browser.close()


def pull(handle: str, out_root: Path, session: requests.Session | None = None) -> list[Post]:
    """Download anything new from a handle's grid; returns every post now on file."""
    out = out_root / handle
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.json"
    known = json.loads(manifest.read_text()) if manifest.exists() else {}

    sess = session or requests.Session()
    posts: dict[str, Post] = {k: Post(**v) for k, v in known.items()}
    for item in fetch_grid(handle):
        key = item["url"].split("?")[0]
        if key in posts and (out / posts[key].file).exists():
            continue
        posted = _posted_on(item["alt"])
        name = f"{posted or 'undated'}_{key.rsplit('/', 1)[-1][:40]}"
        if not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            name += ".jpg"
        try:
            resp = sess.get(item["url"], headers={"User-Agent": UA}, timeout=60)
            resp.raise_for_status()
            (out / name).write_bytes(resp.content)
        except Exception as exc:
            log.warning("%s: could not download %s (%s)", handle, name, str(exc)[:100])
            continue
        posts[key] = Post(handle=handle, posted=posted, alt=item["alt"], file=name,
                          url=item["url"], flagged=bool(WORTH_READING.search(item["alt"])))
        log.info("%s: saved %s%s", handle, name, "  [worth reading]" if posts[key].flagged else "")

    ordered = sorted(posts.values(), key=lambda p: p.posted, reverse=True)
    manifest.write_text(json.dumps({p.url.split("?")[0]: asdict(p) for p in ordered}, indent=1))
    return ordered


def write_review(out_root: Path, all_posts: list[Post], limit: int = 40) -> Path:
    """The queue of posts to look at, newest first. A star means the alt text already hints at
    something; no star means Instagram gave no OCR, which is not evidence of nothing."""
    lines = ["# Instagram review queue",
             "",
             f"Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}. Open the images, then put what they",
             "add into `config/events/overrides.yaml` (detail for an event a feed already has), a curated",
             "file such as `config/events/route-7.yaml` (an event no feed carries), or a `notices:` entry",
             "(an offer or event whose date or promo code is in the caption, which needs an account).",
             ""]
    for p in sorted(all_posts, key=lambda p: (p.posted, p.handle), reverse=True)[:limit]:
        star = "⭐ " if p.flagged else ""
        lines.append(f"- {star}**{p.posted or 'undated'}** @{p.handle} — `data/instagram/{p.handle}/{p.file}`")
        alt = " ".join((p.alt or "").split())
        if alt:
            lines.append(f"  - {alt[:300]}")
    path = out_root / "review.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def _posted_on(alt: str) -> str:
    m = _DATE.search(alt or "")
    if not m:
        return ""
    try:
        return datetime.strptime(f"{m['m']} {m['d']} {m['y']}", "%B %d %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""

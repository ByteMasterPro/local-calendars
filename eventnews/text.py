"""Small text helpers shared by adapters."""

from __future__ import annotations

import html
import re

_BLOCK_END = re.compile(r"</(div|p|li|h[1-6]|tr)>|<br\s*/?>", re.I)
_TAGS = re.compile(r"<[^>]+>")


def html_to_text(fragment: str) -> str:
    text = _BLOCK_END.sub("\n", fragment or "")
    text = _TAGS.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

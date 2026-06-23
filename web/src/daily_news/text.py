from __future__ import annotations

import re
from html import unescape


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean_html_text(value: str | None) -> str:
    if not value:
        return ""
    text = TAG_RE.sub(" ", value)
    text = unescape(text)
    return SPACE_RE.sub(" ", text).strip()


def clamp_text(value: str, max_chars: int) -> str:
    value = clean_html_text(value)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"

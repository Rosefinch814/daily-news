from __future__ import annotations

import httpx
import trafilatura
from readability import Document

from daily_news.text import clean_html_text


async def extract_article_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_chars: int = 6000,
) -> tuple[str, str | None]:
    """Return extracted article text and an optional error message."""
    try:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - preserve fetch error for run logs.
        return "", str(exc)

    html = response.text
    text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    if not text:
        try:
            doc = Document(html)
            text = clean_html_text(doc.summary())
        except Exception as exc:  # noqa: BLE001
            return "", str(exc)

    text = clean_html_text(text)
    return text[:max_chars], None

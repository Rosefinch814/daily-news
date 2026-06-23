from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from dateutil import parser as date_parser

from daily_news.fetch.extract import extract_article_text
from daily_news.models import RawItem, SectionConfig, SourceConfig
from daily_news.text import clean_html_text


def stable_item_id(source_id: str, url: str, title: str) -> str:
    digest = hashlib.sha1(f"{canonical_url(url)}|{title}".encode("utf-8")).hexdigest()
    return digest[:16]


def canonical_url(url: str) -> str:
    split = urlsplit(url)
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(split.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(
        (
            split.scheme.lower(),
            split.netloc.lower(),
            split.path.rstrip("/"),
            urlencode(filtered_query, doseq=True),
            "",
        )
    )


def parse_entry_datetime(entry: object) -> datetime | None:
    for key in ("published", "updated", "created"):
        value = getattr(entry, key, None) if not isinstance(entry, dict) else entry.get(key)
        if value:
            try:
                parsed = date_parser.parse(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
            except (TypeError, ValueError, OverflowError):
                continue
    return None


async def fetch_source_rss(
    client: httpx.AsyncClient,
    source: SourceConfig,
    *,
    limit: int = 25,
) -> list[RawItem]:
    response = await client.get(str(source.url), follow_redirects=True)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    fetched_at = datetime.now(timezone.utc)
    items: list[RawItem] = []

    cutoff = None
    if source.lookback_hours:
        cutoff = fetched_at - timedelta(hours=source.lookback_hours)

    for entry in parsed.entries[:limit]:
        title = clean_html_text(entry.get("title", ""))
        url = entry.get("link", "")
        if not title or not url:
            continue
        published_at = parse_entry_datetime(entry)
        if cutoff and published_at and published_at < cutoff:
            continue
        summary = clean_html_text(entry.get("summary", "") or entry.get("description", ""))
        items.append(
            RawItem(
                id=stable_item_id(source.id, url, title),
                source_id=source.id,
                source_name=source.name,
                source_language=source.language,
                title=title,
                url=canonical_url(url),
                published_at=published_at,
                summary=summary,
                fetched_at=fetched_at,
                fetch_status="rss",
            )
        )

    return items


async def fetch_section_items(
    section: SectionConfig,
    *,
    per_source_limit: int = 25,
    timeout_seconds: float = 20,
) -> list[RawItem]:
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "User-Agent": "daily-news/0.1 (+https://github.com/)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    active_sources = [source for source in section.enabled_sources if source.type == "rss"]
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        tasks = [
            fetch_source_rss(client, source, limit=source.max_items or per_source_limit)
            for source in active_sources
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    items: list[RawItem] = []
    for source, result in zip(active_sources, results, strict=False):
        if isinstance(result, Exception):
            fetched_at = datetime.now(timezone.utc)
            items.append(
                RawItem(
                    id=stable_item_id(source.id, str(source.url), "fetch-error"),
                    source_id=source.id,
                    source_name=source.name,
                    source_language=source.language,
                    title=f"{source.name} fetch failed",
                    url=str(source.url),
                    fetched_at=fetched_at,
                    fetch_status="failed",
                    error=str(result),
                )
            )
        else:
            items.extend(result)
    return dedupe_items(items)


def dedupe_items(items: list[RawItem]) -> list[RawItem]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    deduped: list[RawItem] = []
    for item in items:
        if item.fetch_status == "failed":
            deduped.append(item)
            continue
        title_key = item.title.lower().strip()
        url_key = canonical_url(item.url)
        if url_key in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        deduped.append(item)
    return deduped


async def enrich_candidate_content(
    candidates: list[RawItem],
    *,
    limit: int = 60,
    timeout_seconds: float = 20,
) -> list[RawItem]:
    headers = {
        "User-Agent": "daily-news/0.1 (+https://github.com/)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        tasks = [extract_article_text(client, item.url) for item in candidates[:limit]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched: list[RawItem] = []
    for item, result in zip(candidates[:limit], results, strict=False):
        if isinstance(result, Exception):
            enriched.append(item.model_copy(update={"fetch_status": "failed", "error": str(result)}))
            continue
        content, error = result
        if content:
            enriched.append(item.model_copy(update={"content": content, "fetch_status": "content"}))
        elif error:
            enriched.append(item.model_copy(update={"fetch_status": "failed", "error": error}))
        else:
            enriched.append(item)

    return enriched + candidates[limit:]

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from daily_news.fetch.rss import canonical_url
from daily_news.models import CandidateItem, RawItem, SectionConfig


TERM_ALIASES = {
    "英伟达": ["nvidia", "nvda"],
    "苹果": ["apple", "iphone", "ios", "mac", "apple watch"],
    "台积电": ["tsmc", "taiwan semiconductor"],
    "特斯拉": ["tesla", "fsd", "robotaxi"],
    "微软": ["microsoft", "windows", "copilot"],
    "谷歌": ["google", "gemini", "deepmind"],
    "英特尔": ["intel"],
    "三星电子": ["samsung", "samsung electronics"],
    "SK海力士": ["sk hynix", "sk hynix inc", "hynix"],
    "美光": ["micron", "micron technology"],
    "AI芯片": ["ai chip", "ai chips", "gpu", "gpus", "accelerator", "accelerators"],
    "大模型进展": ["llm", "llms", "large language model", "foundation model", "openai", "anthropic", "glm"],
    "AI产品发布": ["ai product", "ai feature", "agentic ai", "agent", "agents"],
    "自动驾驶": ["autonomous driving", "self-driving", "autopilot", "robotaxi", "fsd"],
    "半导体": ["semiconductor", "chip", "chips", "memory", "hbm", "ram"],
    "黄仁勋": ["jensen huang"],
    "马斯克": ["elon musk", "musk"],
    "奥特曼": ["sam altman", "altman"],
    "库克": ["tim cook"],
}

TITLE_DEDUPE_NOISE_TERMS = [
    "reportedly",
    "report",
    "says",
    "said",
    "launches",
    "launch",
    "announces",
    "announce",
    "announced",
    "unveils",
    "unveil",
    "独家",
    "首发",
    "据悉",
    "消息称",
    "传",
]

TITLE_DEDUPE_MEDIA_PREFIXES = [
    "exclusive",
    "breaking",
    "update",
    "独家",
    "首发",
]

AGGREGATE_NOISE_TERMS = [
    "早报",
    "晚报",
    "8点1氪",
    "氪星晚报",
    "今日热点导览",
    "热点导览",
    "TOP 3大新闻",
    "TOP 3",
    "快讯",
    "收跌",
    "收涨",
    "涨超",
    "跌超",
    "股价",
    "市值",
    "融资",
    "大会",
    "活动",
]

HEAVY_NOISE_TERMS = [
    "早报",
    "晚报",
    "8点1氪",
    "氪星晚报",
    "今日热点导览",
    "热点导览",
]

CONSUMER_NOISE_TERMS = [
    "prime day",
    "apple watch",
    "steam machine",
    "game console",
    "e-bike",
    "ebike",
    "gaming",
]

HIGH_VALUE_TERMS = [
    "nvidia",
    "openai",
    "ai chip",
    "ai chips",
    "semiconductor",
    "data center",
    "datacenter",
    "gpu",
    "hbm",
    "autopilot",
    "autonomous driving",
    "tesla",
    "大模型",
    "豆包",
    "英伟达",
    "半导体",
    "AI芯片",
    "自动驾驶",
]


def _match_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    matches: list[str] = []
    for term in terms:
        aliases = [term, *TERM_ALIASES.get(term, [])]
        if any(alias and alias.lower() in lowered for alias in aliases):
            matches.append(term)
    return matches


def _match_plain_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term and term.lower() in lowered]


def dedupe_url_key(url: str) -> str:
    normalized = canonical_url(url)
    split = urlsplit(normalized)
    return urlunsplit(("https", split.netloc, split.path, split.query, ""))


def normalize_title_for_dedupe(title: str) -> str:
    normalized = title.lower()
    for prefix in TITLE_DEDUPE_MEDIA_PREFIXES:
        normalized = re.sub(rf"^\s*{re.escape(prefix.lower())}\s*[:：｜|\-—]\s*", "", normalized)
    for term in TITLE_DEDUPE_NOISE_TERMS:
        normalized = re.sub(rf"\b{re.escape(term.lower())}\b", " ", normalized)
        normalized = normalized.replace(term, " ")
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized, flags=re.UNICODE)
    return normalized.strip()


def title_dedupe_hash(title: str) -> str | None:
    normalized = normalize_title_for_dedupe(title)
    if not normalized:
        return None
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def score_item(item: RawItem, section: SectionConfig) -> CandidateItem:
    text = item.text_for_scoring
    want_terms = section.interests.want.all_terms
    avoid_terms = section.interests.avoid
    matched = _match_terms(text, want_terms)
    avoided = _match_terms(text, avoid_terms)
    aggregate_noise = _match_plain_terms(text, AGGREGATE_NOISE_TERMS)
    heavy_noise = _match_plain_terms(text, HEAVY_NOISE_TERMS)
    consumer_noise = _match_plain_terms(text, CONSUMER_NOISE_TERMS)
    high_value = _match_plain_terms(text, HIGH_VALUE_TERMS)

    source_weight = next(
        (source.weight for source in section.sources if source.id == item.source_id),
        1.0,
    )
    score = 5.0 * source_weight
    score += len(matched) * 18.0
    score -= len(avoided) * 30.0
    score -= len(aggregate_noise) * 18.0
    score -= len(heavy_noise) * 30.0
    score -= len(consumer_noise) * 18.0
    score += min(len(high_value), 4) * 8.0
    if item.summary:
        score += 5.0
    if item.content:
        score += 8.0
    if item.source_language == "en" and matched:
        score += 8.0
    if item.fetch_status == "failed":
        score -= 20.0
    if heavy_noise:
        score = min(score, 35.0)
    elif aggregate_noise:
        score = min(score, 50.0)

    reason_parts: list[str] = []
    if matched:
        reason_parts.append("命中关注：" + "、".join(matched))
    if avoided:
        reason_parts.append("命中不想看：" + "、".join(avoided))
    if aggregate_noise:
        reason_parts.append("聚合/快讯降权：" + "、".join(aggregate_noise))
    if consumer_noise:
        reason_parts.append("消费/娱乐弱相关降权：" + "、".join(consumer_noise))
    if high_value:
        reason_parts.append("高价值主题加权：" + "、".join(high_value[:6]))
    if not reason_parts:
        reason_parts.append("未命中明确偏好，按源权重保留排序")

    return CandidateItem(
        raw_item=item,
        score=round(score, 2),
        matched_terms=matched,
        avoided_terms=avoided,
        reason="；".join(reason_parts),
        entered_ai=score > 0,
    )


def rank_candidates(
    items: list[RawItem],
    section: SectionConfig,
    *,
    max_candidates: int = 60,
    min_score: float = 0,
    per_source_limit: int = 4,
    require_interest_match_when_over_capacity: bool = True,
    historical_urls: set[str] | None = None,
    historical_title_hashes: set[str] | None = None,
) -> list[CandidateItem]:
    candidates = [score_item(item, section) for item in items if item.fetch_status != "failed"]
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    selected: list[CandidateItem] = []
    seen_urls: set[str] = set(historical_urls or set())
    seen_title_hashes: set[str] = set(historical_title_hashes or set())
    source_counts: dict[str, int] = {}
    for candidate in candidates:
        url_key = dedupe_url_key(candidate.raw_item.url)
        title_hash = title_dedupe_hash(candidate.raw_item.title)
        if url_key in seen_urls:
            continue
        if title_hash and title_hash in seen_title_hashes:
            continue
        if (
            require_interest_match_when_over_capacity
            and not candidate.matched_terms
            and len(candidates) >= max_candidates
        ):
            continue
        source_name = candidate.raw_item.source_name
        if source_counts.get(source_name, 0) >= per_source_limit:
            continue
        seen_urls.add(url_key)
        if title_hash:
            seen_title_hashes.add(title_hash)
        if candidate.score >= min_score:
            selected.append(candidate)
            source_counts[source_name] = source_counts.get(source_name, 0) + 1
        if len(selected) >= max_candidates:
            break
    return selected

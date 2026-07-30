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
    "长鑫科技": ["长鑫存储", "cxmt", "changxin memory"],
    "铠侠": ["kioxia"],
    "AI芯片": ["ai chip", "ai chips", "gpu", "gpus", "accelerator", "accelerators"],
    "大模型进展": ["llm", "llms", "large language model", "foundation model", "openai", "anthropic", "glm"],
    "AI产品发布": ["ai product", "ai feature", "agentic ai", "agent", "agents"],
    "自动驾驶": ["autonomous driving", "self-driving", "autopilot", "robotaxi", "fsd"],
    "半导体": ["semiconductor", "chip", "chips", "memory", "hbm", "ram"],
    "HBM": ["high bandwidth memory", "高带宽内存"],
    "DRAM": ["dynamic random access memory", "动态随机存取存储器"],
    "NAND": ["nand flash", "闪存"],
    "存储芯片": ["memory chip", "memory chips"],
    "企业级SSD": ["enterprise ssd", "enterprise ssds", "企业级固态硬盘"],
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

STORAGE_COMPANY_ALIASES = {
    "SK海力士": ["sk海力士", "sk 海力士", "sk hynix", "hynix"],
    "美光": ["美光", "micron", "micron technology"],
    "三星电子": ["三星电子", "三星半导体", "samsung electronics", "samsung semiconductor"],
    "长鑫科技": ["长鑫科技", "长鑫存储", "cxmt", "changxin memory"],
    "铠侠": ["铠侠", "kioxia"],
}

STORAGE_EVENT_TERMS = [
    "财报",
    "业绩",
    "营收",
    "销售额",
    "营业利润",
    "净利润",
    "亏损",
    "earnings",
    "financial results",
    "revenue",
    "operating profit",
    "net profit",
    "季度业绩",
    "存储价格",
    "存储芯片价格",
    "内存价格",
    "闪存价格",
    "memory price",
    "memory pricing",
    "dram price",
    "nand price",
    "订单",
    "长约",
    "出货",
    "供货",
    "产能",
    "扩产",
    "资本开支",
    "capex",
    "量产",
    "投产",
    "hbm",
    "dram",
    "nand",
    "企业级ssd",
    "enterprise ssd",
]

STORAGE_EVENT_WINDOW_CHARS = 220
STORAGE_EVENT_SCORE_FLOOR = 42.0


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


def _nearby_storage_event_signals(text: str) -> list[str]:
    lowered = text.lower()
    signals: list[str] = []
    segments = re.split(r"[。！？!?；;\n\r]+", lowered)
    for segment in segments:
        for company, aliases in STORAGE_COMPANY_ALIASES.items():
            company_positions = [
                index
                for alias in aliases
                for index in (segment.find(alias.lower()),)
                if index >= 0
            ]
            if not company_positions:
                continue
            for event_term in STORAGE_EVENT_TERMS:
                event_start = segment.find(event_term.lower())
                while event_start >= 0:
                    if any(
                        abs(event_start - company_start) <= STORAGE_EVENT_WINDOW_CHARS
                        for company_start in company_positions
                    ):
                        signal = f"{company}×{event_term}"
                        if signal not in signals:
                            signals.append(signal)
                        break
                    event_start = segment.find(event_term.lower(), event_start + 1)
                if any(signal.startswith(f"{company}×") for signal in signals):
                    break
    return signals


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
    priority_signals = _nearby_storage_event_signals(text)

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
    if priority_signals:
        score = max(score, STORAGE_EVENT_SCORE_FLOOR)

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
    if priority_signals:
        reason_parts.append("重点存储事件保护：" + "、".join(priority_signals))
    if not reason_parts:
        reason_parts.append("未命中明确偏好，按源权重保留排序")

    return CandidateItem(
        raw_item=item,
        score=round(score, 2),
        matched_terms=matched,
        avoided_terms=avoided,
        priority_signals=priority_signals,
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
    candidates.sort(
        key=lambda candidate: (bool(candidate.priority_signals), candidate.score),
        reverse=True,
    )
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

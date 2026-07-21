from __future__ import annotations

import html
import json
import logging
import math
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from daily_news.ai_engine import (
    AIEngineError,
    ProviderName,
    XHSCondenseOutput,
    XHSCondenseSlotOutput,
    XHSMagnetizeOutput,
    XHSNoteTitleOutput,
    build_xhs_condense_file_prompt,
    build_xhs_magnetize_prompt,
    build_xhs_note_title_prompt,
    run_ai_task,
)
from daily_news.config import PipelineConfig
from daily_news.models import BriefArticle, HeadlineArticle, Issue
from daily_news.paths import DIST_DIR, RUNS_DIR
from daily_news.storage.local import save_ai_task_run


CARD_WIDTH = 1080
CARD_HEIGHT = 1440
XHS_PUBLICATION_NAME = "AI科技日报"
MAX_HEADLINE_CARDS = 3
BRIEF_PAGE_MAX_ITEMS = 5
BRIEF_LIST_HEIGHT_LIMIT = 1080
BRIEF_ITEM_SOFT_LIMIT = 260
HASHTAGS = f"#{XHS_PUBLICATION_NAME} #科技日报 #AI日报 #人工智能 #科技资讯"
NOTE_HASHTAGS = "#AI日报 #人工智能 #AIGC #科技资讯"
NOTE_SLOGAN = "看完图组，快速补齐今天最值得关注的 AI 与科技动态。"
GENERIC_NOTE_TITLE_TERMS = (
    "AI科技日报",
    "AI日报",
    "日报",
    "看点",
    "速览",
    "简报",
    "今日看点",
    "今日重点",
    "今日速览",
    "今日简报",
    "新闻简报",
    "科技资讯",
    "科技新闻",
    "一文看懂",
    "小红书",
    "左滑",
)
WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
LOGGER = logging.getLogger(__name__)

CoverTemplate = Literal["classic", "single-hook", "v2"]
SlotType = Literal["cover_hook", "cover_sub", "headline_summary", "headline_impact", "brief_summary"]
SLOT_RANGES: dict[SlotType, tuple[int, int]] = {
    "cover_hook": (12, 24),
    "cover_sub": (28, 46),
    "headline_summary": (90, 155),
    "headline_impact": (85, 145),
    "brief_summary": (22, 52),
}
COVER_HOOK_LARGE_MAX_CHARS = 16
COVER_SAFE_TOP = 180
COVER_SAFE_BOTTOM = 1260
V2_TITLE_FONT_SIZES = (100, 90, 82)
MAGNETIZE_RESTRAINED_BANNED_TERMS = (
    "震惊",
    "太火了",
    "暴涨",
    "崩盘",
    "吊打",
    "秒杀",
    "逆天",
)
MAGNETIZE_FUTURE_SOURCE_TERMS = ("计划", "目标", "预计", "预期", "拟", "将", "有望", "可能", "或将", "力争")
MAGNETIZE_FUTURE_OUTPUT_TERMS = MAGNETIZE_FUTURE_SOURCE_TERMS + ("要", "冲刺")
MAGNETIZE_ABSOLUTE_TERMS = ("已实现", "正式上线", "史上最大", "史最大", "首次", "唯一", "全面", "领先", "超过", "吊打", "碾压")
MAGNETIZE_ABSOLUTE_EQUIVALENTS = {"史最大": ("史最大", "史上最大")}
MAGNETIZE_ENTITY_QUALIFIERS = ("国行", "中国", "美国", "韩国", "日本", "全球", "国内", "海外", "当地")
MAGNETIZE_ENTITY_STOPWORDS = {
    "AI",
    "CEO",
    "ARR",
    "IPO",
    "人工",
    "智能",
    "芯片",
    "模型",
    "手机",
    "公司",
    "政府",
    "法院",
    "产品",
    "市场",
}


@dataclass(frozen=True)
class XHSExportResult:
    output_dir: Path
    image_paths: list[Path]
    caption_path: Path
    html_path: Path


@dataclass(frozen=True)
class XHSCoverTitleVariants:
    original: str
    fallback: str
    restrained: str
    punchy: str | None
    source: Literal["ai", "fallback"]
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Card:
    kind: str
    html_body: str


@dataclass(frozen=True)
class CondenseRequest:
    slot_id: str
    slot_type: SlotType
    title: str
    original_text: str
    min_chars: int
    max_chars: int


@dataclass(frozen=True)
class XHSCondenseSlot:
    request: CondenseRequest
    article_index: int
    level: Literal["headline", "brief"]
    kicker: str
    sources: list[dict[str, str]]


class XHSCondenser:
    def __init__(self, responses: dict[str, str | XHSCondenseSlotOutput]) -> None:
        self.responses = responses
        self.cache: dict[str, str] = {}

    def condense(self, request: CondenseRequest, fallback: str) -> str:
        if request.slot_id in self.cache:
            return self.cache[request.slot_id]
        if original_text_is_in_range(
            request.original_text,
            request.min_chars,
            request.max_chars,
            require_complete=request.slot_type != "cover_hook",
        ):
            self.cache[request.slot_id] = normalize_condensed_text(request.original_text, request.slot_type)
            return self.cache[request.slot_id]

        response = self.responses.get(request.slot_id)
        if response is None:
            LOGGER.warning("xhs_condense output missing for %s", request.slot_id)
            self.cache[request.slot_id] = fallback
            return fallback

        candidate_text = response if isinstance(response, str) else response.text
        candidate = normalize_condensed_text(candidate_text, request.slot_type)
        if is_valid_condensed_text(candidate, request):
            self.cache[request.slot_id] = candidate
            return candidate

        LOGGER.warning("xhs_condense output rejected for %s: %s", request.slot_id, candidate)
        self.cache[request.slot_id] = fallback
        return fallback

    def emphasis_terms(self, slot_id: str) -> list[str]:
        response = self.responses.get(slot_id)
        if not isinstance(response, XHSCondenseSlotOutput):
            return []
        return [term for term in response.emphasis_terms if term]


def load_issue_for_xhs(issue_date: str, *, dist_dir: Path = DIST_DIR) -> Issue:
    issue_path = dist_dir / "data" / "issues" / f"{issue_date}.json"
    if not issue_path.exists():
        raise FileNotFoundError(f"Issue JSON not found: {issue_path}")
    return Issue.model_validate_json(issue_path.read_text(encoding="utf-8"))


def export_xhs_issue(
    issue: Issue,
    *,
    output_dir: Path | None = None,
    config: PipelineConfig | None = None,
    ai_condense: bool = False,
    provider: ProviderName | None = None,
    cover_template: CoverTemplate = "classic",
) -> XHSExportResult:
    validate_cover_template(cover_template)
    default_dir_name = issue.issue_date.isoformat()
    if cover_template == "single-hook":
        default_dir_name += "-single-hook"
    elif cover_template == "v2":
        default_dir_name += "-v2"
    out_dir = output_dir or RUNS_DIR / "xhs" / default_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()

    condenser = (
        prepare_xhs_condenser(
            issue,
            out_dir=out_dir,
            config=config,
            provider=provider,
            cover_template=cover_template,
        )
        if ai_condense and config
        else None
    )
    cover_title_variants = None
    if cover_template == "v2" and issue.headlines:
        cover_title_variants = prepare_v2_cover_title_variants(
            issue,
            out_dir=out_dir,
            condenser=condenser,
            config=config,
            provider=provider,
            ai_enabled=ai_condense and config is not None,
        )
        write_cover_title_variants(out_dir / "cover_title_variants.txt", cover_title_variants)
    cards = build_cards(
        issue,
        condenser=condenser,
        cover_template=cover_template,
        v2_cover_title=cover_title_variants.restrained if cover_title_variants else None,
    )
    html_path = out_dir / "cards.html"
    html_path.write_text(render_cards_html(issue, cards), encoding="utf-8")
    caption_path = out_dir / "caption.txt"
    note_title = build_note_title(
        issue,
        out_dir=out_dir,
        config=config,
        provider=provider,
        ai_enabled=ai_condense and config is not None,
    )
    caption_path.write_text(build_caption(issue, title=note_title), encoding="utf-8")
    image_paths = render_card_images(html_path, out_dir, len(cards))
    return XHSExportResult(
        output_dir=out_dir,
        image_paths=image_paths,
        caption_path=caption_path,
        html_path=html_path,
    )


def prepare_xhs_condenser(
    issue: Issue,
    *,
    out_dir: Path,
    config: PipelineConfig,
    provider: ProviderName | None = None,
    cover_template: CoverTemplate = "classic",
) -> XHSCondenser:
    slots = collect_condense_slots(issue, include_cover=cover_template in {"single-hook", "v2"})
    input_path = out_dir / "xhs_condense_input.json"
    input_path.write_text(json.dumps(build_xhs_condense_input(issue, slots), ensure_ascii=False, indent=2), encoding="utf-8")
    selected_provider: ProviderName = provider or config.ai.stage_providers.get("xhs_condense") or config.ai.default_provider
    try:
        output, ai_run = run_ai_task(
            task_type="xhs_condense",
            prompt=build_xhs_condense_file_prompt(input_path.resolve()),
            output_model=XHSCondenseOutput,
            provider=selected_provider,
            config=config,
        )
        save_ai_task_run(
            f"xhs-{issue.issue_date.isoformat()}",
            "xhs_condense",
            ai_run,
            save_attempts=config.logging.save_attempts,
            save_provider_events=config.logging.save_provider_events,
            append_metrics_jsonl=config.logging.append_metrics_jsonl,
        )
        responses = {slot.id: slot for slot in output.slots}
    except AIEngineError as exc:
        if exc.record is not None:
            save_ai_task_run(
                f"xhs-{issue.issue_date.isoformat()}",
                "xhs_condense",
                exc.record,
                save_attempts=config.logging.save_attempts,
                save_provider_events=config.logging.save_provider_events,
                append_metrics_jsonl=config.logging.append_metrics_jsonl,
            )
        LOGGER.warning("xhs_condense batch failed: %s", exc)
        responses = {}
    return XHSCondenser(responses)


def prepare_v2_cover_title_variants(
    issue: Issue,
    *,
    out_dir: Path,
    condenser: XHSCondenser | None,
    config: PipelineConfig | None,
    provider: ProviderName | None = None,
    ai_enabled: bool = False,
) -> XHSCoverTitleVariants:
    article = issue.headlines[0]
    hook_min, hook_max = SLOT_RANGES["cover_hook"]
    fallback = condense_slot(
        article.title_zh,
        slot_id="cover_hook",
        slot_type="cover_hook",
        min_chars=hook_min,
        max_chars=hook_max,
        title=article.title_zh,
        condenser=condenser,
    )
    fallback_variants = XHSCoverTitleVariants(
        original=article.title_zh,
        fallback=fallback,
        restrained=fallback,
        punchy=None,
        source="fallback",
    )
    if not ai_enabled or config is None:
        return fallback_variants

    input_path = out_dir / "xhs_magnetize_input.json"
    input_path.write_text(
        json.dumps(build_xhs_magnetize_input(issue), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    selected_provider: ProviderName = (
        provider or config.ai.stage_providers.get("xhs_magnetize") or config.ai.default_provider
    )
    try:
        output, ai_run = run_ai_task(
            task_type="xhs_magnetize",
            prompt=build_xhs_magnetize_prompt(input_path.resolve()),
            output_model=XHSMagnetizeOutput,
            provider=selected_provider,
            config=config,
        )
        save_ai_task_run(
            f"xhs-{issue.issue_date.isoformat()}",
            "xhs_magnetize",
            ai_run,
            save_attempts=config.logging.save_attempts,
            save_provider_events=config.logging.save_provider_events,
            append_metrics_jsonl=config.logging.append_metrics_jsonl,
        )
    except AIEngineError as exc:
        if exc.record is not None:
            save_ai_task_run(
                f"xhs-{issue.issue_date.isoformat()}",
                "xhs_magnetize",
                exc.record,
                save_attempts=config.logging.save_attempts,
                save_provider_events=config.logging.save_provider_events,
                append_metrics_jsonl=config.logging.append_metrics_jsonl,
            )
        LOGGER.warning("xhs_magnetize failed: %s", exc)
        return XHSCoverTitleVariants(
            original=fallback_variants.original,
            fallback=fallback_variants.fallback,
            restrained=fallback_variants.restrained,
            punchy=None,
            source="fallback",
            rejection_reasons=(f"provider 失败：{exc}",),
        )

    restrained = compact_text(output.restrained)
    punchy = compact_text(output.punchy)
    restrained_reasons = validate_magnetized_title(
        output.restrained,
        original_title=article.title_zh,
        summary=article.summary_zh,
        restrained=True,
    )
    punchy_reasons = validate_magnetized_title(
        output.punchy,
        original_title=article.title_zh,
        summary=article.summary_zh,
        restrained=False,
    )
    reasons = tuple(
        [f"克制版：{reason}" for reason in restrained_reasons]
        + [f"冲版：{reason}" for reason in punchy_reasons]
    )
    if restrained_reasons:
        LOGGER.warning("xhs_magnetize restrained output rejected: %s", "; ".join(restrained_reasons))
    if punchy_reasons:
        LOGGER.warning("xhs_magnetize punchy output rejected: %s", "; ".join(punchy_reasons))
    return XHSCoverTitleVariants(
        original=article.title_zh,
        fallback=fallback,
        restrained=fallback if restrained_reasons else restrained,
        punchy=None if punchy_reasons else punchy,
        source="fallback" if restrained_reasons else "ai",
        rejection_reasons=reasons,
    )


def build_xhs_magnetize_input(issue: Issue) -> dict[str, object]:
    article = issue.headlines[0]
    hook_min, hook_max = SLOT_RANGES["cover_hook"]
    return {
        "publication_name": XHS_PUBLICATION_NAME,
        "issue_date": issue.issue_date.isoformat(),
        "title_zh": article.title_zh,
        "summary_zh": article.summary_zh,
        "target_min": hook_min,
        "target_max": hook_max,
    }


def write_cover_title_variants(path: Path, variants: XHSCoverTitleVariants) -> None:
    punchy = variants.punchy or "无（未通过校验或未启用 AI）"
    reasons = "无" if not variants.rejection_reasons else "；".join(variants.rejection_reasons)
    path.write_text(
        "\n".join(
            [
                f"原标题：{variants.original}",
                f"原标题收敛兜底：{variants.fallback}",
                f"当前使用（克制版）：{variants.restrained}",
                f"冲版备选：{punchy}",
                f"当前来源：{variants.source}",
                f"回退/拒绝原因：{reasons}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def build_note_title(
    issue: Issue,
    *,
    out_dir: Path | None = None,
    config: PipelineConfig | None = None,
    provider: ProviderName | None = None,
    ai_enabled: bool = False,
) -> str:
    fallback = fallback_note_title(issue)
    if not ai_enabled or config is None or out_dir is None:
        return fallback

    input_path = out_dir / "xhs_note_title_input.json"
    input_path.write_text(json.dumps(build_xhs_note_title_input(issue), ensure_ascii=False, indent=2), encoding="utf-8")
    selected_provider: ProviderName = provider or config.ai.stage_providers.get("xhs_note_title") or config.ai.default_provider
    try:
        output, ai_run = run_ai_task(
            task_type="xhs_note_title",
            prompt=build_xhs_note_title_prompt(input_path.resolve()),
            output_model=XHSNoteTitleOutput,
            provider=selected_provider,
            config=config,
        )
        save_ai_task_run(
            f"xhs-{issue.issue_date.isoformat()}",
            "xhs_note_title",
            ai_run,
            save_attempts=config.logging.save_attempts,
            save_provider_events=config.logging.save_provider_events,
            append_metrics_jsonl=config.logging.append_metrics_jsonl,
        )
    except AIEngineError as exc:
        if exc.record is not None:
            save_ai_task_run(
                f"xhs-{issue.issue_date.isoformat()}",
                "xhs_note_title",
                exc.record,
                save_attempts=config.logging.save_attempts,
                save_provider_events=config.logging.save_provider_events,
                append_metrics_jsonl=config.logging.append_metrics_jsonl,
            )
        LOGGER.warning("xhs_note_title failed: %s", exc)
        return fallback

    title = compact_text(output.title)
    if is_valid_note_title(title, issue):
        return title

    LOGGER.warning("xhs_note_title output rejected: %s", title)
    return fallback


def build_xhs_note_title_input(issue: Issue) -> dict[str, object]:
    return {
        "publication_name": XHS_PUBLICATION_NAME,
        "issue_date": issue.issue_date.isoformat(),
        "date_cn": issue.date_cn,
        "title_max_chars": 20,
        "headlines": [
            {
                "index": index,
                "title": article.title_zh,
                "kicker": article.kicker,
                "summary_zh": compact_text(article.summary_zh),
                "ai_impact": compact_text(article.ai_impact),
                "sources": source_payload(article.sources),
            }
            for index, article in enumerate(issue.headlines[:MAX_HEADLINE_CARDS], start=1)
        ],
        "brief_titles": [article.title_zh for article in issue.briefs],
    }


def collect_condense_slots(issue: Issue, *, include_cover: bool = False) -> list[XHSCondenseSlot]:
    slots: list[XHSCondenseSlot] = []
    if include_cover and issue.headlines:
        article = issue.headlines[0]
        sources = source_payload(article.sources)
        hook_min, hook_max = SLOT_RANGES["cover_hook"]
        sub_min, sub_max = SLOT_RANGES["cover_sub"]
        slots.extend(
            [
                XHSCondenseSlot(
                    request=CondenseRequest(
                        slot_id="cover_hook",
                        slot_type="cover_hook",
                        title=article.title_zh,
                        original_text=article.title_zh,
                        min_chars=hook_min,
                        max_chars=hook_max,
                    ),
                    article_index=1,
                    level="headline",
                    kicker=article.kicker,
                    sources=sources,
                ),
                XHSCondenseSlot(
                    request=CondenseRequest(
                        slot_id="cover_sub",
                        slot_type="cover_sub",
                        title=article.title_zh,
                        original_text=article.summary_zh,
                        min_chars=sub_min,
                        max_chars=sub_max,
                    ),
                    article_index=1,
                    level="headline",
                    kicker=article.kicker,
                    sources=sources,
                ),
            ]
        )
    for index, article in enumerate(issue.headlines[:MAX_HEADLINE_CARDS], start=1):
        summary_min, summary_max = SLOT_RANGES["headline_summary"]
        impact_min, impact_max = SLOT_RANGES["headline_impact"]
        sources = source_payload(article.sources)
        slots.append(
            XHSCondenseSlot(
                request=CondenseRequest(
                    slot_id=f"headline_{index:02d}_summary",
                    slot_type="headline_summary",
                    title=article.title_zh,
                    original_text=article.summary_zh,
                    min_chars=summary_min,
                    max_chars=summary_max,
                ),
                article_index=index,
                level="headline",
                kicker=article.kicker,
                sources=sources,
            )
        )
        slots.append(
            XHSCondenseSlot(
                request=CondenseRequest(
                    slot_id=f"headline_{index:02d}_impact",
                    slot_type="headline_impact",
                    title=article.title_zh,
                    original_text=article.ai_impact,
                    min_chars=impact_min,
                    max_chars=impact_max,
                ),
                article_index=index,
                level="headline",
                kicker=article.kicker,
                sources=sources,
            )
        )
    for index, article in enumerate(issue.briefs, start=1):
        min_chars, max_chars = SLOT_RANGES["brief_summary"]
        slots.append(
            XHSCondenseSlot(
                request=CondenseRequest(
                    slot_id=f"brief_{index:02d}_summary",
                    slot_type="brief_summary",
                    title=article.title_zh,
                    original_text=article.summary_zh,
                    min_chars=min_chars,
                    max_chars=max_chars,
                ),
                article_index=index,
                level="brief",
                kicker="",
                sources=source_payload(article.sources),
            )
        )
    return slots


def build_xhs_condense_input(issue: Issue, slots: Sequence[XHSCondenseSlot]) -> dict[str, object]:
    used_slot_types = {slot.request.slot_type for slot in slots}
    return {
        "publication_name": XHS_PUBLICATION_NAME,
        "issue_date": issue.issue_date.isoformat(),
        "date_cn": issue.date_cn,
        "slot_ranges": {
            slot_type: {"target_min": min_chars, "target_max": max_chars}
            for slot_type, (min_chars, max_chars) in SLOT_RANGES.items()
            if slot_type in used_slot_types
        },
        "slots": [
            {
                "id": slot.request.slot_id,
                "slot_type": slot.request.slot_type,
                "level": slot.level,
                "article_index": slot.article_index,
                "title": slot.request.title,
                "kicker": slot.kicker,
                "sources": slot.sources,
                "original_text": compact_text(slot.request.original_text),
                "target_min": slot.request.min_chars,
                "target_max": slot.request.max_chars,
            }
            for slot in slots
        ],
    }


def build_cards(
    issue: Issue,
    *,
    condenser: XHSCondenser | None = None,
    cover_template: CoverTemplate = "classic",
    v2_cover_title: str | None = None,
) -> list[Card]:
    validate_cover_template(cover_template)
    if cover_template == "single-hook":
        cover = single_hook_cover_card(issue, condenser=condenser)
    elif cover_template == "v2":
        cover = v2_cover_card(issue, condenser=condenser, title_override=v2_cover_title)
    else:
        cover = cover_card(issue)
    cards = [cover]
    for index, article in enumerate(issue.headlines[:MAX_HEADLINE_CARDS], start=1):
        cards.append(headline_card(issue, article, index, condenser=condenser))
    cards.extend(brief_cards(issue, condenser=condenser))
    return cards


def cover_card(issue: Issue) -> Card:
    headline_count = min(len(issue.headlines), MAX_HEADLINE_CARDS)
    headline_items = "\n".join(
        f"""
        <div class="hl">
          <div class="no">{idx:02d}</div>
          <div class="t">{escape(article.title_zh)}</div>
        </div>
        """
        for idx, article in enumerate(issue.headlines[:MAX_HEADLINE_CARDS], start=1)
    )
    body = f"""
      <div class="cv-top">
        <span class="pill">{headline_count} 条头条</span>
        <span class="pill">{len(issue.briefs)} 条速览</span>
        <span class="pill">约 {estimate_reading_minutes(issue)} 分钟</span>
      </div>
      <div class="datewrap"><span class="date">{escape(date_dot(issue))}</span><span class="dow">{escape(weekday_cn(issue))}</span></div>
      <h1 class="title">{escape(XHS_PUBLICATION_NAME)}</h1>
      <div class="rule"></div>
      <div class="lead-label">今日头条</div>
      {headline_items}
      <div class="swipe">
        <span>左滑翻阅</span>
        <svg width="66" height="22" viewBox="0 0 66 22" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M6 4l7 7-7 7"/><path d="M25 4l7 7-7 7"/><path d="M44 4l7 7-7 7"/>
        </svg>
      </div>
    """
    return Card(kind="cover", html_body=body)


def single_hook_cover_card(issue: Issue, *, condenser: XHSCondenser | None = None) -> Card:
    if not issue.headlines:
        return cover_card(issue)

    article = issue.headlines[0]
    hook_min, hook_max = SLOT_RANGES["cover_hook"]
    sub_min, sub_max = SLOT_RANGES["cover_sub"]
    hook = condense_slot(
        article.title_zh,
        slot_id="cover_hook",
        slot_type="cover_hook",
        min_chars=hook_min,
        max_chars=hook_max,
        title=article.title_zh,
        condenser=condenser,
    )
    sub = condense_slot(
        article.summary_zh,
        slot_id="cover_sub",
        slot_type="cover_sub",
        min_chars=sub_min,
        max_chars=sub_max,
        title=article.title_zh,
        condenser=condenser,
    )
    size_class = "l" if len(hook) <= COVER_HOOK_LARGE_MAX_CHARS else "m"
    emphasis_terms = condenser.emphasis_terms("cover_hook") if condenser else []
    hook_html = emphasize_cover_text(hook, emphasis_terms)
    sub_html = emphasize_cover_text(sub, [])
    kicker = f"{article.kicker} · 今日头条" if article.kicker else "今日头条"
    remaining_headlines = max(0, min(len(issue.headlines), MAX_HEADLINE_CARDS) - 1)
    body = f"""
      <div class="cv2-head">
        <div class="cv2-brand"><span class="seal-mark"></span><span class="name">{escape(XHS_PUBLICATION_NAME)}</span></div>
        <div class="cv2-date"><span class="d">{escape(date_dot(issue))}</span><span class="dow">{escape(weekday_cn(issue))}</span></div>
      </div>
      <div class="cv2-hook">
        <div class="cv2-kicker">{escape(kicker)}</div>
        <h1 class="cv2-big {size_class}">{hook_html}</h1>
        <p class="cv2-sub">{sub_html}</p>
      </div>
      <div class="cv2-foot">
        <div class="bar"></div>
        <div class="row">
          <span class="more">+{remaining_headlines} 条头条 · {len(issue.briefs)} 速览 · 约 {estimate_reading_minutes(issue)} 分钟</span>
          <span class="swipe">左滑翻阅
            <svg width="66" height="22" viewBox="0 0 66 22" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M6 4l7 7-7 7"/><path d="M25 4l7 7-7 7"/><path d="M44 4l7 7-7 7"/>
            </svg>
          </span>
        </div>
      </div>
    """
    return Card(kind="cover2", html_body=body)


def v2_cover_card(
    issue: Issue,
    *,
    condenser: XHSCondenser | None = None,
    title_override: str | None = None,
) -> Card:
    if not issue.headlines:
        return cover_card(issue)

    article = issue.headlines[0]
    hook_min, hook_max = SLOT_RANGES["cover_hook"]
    sub_min, sub_max = SLOT_RANGES["cover_sub"]
    fallback_hook = condense_slot(
        article.title_zh,
        slot_id="cover_hook",
        slot_type="cover_hook",
        min_chars=hook_min,
        max_chars=hook_max,
        title=article.title_zh,
        condenser=condenser,
    )
    hook = title_override or fallback_hook
    sub = condense_slot(
        article.summary_zh,
        slot_id="cover_sub",
        slot_type="cover_sub",
        min_chars=sub_min,
        max_chars=sub_max,
        title=article.title_zh,
        condenser=condenser,
    )
    emphasis_terms = condenser.emphasis_terms("cover_hook") if condenser else []
    hook_html = emphasize_v2_cover_text(hook, emphasis_terms)
    kicker = f"{article.kicker} · 今日头条" if article.kicker else "今日头条"
    remaining_headlines = max(0, min(len(issue.headlines), MAX_HEADLINE_CARDS) - 1)
    body = f"""
      <div class="cv2-head">
        <div class="cv2-brand"><span class="seal-mark"></span><span class="name">{escape(XHS_PUBLICATION_NAME)}</span></div>
        <div class="cv2-date"><span class="d">{escape(date_dot(issue))}</span><span class="dow">{escape(weekday_cn(issue))}</span></div>
      </div>
      <div class="hook3">
        <div class="eyebrow">{escape(kicker)}</div>
        <h1 class="title3">{hook_html}</h1>
        <p class="sub3">{escape(sub)}</p>
      </div>
      <div class="foot3">
        <div class="row">
          <span>+{remaining_headlines} 条头条 · {len(issue.briefs)} 速览 · 约 {estimate_reading_minutes(issue)} 分钟</span>
          <span class="swipe">左滑翻阅
            <svg width="66" height="22" viewBox="0 0 66 22" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M6 4l7 7-7 7"/><path d="M25 4l7 7-7 7"/><path d="M44 4l7 7-7 7"/>
            </svg>
          </span>
        </div>
      </div>
    """
    return Card(kind="coverv2", html_body=body)


def headline_card(
    issue: Issue,
    article: HeadlineArticle,
    index: int,
    *,
    condenser: XHSCondenser | None = None,
) -> Card:
    total = min(len(issue.headlines), MAX_HEADLINE_CARDS)
    summary_min, summary_max = SLOT_RANGES["headline_summary"]
    impact_min, impact_max = SLOT_RANGES["headline_impact"]
    kicker = f'<div class="kicker">{escape(article.kicker)}</div>' if article.kicker else ""
    body = f"""
      <div class="topbar"><span class="l">HEADLINE {index}</span><span>{escape(date_dot(issue))}</span></div>
      <div class="hl-body">
        {kicker}
        <h2>{escape(article.title_zh)}</h2>
        <div class="fact">
          <div class="label">发生了什么</div>
          <p>{escape(condense_slot(article.summary_zh, slot_id=f"headline_{index:02d}_summary", slot_type="headline_summary", min_chars=summary_min, max_chars=summary_max, title=article.title_zh, condenser=condenser))}</p>
        </div>
        <div class="impact">
          <div class="label"><span class="chip">AI</span>为什么重要 · AI 分析</div>
          <p>{escape(condense_slot(article.ai_impact, slot_id=f"headline_{index:02d}_impact", slot_type="headline_impact", min_chars=impact_min, max_chars=impact_max, title=article.title_zh, condenser=condenser))}</p>
        </div>
        <div class="src">来源 · {escape(source_names(article.sources))}</div>
      </div>
      <div class="foot"><span>{escape(XHS_PUBLICATION_NAME)} · {escape(date_dot(issue))}</span><span class="pg">头条 {index} / {total}</span></div>
    """
    return Card(kind="headline", html_body=body)


def brief_cards(issue: Issue, *, condenser: XHSCondenser | None = None) -> list[Card]:
    pages = paginate_briefs(issue.briefs, condenser=condenser)
    cards: list[Card] = []
    start_no = 1
    for page_index, page_items in enumerate(pages, start=1):
        items_html = "\n".join(
            brief_item_html(article, start_no + offset, condenser=condenser)
            for offset, article in enumerate(page_items)
        )
        body = f"""
          <div class="topbar"><span class="l">BRIEFS</span><span>{escape(date_dot(issue))}</span></div>
          <h2>今日速览</h2>
          <div class="brief-list">
            {items_html}
          </div>
          <div class="foot"><span>{escape(XHS_PUBLICATION_NAME)} · {escape(date_dot(issue))}</span><span class="pg">速览 {page_index} / {len(pages)}</span></div>
        """
        cards.append(Card(kind="briefs", html_body=body))
        start_no += len(page_items)
    return cards


def brief_item_html(article: BriefArticle, number: int, *, condenser: XHSCondenser | None = None) -> str:
    min_chars, max_chars = SLOT_RANGES["brief_summary"]
    summary = condense_slot(
        article.summary_zh,
        slot_id=f"brief_{number:02d}_summary",
        slot_type="brief_summary",
        min_chars=min_chars,
        max_chars=max_chars,
        title=article.title_zh,
        condenser=condenser,
    )
    return f"""
      <div class="brief-item">
        <div class="no">{number:02d}</div>
        <div>
          <h3>{escape(article.title_zh)}</h3>
          <p>{escape(summary)}</p>
          <div class="src">来源 · {escape(source_names(article.sources))}</div>
        </div>
      </div>
    """


def build_caption(issue: Issue, *, title: str | None = None) -> str:
    note_title = title if title is not None else fallback_note_title(issue)
    return f"{note_title}\n\n{build_note_body(issue)}\n"


def build_note_body(issue: Issue) -> str:
    headline_lines = "\n".join(
        f"{idx}. {article.title_zh}"
        for idx, article in enumerate(issue.headlines[:MAX_HEADLINE_CARDS], start=1)
    )
    parts = [
        NOTE_HASHTAGS,
        "",
        "今日头条：",
        headline_lines,
        "",
        NOTE_SLOGAN,
    ]
    return limit_note_body("\n".join(part for part in parts if part is not None), issue)


def limit_note_body(body: str, issue: Issue) -> str:
    if len(body) <= 1000:
        return body
    for headline_count in range(min(len(issue.headlines), MAX_HEADLINE_CARDS) - 1, 0, -1):
        headline_lines = "\n".join(
            f"{idx}. {article.title_zh}"
            for idx, article in enumerate(issue.headlines[:headline_count], start=1)
        )
        shorter = "\n".join([NOTE_HASHTAGS, "", "今日头条：", headline_lines, "", NOTE_SLOGAN])
        if len(shorter) <= 1000:
            return shorter
    return finish_complete_text("\n".join([NOTE_HASHTAGS, "", NOTE_SLOGAN])[:1000])


def fallback_note_title(issue: Issue) -> str:
    return f"{XHS_PUBLICATION_NAME} · {issue.issue_date.month}月{issue.issue_date.day}日"


def is_valid_note_title(title: str, issue: Issue) -> bool:
    if not title or len(title) > 20 or "\n" in title or "…" in title or "..." in title:
        return False
    if any(term in title for term in GENERIC_NOTE_TITLE_TERMS):
        return False
    allowed_text = " ".join(
        [
            XHS_PUBLICATION_NAME,
            issue.issue_date.isoformat(),
            issue.date_cn,
            f"{issue.issue_date.month}月{issue.issue_date.day}日",
        ]
        + [
            f"{article.title_zh} {article.summary_zh} {article.ai_impact}"
            for article in issue.headlines[:MAX_HEADLINE_CARDS]
        ]
        + [article.title_zh for article in issue.briefs]
    )
    return numbers_in_text(title).issubset(numbers_in_text(allowed_text))


def render_cards_html(issue: Issue, cards: Sequence[Card]) -> str:
    card_html = "\n".join(
        f"""
        <section id="card-{index}" class="card {card.kind}">
          {card.html_body}
        </section>
        """
        for index, card in enumerate(cards, start=1)
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(XHS_PUBLICATION_NAME)} · 小红书导出</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,900&family=JetBrains+Mono:wght@500;700&family=Noto+Serif+SC:wght@400;500;600;700;900&display=swap" rel="stylesheet">
  <style>{CSS}</style>
</head>
<body>
  <div class="stage">{card_html}</div>
</body>
</html>
"""


def render_card_images(html_path: Path, output_dir: Path, count: int) -> list[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on local tooling
        raise RuntimeError("Playwright is required for export-xhs. Install it and run `playwright install chromium`.") from exc

    image_paths: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": CARD_WIDTH + 120, "height": CARD_HEIGHT + 120},
            device_scale_factor=1,
        )
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        validate_single_hook_layout(page)
        validate_v2_cover_layout(page)
        for index in range(1, count + 1):
            output_path = output_dir / f"{index:02d}.png"
            page.locator(f"#card-{index}").screenshot(path=str(output_path))
            image_paths.append(output_path)
        browser.close()
    return image_paths


def validate_single_hook_layout(page: object) -> None:
    cover = page.locator("#card-1.cover2")  # type: ignore[attr-defined]
    if cover.count() == 0:
        return

    def content_fits() -> bool:
        card_box = cover.bounding_box()
        big_box = cover.locator(".cv2-big").bounding_box()
        sub_box = cover.locator(".cv2-sub").bounding_box()
        if card_box is None or big_box is None or sub_box is None:
            return False
        content_top = min(big_box["y"], sub_box["y"]) - card_box["y"]
        content_bottom = max(big_box["y"] + big_box["height"], sub_box["y"] + sub_box["height"]) - card_box["y"]
        return content_top >= COVER_SAFE_TOP and content_bottom <= COVER_SAFE_BOTTOM

    if not content_fits() and cover.locator(".cv2-big.l").count():
        cover.locator(".cv2-big").evaluate("element => { element.classList.remove('l'); element.classList.add('m'); }")
    if not content_fits():
        raise RuntimeError("single-hook cover content exceeds the 1080x1080 feed safe area")


def validate_v2_cover_layout(page: object) -> None:
    cover = page.locator("#card-1.coverv2")  # type: ignore[attr-defined]
    if cover.count() == 0:
        return

    title = cover.locator(".title3")

    def content_fits() -> bool:
        card_box = cover.bounding_box()
        title_box = title.bounding_box()
        sub_box = cover.locator(".sub3").bounding_box()
        if card_box is None or title_box is None or sub_box is None:
            return False
        content_top = min(title_box["y"], sub_box["y"]) - card_box["y"]
        content_bottom = max(title_box["y"] + title_box["height"], sub_box["y"] + sub_box["height"]) - card_box["y"]
        return content_top >= COVER_SAFE_TOP and content_bottom <= COVER_SAFE_BOTTOM

    for font_size in V2_TITLE_FONT_SIZES:
        title.evaluate("(element, size) => { element.style.fontSize = `${size}px`; }", font_size)
        if content_fits():
            return
    raise RuntimeError("v2 cover content exceeds the 1080x1080 feed safe area")


def paginate_briefs(items: Sequence[BriefArticle], *, condenser: XHSCondenser | None = None) -> list[list[BriefArticle]]:
    pages: list[list[BriefArticle]] = []
    current: list[BriefArticle] = []
    current_height = 0
    for number, item in enumerate(items, start=1):
        height = estimate_brief_item_height(item, number=number, condenser=condenser)
        should_break = (
            current
            and (
                len(current) >= BRIEF_PAGE_MAX_ITEMS
                or current_height + height > BRIEF_LIST_HEIGHT_LIMIT
                or (height > BRIEF_ITEM_SOFT_LIMIT and current_height > BRIEF_LIST_HEIGHT_LIMIT - BRIEF_ITEM_SOFT_LIMIT)
            )
        )
        if should_break:
            pages.append(current)
            current = []
            current_height = 0
        current.append(item)
        current_height += height
    if current:
        pages.append(current)
    return pages


def estimate_brief_item_height(article: BriefArticle, *, number: int | None = None, condenser: XHSCondenser | None = None) -> int:
    min_chars, max_chars = SLOT_RANGES["brief_summary"]
    summary = condense_slot(
        article.summary_zh,
        slot_id=f"brief_{number:02d}_summary" if number is not None else "",
        slot_type="brief_summary",
        min_chars=min_chars,
        max_chars=max_chars,
        title=article.title_zh,
        condenser=condenser,
    )
    title_lines = max(1, math.ceil(len(article.title_zh) / 22))
    summary_lines = max(1, math.ceil(len(summary) / 27))
    title_height = title_lines * 47
    summary_height = summary_lines * 44
    source_height = 26
    vertical_padding_and_margins = 62
    return title_height + summary_height + source_height + vertical_padding_and_margins


def condense_slot(
    text: str,
    *,
    slot_id: str = "",
    slot_type: SlotType,
    min_chars: int,
    max_chars: int,
    title: str = "",
    condenser: XHSCondenser | None = None,
) -> str:
    fallback = fallback_condense_slot(text, slot_type=slot_type, min_chars=min_chars, max_chars=max_chars)
    if condenser is None:
        return fallback
    request = CondenseRequest(
        slot_id=slot_id,
        slot_type=slot_type,
        title=title,
        original_text=text,
        min_chars=min_chars,
        max_chars=max_chars,
    )
    return condenser.condense(request, fallback)


def fallback_condense_slot(text: str, *, slot_type: SlotType, min_chars: int, max_chars: int) -> str:
    value = compact_text(text)
    if not value:
        return ""
    if slot_type == "cover_hook":
        return fallback_cover_hook(value, min_chars=min_chars, max_chars=max_chars)
    if min_chars <= len(value) <= max_chars and ends_complete(value):
        return value
    if len(value) <= max_chars:
        return finish_complete_text(value)

    selected: list[str] = []
    total = 0
    for unit in complete_text_units(value):
        next_total = total + len(unit)
        if selected and next_total > max_chars:
            break
        if not selected and next_total > max_chars:
            selected.append(fit_long_unit(unit, max_chars=max_chars))
            break
        selected.append(unit)
        total = next_total
    return finish_complete_text("".join(selected))


def fallback_cover_hook(text: str, *, min_chars: int, max_chars: int) -> str:
    value = compact_text(text).strip("。！？!? ")
    if len(value) <= max_chars:
        return value

    clauses = [part.strip("，,：:；;。！？!? ") for part in re.split(r"[，,：:；;。！？!?]", value) if part.strip()]
    selected = ""
    for clause in clauses:
        candidate = clause if not selected else f"{selected}，{clause}"
        if len(candidate) > max_chars:
            break
        selected = candidate
    if len(selected) >= min_chars:
        return selected
    return value[:max_chars].rstrip("，,、；;：: ")


def normalize_condensed_text(text: str, slot_type: SlotType) -> str:
    value = compact_text(text)
    if slot_type == "cover_hook":
        return value.strip("。！？!? ")
    return finish_complete_text(value)


def original_text_is_in_range(text: str, min_chars: int, max_chars: int, *, require_complete: bool = True) -> bool:
    value = compact_text(text)
    return min_chars <= len(value) <= max_chars and (ends_complete(value) if require_complete else True)


def is_valid_condensed_text(text: str, request: CondenseRequest) -> bool:
    if not text or "…" in text or "..." in text:
        return False
    if request.slot_type != "cover_hook" and not ends_complete(text):
        return False
    if len(text) > request.max_chars:
        return False
    if len(compact_text(request.original_text)) >= request.min_chars and len(text) < request.min_chars:
        return False
    return numbers_in_text(text).issubset(numbers_in_text(request.original_text + request.title))


def emphasize_cover_text(text: str, emphasis_terms: Sequence[str]) -> str:
    terms = sorted({compact_text(term) for term in emphasis_terms if compact_text(term) in text}, key=len, reverse=True)
    matches: list[tuple[int, int]] = []
    if terms:
        for term in terms:
            matches.extend((match.start(), match.end()) for match in re.finditer(re.escape(term), text))
    else:
        number_pattern = r"\d+(?:\.\d+)?(?:万亿|亿美元|亿元|亿|万|%|美元|元|股)?"
        matches.extend((match.start(), match.end()) for match in re.finditer(number_pattern, text))

    selected: list[tuple[int, int]] = []
    for start, end in sorted(matches, key=lambda span: (span[0], -(span[1] - span[0]))):
        if any(start < existing_end and end > existing_start for existing_start, existing_end in selected):
            continue
        selected.append((start, end))
    selected.sort()

    parts: list[str] = []
    cursor = 0
    for start, end in selected:
        parts.append(escape(text[cursor:start]))
        parts.append(f"<em>{escape(text[start:end])}</em>")
        cursor = end
    parts.append(escape(text[cursor:]))
    return "".join(parts)


def emphasize_v2_cover_text(text: str, emphasis_terms: Sequence[str]) -> str:
    terms: list[str] = []
    for term in emphasis_terms:
        value = compact_text(term)
        if value and value in text and value not in terms:
            terms.append(value)
    match: re.Match[str] | None = None
    if terms:
        match = re.search(re.escape(terms[0]), text)
    if match is None:
        number_pattern = r"\d+(?:\.\d+)?(?:万亿|亿美元|亿元|万股|亿股|亿|万|%|美元|元|股)?"
        match = re.search(number_pattern, text)
    if match is None:
        return escape(text)
    return "".join(
        [
            escape(text[: match.start()]),
            f'<span class="mark">{escape(text[match.start() : match.end()])}</span>',
            escape(text[match.end() :]),
        ]
    )


def validate_magnetized_title(
    title: str,
    *,
    original_title: str,
    summary: str,
    restrained: bool,
) -> list[str]:
    reasons: list[str] = []
    value = compact_text(title)
    min_chars, max_chars = SLOT_RANGES["cover_hook"]
    source_text = f"{original_title} {summary}"

    if not value:
        return ["输出为空"]
    if "\n" in title or "\r" in title:
        reasons.append("包含换行")
    if "…" in value or "..." in value:
        reasons.append("包含省略号")
    if not min_chars <= len(value) <= max_chars:
        reasons.append(f"长度 {len(value)} 不在 {min_chars}-{max_chars} 字区间")

    if not normalized_numbers(value).issubset(normalized_numbers(source_text)):
        reasons.append("出现原文没有的数字")

    source_anchors = subject_anchors(source_text)
    candidate_anchors = subject_anchors(value)
    if not source_anchors or not candidate_anchors or not source_anchors.intersection(candidate_anchors):
        reasons.append("未保留可验证的原文主体")
    primary_anchor = primary_subject_anchor(value)
    if primary_anchor is None or primary_anchor.casefold() not in unicodedata.normalize("NFKC", source_text).casefold():
        reasons.append("候选标题的首个主体不在原文中")

    source_latin = latin_entity_terms(source_text)
    candidate_latin = latin_entity_terms(value)
    unknown_latin = candidate_latin - source_latin
    if unknown_latin:
        reasons.append(f"出现原文没有的英文主体：{', '.join(sorted(unknown_latin))}")

    if restrained:
        banned = [term for term in MAGNETIZE_RESTRAINED_BANNED_TERMS if term in value]
        if banned:
            reasons.append(f"克制版命中情绪词：{', '.join(banned)}")

    if any(term in original_title for term in MAGNETIZE_FUTURE_SOURCE_TERMS) and not any(
        term in value for term in MAGNETIZE_FUTURE_OUTPUT_TERMS
    ):
        reasons.append("丢失计划/目标等未来语气")

    unsupported_absolute = [
        term
        for term in MAGNETIZE_ABSOLUTE_TERMS
        if term in value
        and not any(source_term in source_text for source_term in MAGNETIZE_ABSOLUTE_EQUIVALENTS.get(term, (term,)))
    ]
    if unsupported_absolute:
        reasons.append(f"新增原文没有的程度词：{', '.join(unsupported_absolute)}")
    return reasons


def normalized_numbers(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text)
    return numbers_in_text(normalized)


def latin_entity_terms(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text)
    terms = {
        match.group(0).strip().casefold()
        for match in re.finditer(
            r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+-]*(?:\s+[A-Za-z][A-Za-z0-9.+-]*)?(?![A-Za-z0-9])",
            normalized,
        )
    }
    return {term for term in terms if term.upper() not in MAGNETIZE_ENTITY_STOPWORDS}


def subject_anchors(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", compact_text(text))
    anchors = set(latin_entity_terms(normalized))
    fragments = re.split(
        r"[，,：:|；;。！？!?()（）“”\"'、]"
        r"|把|向|由|与|及|发布|宣布|曝光|获批|获备案|计划|目标|预计|暂停|遭|起诉|投资|收购|推出|回应",
        normalized,
    )
    for fragment in fragments:
        value = re.sub(r"^\d+(?:\.\d+)?(?:年|月|日|小时|倍)?", "", fragment).strip()
        for qualifier in MAGNETIZE_ENTITY_QUALIFIERS:
            if value.startswith(qualifier):
                value = value[len(qualifier) :]
                break
        value = re.split(r"等\d*款|等|和", value, maxsplit=1)[0]
        cjk_runs = re.findall(r"[一-鿿]{2,12}", value)
        if not cjk_runs:
            continue
        run = cjk_runs[0]
        for length in (2, 3, 4):
            if len(run) >= length:
                anchor = run[:length]
                if anchor not in MAGNETIZE_ENTITY_STOPWORDS:
                    anchors.add(anchor)
    return anchors


def primary_subject_anchor(text: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", compact_text(text))
    for fragment in re.split(r"[，,：:；;。！？!?()（）“”\"'、]", normalized):
        value = re.sub(r"^\d+(?:\.\d+)?(?:到|至|-)?\d*(?:年|月|日|小时|倍|%|亿|万)?", "", fragment).strip()
        if any(value.startswith(term) for term in MAGNETIZE_RESTRAINED_BANNED_TERMS):
            continue
        for qualifier in MAGNETIZE_ENTITY_QUALIFIERS:
            if value.startswith(qualifier):
                value = value[len(qualifier) :]
                break
        latin = re.search(
            r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9.+-]*(?:\s+[A-Za-z][A-Za-z0-9.+-]*)?(?![A-Za-z0-9])",
            value,
        )
        cjk = re.search(r"[一-鿿]{2,}", value)
        candidates = [match for match in (latin, cjk) if match is not None]
        if not candidates:
            continue
        first = min(candidates, key=lambda match: match.start())
        anchor = first.group(0)
        if re.fullmatch(r"[一-鿿]+", anchor):
            anchor = anchor[:2]
        if anchor.upper() not in MAGNETIZE_ENTITY_STOPWORDS:
            return anchor
    return None


def validate_cover_template(cover_template: str) -> None:
    if cover_template not in {"classic", "single-hook", "v2"}:
        raise ValueError(f"Unsupported XHS cover template: {cover_template}")


def numbers_in_text(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?%?", text))


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def complete_text_units(text: str) -> list[str]:
    sentences = re.findall(r"[^。！？!?]+[。！？!?]?", text)
    units: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= 72:
            units.append(sentence)
            continue
        semicolon_parts = re.findall(r"[^；;]+[；;]?", sentence)
        for part in semicolon_parts:
            part = part.strip()
            if not part:
                continue
            if len(part) <= 58:
                units.append(part)
            else:
                units.extend(fragment.strip() for fragment in re.findall(r"[^，,]+[，,]?", part) if fragment.strip())
    return units


def fit_long_unit(unit: str, *, max_chars: int) -> str:
    fragments = [fragment.strip() for fragment in re.findall(r"[^，,、：:；;]+[，,、：:；;]?", unit) if fragment.strip()]
    selected: list[str] = []
    total = 0
    for fragment in fragments:
        next_total = total + len(fragment)
        if selected and next_total > max_chars:
            break
        selected.append(fragment)
        total = next_total
    if selected:
        return "".join(selected)
    return unit[:max_chars]


def finish_complete_text(text: str) -> str:
    value = text.strip().rstrip("，,、；;：: ")
    if value and not ends_complete(value):
        value += "。"
    return value


def ends_complete(text: str) -> bool:
    return bool(text) and text[-1] in "。！？!?"


def estimate_reading_minutes(issue: Issue) -> int:
    text = " ".join(
        [article.title_zh + article.summary_zh for article in issue.headlines]
        + [article.title_zh + article.summary_zh for article in issue.briefs]
    )
    return max(2, min(6, math.ceil(len(text) / 520)))


def source_names(sources: Sequence[object]) -> str:
    names: list[str] = []
    for source in sources:
        name = getattr(source, "name", "")
        if name:
            names.append(name)
    return "、".join(names) or "原文来源"


def source_payload(sources: Sequence[object]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for source in sources:
        name = getattr(source, "name", "")
        url = getattr(source, "url", "")
        if name or url:
            payload.append({"name": str(name), "url": str(url)})
    return payload


def date_dot(issue: Issue) -> str:
    return issue.issue_date.strftime("%Y.%m.%d")


def weekday_cn(issue: Issue) -> str:
    return WEEKDAYS_CN[issue.issue_date.weekday()]


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def remove_output_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


CSS = """
:root{
  --paper:#F4EFE3; --ink:#1A1714; --ink-2:#3D372F; --ink-3:#5A5247;
  --muted:#8E836F; --rule:#D2C8B2; --rule-2:#B7AB90;
  --seal:#A6342B; --seal-soft:#F0DCD5; --ai-bg:#ECE7DA; --field:#FBF8EF;
  --sans:"Noto Serif SC","Songti SC",serif;
  --disp:"Fraunces","Georgia",serif;
  --mono:"JetBrains Mono",ui-monospace,monospace;
}
*{box-sizing:border-box;}
body{
  margin:0;
  background:#cfc6b2;
  color:var(--ink);
  font-family:var(--sans);
}
.stage{
  display:grid;
  gap:48px;
  padding:60px;
}
.card{
  width:1080px;
  height:1440px;
  background:var(--paper);
  color:var(--ink);
  position:relative;
  overflow:hidden;
}
.card .topbar{
  display:flex;
  justify-content:space-between;
  align-items:center;
  font-family:var(--mono);
  font-size:22px;
  letter-spacing:.14em;
  color:var(--muted);
}
.card .topbar .l{
  color:var(--seal);
  font-weight:700;
}
.card .foot{
  position:absolute;
  left:0;
  right:0;
  bottom:0;
  padding:0 72px 40px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  font-family:var(--mono);
  font-size:20px;
  letter-spacing:.12em;
  color:var(--muted);
}
.card .foot .pg{
  color:var(--seal);
  font-weight:700;
}
.cover{
  padding:88px 84px;
}
.cover .cv-top{
  display:flex;
  justify-content:flex-end;
  flex-wrap:wrap;
  gap:14px;
  margin-bottom:48px;
}
.pill{
  font-family:var(--mono);
  font-weight:700;
  font-size:22px;
  letter-spacing:.06em;
  color:var(--seal);
  background:var(--seal-soft);
  border:1.5px solid var(--seal);
  border-radius:999px;
  padding:11px 22px;
  white-space:nowrap;
}
.cover .datewrap{
  display:flex;
  align-items:center;
  gap:24px;
  margin-bottom:28px;
}
.cover .date{
  font-family:var(--mono);
  font-weight:700;
  font-size:82px;
  letter-spacing:.01em;
  color:var(--seal);
  line-height:1;
}
.cover .dow{
  font-family:var(--sans);
  font-weight:900;
  font-size:46px;
  line-height:1;
  color:var(--paper);
  background:var(--seal);
  border-radius:14px;
  padding:11px 24px;
}
.cover .title{
  font-family:var(--sans);
  font-weight:900;
  font-size:146px;
  line-height:1.02;
  letter-spacing:.03em;
  margin:0 0 6px;
  color:var(--ink);
}
.cover .rule{
  height:8px;
  background:var(--seal);
  width:180px;
  margin:40px 0 56px;
}
.cover .lead-label{
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.2em;
  color:var(--seal);
  margin-bottom:30px;
  text-transform:uppercase;
}
.cover .hl{
  display:flex;
  gap:26px;
  align-items:flex-start;
  padding:26px 0;
  border-top:1.5px solid var(--rule);
}
.cover .hl:last-of-type{
  border-bottom:1.5px solid var(--rule);
}
.cover .hl .no{
  font-family:var(--disp);
  font-weight:900;
  font-size:52px;
  line-height:1;
  color:var(--seal);
  flex:none;
  width:74px;
}
.cover .hl .t{
  font-family:var(--sans);
  font-weight:700;
  font-size:40px;
  line-height:1.28;
  color:var(--ink);
}
.cover .swipe{
  position:absolute;
  left:0;
  right:0;
  bottom:54px;
  display:flex;
  align-items:center;
  justify-content:center;
  gap:16px;
  font-family:var(--mono);
  font-weight:700;
  font-size:24px;
  letter-spacing:.2em;
  color:var(--seal);
  text-transform:uppercase;
}
.cover .swipe svg{
  color:var(--seal);
}
.cover2{
  display:flex;
  flex-direction:column;
}
.cv2-head{
  flex:none;
  display:flex;
  justify-content:space-between;
  align-items:center;
  padding:64px 80px 30px;
  border-bottom:3px solid var(--rule-2);
}
.cv2-brand{
  display:flex;
  align-items:center;
  gap:18px;
}
.cv2-brand .seal-mark{
  width:26px;
  height:26px;
  background:var(--seal);
  flex:none;
}
.cv2-brand .name{
  font-family:var(--sans);
  font-weight:900;
  font-size:36px;
  letter-spacing:.03em;
  color:var(--ink-2);
}
.cv2-date{
  display:flex;
  align-items:baseline;
  gap:16px;
}
.cv2-date .d{
  font-family:var(--mono);
  font-weight:700;
  font-size:40px;
  letter-spacing:.02em;
  color:var(--seal);
  line-height:1;
}
.cv2-date .dow{
  font-family:var(--sans);
  font-weight:900;
  font-size:28px;
  color:var(--ink-3);
  line-height:1;
}
.cv2-hook{
  flex:1 1 auto;
  display:flex;
  flex-direction:column;
  justify-content:center;
  padding:0 80px;
  min-height:0;
}
.cv2-kicker{
  align-self:flex-start;
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.14em;
  color:var(--paper);
  background:var(--seal);
  padding:11px 22px;
  border-radius:6px;
  margin-bottom:46px;
  text-transform:uppercase;
}
.cv2-big{
  font-family:var(--sans);
  font-weight:900;
  line-height:1.14;
  letter-spacing:.005em;
  color:var(--ink);
  margin:0;
}
.cv2-big em{
  color:var(--seal);
  font-style:normal;
}
.cv2-big.l{
  font-size:116px;
}
.cv2-big.m{
  font-size:94px;
}
.cv2-sub{
  margin:36px 0 0;
  font-family:var(--sans);
  font-weight:500;
  font-size:40px;
  line-height:1.42;
  color:var(--ink-3);
}
.cv2-sub em{
  color:var(--seal);
  font-style:normal;
  font-weight:700;
}
.cv2-foot{
  flex:none;
  padding:30px 80px 64px;
  border-top:3px solid var(--rule-2);
}
.cv2-foot .bar{
  height:6px;
  width:120px;
  background:var(--seal);
  margin-bottom:24px;
}
.cv2-foot .row{
  display:flex;
  justify-content:space-between;
  align-items:center;
  font-family:var(--mono);
  font-weight:700;
  font-size:24px;
  letter-spacing:.08em;
  color:var(--muted);
}
.cv2-foot .more{
  color:var(--ink-3);
}
.cv2-foot .swipe{
  display:flex;
  align-items:center;
  gap:14px;
  color:var(--seal);
  text-transform:uppercase;
}
.cv2-foot .swipe svg{
  color:var(--seal);
}
.coverv2{
  display:flex;
  flex-direction:column;
}
.coverv2 .cv2-head{
  position:relative;
  z-index:2;
  padding:56px 84px 28px;
}
.hook3{
  position:relative;
  z-index:2;
  flex:1 1 auto;
  display:flex;
  flex-direction:column;
  justify-content:center;
  padding:30px 84px;
  min-height:0;
}
.eyebrow{
  align-self:flex-start;
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.16em;
  color:var(--seal);
  margin-bottom:28px;
  text-transform:uppercase;
}
.title3{
  font-family:var(--sans);
  font-weight:900;
  font-size:100px;
  line-height:1.16;
  letter-spacing:.008em;
  color:var(--ink);
  margin:0;
}
.mark{
  color:var(--seal);
  font-style:normal;
  font-weight:inherit;
  text-decoration:underline;
  text-decoration-color:var(--seal);
  text-decoration-thickness:11px;
  text-underline-offset:10px;
  text-decoration-skip-ink:none;
}
.sub3{
  margin:34px 0 0;
  font-family:var(--sans);
  font-weight:500;
  font-size:40px;
  line-height:1.44;
  color:var(--ink-3);
}
.foot3{
  position:relative;
  z-index:2;
  flex:none;
  padding:26px 84px 56px;
  border-top:3px solid var(--rule-2);
}
.foot3 .row{
  display:flex;
  justify-content:space-between;
  align-items:center;
  font-family:var(--mono);
  font-weight:700;
  font-size:22px;
  letter-spacing:.06em;
  color:var(--muted);
}
.foot3 .swipe{
  display:flex;
  align-items:center;
  gap:14px;
  color:var(--seal);
  text-transform:uppercase;
}
.foot3 .swipe svg{
  color:var(--seal);
}
.headline{
  padding:58px 72px 86px;
  display:flex;
  flex-direction:column;
}
.headline .topbar{
  flex:none;
}
.hl-body{
  flex:1 1 auto;
  display:flex;
  flex-direction:column;
  justify-content:center;
  min-height:0;
}
.headline .kicker{
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.16em;
  color:var(--seal);
  margin:0 0 18px;
  text-transform:uppercase;
}
.headline h2{
  font-family:var(--sans);
  font-weight:900;
  font-size:56px;
  line-height:1.18;
  letter-spacing:.01em;
  margin:0 0 14px;
  color:var(--ink);
}
.fact{
  margin-top:32px;
}
.fact .label{
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.13em;
  color:var(--ink-3);
  margin-bottom:16px;
}
.fact p{
  margin:0;
  font-family:var(--sans);
  font-weight:500;
  font-size:36px;
  line-height:1.5;
  color:var(--ink-2);
}
.impact{
  position:relative;
  margin-top:40px;
  padding:30px 36px 34px 40px;
  background:var(--ai-bg);
  border-left:8px solid var(--seal);
}
.impact .label{
  display:flex;
  align-items:center;
  gap:12px;
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.1em;
  color:var(--seal);
  margin-bottom:16px;
}
.impact .label .chip{
  font-size:19px;
  letter-spacing:.08em;
  color:var(--paper);
  background:var(--seal);
  border-radius:5px;
  padding:3px 10px;
}
.impact p{
  margin:0;
  font-family:var(--sans);
  font-weight:500;
  font-size:34px;
  line-height:1.52;
  color:var(--ink-3);
}
.headline .src{
  margin-top:34px;
  font-family:var(--mono);
  font-size:26px;
  letter-spacing:.06em;
  color:var(--muted);
}
.briefs{
  padding:60px 72px 86px;
}
.briefs h2{
  font-family:var(--sans);
  font-weight:900;
  font-size:66px;
  line-height:1;
  margin:26px 0 24px;
  color:var(--ink);
}
.brief-list{
  border-top:3px solid var(--ink);
  border-bottom:3px solid var(--ink);
}
.brief-item{
  display:grid;
  grid-template-columns:64px 1fr;
  gap:22px;
  padding:22px 0;
  border-bottom:1px solid var(--rule);
  break-inside:avoid;
}
.brief-item:last-child{
  border-bottom:0;
}
.brief-item .no{
  font-family:var(--disp);
  font-weight:900;
  font-size:42px;
  line-height:1;
  color:var(--seal);
}
.brief-item h3{
  margin:0;
  font-family:var(--sans);
  font-weight:700;
  font-size:38px;
  line-height:1.22;
  color:var(--ink);
}
.brief-item p{
  margin:10px 0 8px;
  font-family:var(--sans);
  font-weight:500;
  font-size:31px;
  line-height:1.4;
  color:var(--ink-2);
}
.brief-item .src{
  font-family:var(--mono);
  font-size:20px;
  letter-spacing:.05em;
  color:var(--muted);
}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tarfile
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from daily_news.ai_engine import (
    AIEngineError,
    ProviderName,
    build_digest_file_prompt,
    build_issue_file_prompt,
    build_issue_hybrid_edit_prompt,
    build_issue_humanize_prompt,
    build_selection_file_prompt,
    build_shortlist_file_prompt,
    extract_json_object,
    run_provider,
    run_ai_task,
)
from daily_news.config import PipelineConfig, load_config, load_pipeline_config, load_section
from daily_news.fetch.rss import enrich_candidate_content, fetch_section_items
from daily_news.models import (
    AIIssueOutput,
    AIRunRecord,
    CandidateItem,
    CodexSelectionOutput,
    CodexShortlistOutput,
    DigestFeedbackOutput,
    Issue,
    RawItem,
)
from daily_news.paths import DIST_DIR, DIST_OWNER_DIR, WEB_DIR
from daily_news.render import build_frontend_app
from daily_news.scoring import rank_candidates
from daily_news.storage.local import (
    ai_logs_dir,
    artifact_path,
    logs_dir,
    load_issue,
    load_codex_shortlist,
    load_enriched_candidates,
    load_issue_from_run,
    load_issue_draft_from_run,
    load_profiles,
    load_recent_issue_history,
    load_recent_issue_selection_index,
    load_raw_items,
    load_selection,
    load_shortlist,
    run_dir,
    save_ai_task_run,
    save_candidates,
    save_codex_shortlist,
    save_enriched_candidates,
    save_issue,
    save_issue_draft,
    save_selection,
    save_selection_history_index,
    save_raw_items,
    snapshot_profiles,
    output_dir,
    write_profiles,
)
from daily_news.storage.supabase import SupabaseStore
from daily_news.xhs_export import (
    XHSExportAIError,
    XHSExportConfigurationError,
    export_xhs_issue,
    load_issue_for_xhs,
)
from daily_news.zh_editor import (
    HumanizeValidationReport,
    build_blind_mapping,
    build_blind_review,
    guarded_hybrid_output,
    guarded_humanized_output,
    issue_to_ai_output,
    validate_variant_against_sources,
    with_ai_output,
    write_json,
)


WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PipelineStage = Literal[
    "fetch",
    "local_shortlist",
    "ai_shortlist",
    "enrich",
    "ai_select",
    "ai_compose",
    "ai_humanize",
    "publish_frontend",
]
AIStage = Literal["ai_shortlist", "ai_select", "ai_compose", "ai_humanize"]
AITaskType = Literal["semantic_shortlist", "selection", "issue_compose", "issue_humanize", "digest_feedback"]
AI_STAGE_TASKS: dict[AIStage, AITaskType] = {
    "ai_shortlist": "semantic_shortlist",
    "ai_select": "selection",
    "ai_compose": "issue_compose",
    "ai_humanize": "issue_humanize",
}
PIPELINE_STAGES: list[PipelineStage] = [
    "fetch",
    "local_shortlist",
    "ai_shortlist",
    "enrich",
    "ai_select",
    "ai_compose",
    "ai_humanize",
    "publish_frontend",
]


@contextmanager
def temporary_feedback_mode(mode: Literal["reader", "owner"]):
    previous = os.environ.get("FEEDBACK_MODE")
    os.environ["FEEDBACK_MODE"] = mode
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("FEEDBACK_MODE", None)
        else:
            os.environ["FEEDBACK_MODE"] = previous


def date_cn(value: date) -> str:
    return f"{value.year} 年 {value.month} 月 {value.day} 日 · {WEEKDAYS_CN[value.weekday()]}"


def parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    return date.fromisoformat(value)


def next_issue_number(section_slug: str) -> int:
    issues_dir = DIST_DIR / "issues"
    if not issues_dir.exists():
        return 1
    return len(sorted(issues_dir.glob("*.html"))) + 1


def make_issue(
    output: AIIssueOutput,
    *,
    section_slug: str,
    publication_name: str,
    issue_date: date,
    volume: int,
    number: int,
) -> Issue:
    issue_id = f"{section_slug}-{issue_date.isoformat()}"
    output_path = f"issues/{issue_date.isoformat()}.html"
    return Issue(
        id=issue_id,
        section_slug=section_slug,
        publication_name=publication_name,
        issue_date=issue_date,
        volume=volume,
        number=number,
        date_cn=date_cn(issue_date),
        output_path=output_path,
        headlines=output.headlines,
        briefs=output.briefs,
        discarded=output.discarded,
        merged_sources=output.merged_sources,
    )


def log_step(step: str, message: str) -> None:
    print(f"[{step}] {message}", flush=True)


def new_run_id(section_slug: str, issue_date: date) -> str:
    return f"{section_slug}-{issue_date.isoformat()}-{datetime.now().strftime('%H%M%S')}"


def resolve_stage_provider(
    config: PipelineConfig,
    task_type: AITaskType,
    cli_provider: ProviderName | None = None,
) -> ProviderName:
    return cli_provider or config.ai.stage_providers.get(task_type) or config.ai.default_provider


def date_from_run_id(run_id: str, section_slug: str) -> date:
    match = re.match(rf"^{re.escape(section_slug)}-(\d{{4}}-\d{{2}}-\d{{2}})-", run_id)
    if not match:
        raise ValueError("Cannot infer date from run_id; pass --date YYYY-MM-DD")
    return date.fromisoformat(match.group(1))


def summarize_raw_items(raw_items: list[RawItem]) -> None:
    by_source: dict[str, int] = {}
    failures: list[RawItem] = []
    for item in raw_items:
        if item.fetch_status == "failed":
            failures.append(item)
            continue
        by_source[item.source_name] = by_source.get(item.source_name, 0) + 1
    print("抓取摘要：")
    print(f"- 有效新闻：{sum(by_source.values())} 条")
    print(f"- 成功源：{len(by_source)} 个")
    for source, count in sorted(by_source.items()):
        print(f"  - {source}: {count} 条")
    if failures:
        print(f"- 失败源/失败项：{len(failures)}")
        for item in failures[:5]:
            print(f"  - {item.source_name}: {item.error or item.title}")


def summarize_candidates(candidates: list[CandidateItem], *, limit: int = 10) -> None:
    print(f"本地预筛摘要：{len(candidates)} 条")
    for index, candidate in enumerate(candidates[:limit], start=1):
        item = candidate.raw_item
        print(f"{index:02d}. [{candidate.score:.1f}] {item.title}")
        print(f"    来源：{item.source_name}；原因：{candidate.reason}")


def build_prefilter_history(
    *,
    section_slug: str,
    issue_date: date,
    config: PipelineConfig,
) -> Any:
    return load_recent_issue_history(
        section_slug=section_slug,
        before_date=issue_date,
        lookback_days=config.dedupe.history_lookback_days,
        include_title_hashes=config.dedupe.title_hash_enabled,
    )


def summarize_prefilter_history(history: Any, *, lookback_days: int, title_hash_enabled: bool) -> None:
    print(
        "历史去重："
        f"窗口 {lookback_days} 天；"
        f"历史期 {len(history.issue_ids)} 个；"
        f"URL {len(history.urls)} 个；"
        f"标题 hash {len(history.title_hashes) if title_hash_enabled else 0} 个"
    )


def build_selection_history_index(*, section_slug: str, issue_date: date, config: PipelineConfig) -> list[dict[str, Any]]:
    if not config.selection_history.enabled:
        return []
    return load_recent_issue_selection_index(
        section_slug=section_slug,
        before_date=issue_date,
        lookback_days=config.selection_history.lookback_days,
        max_items=config.selection_history.max_items,
    )


def summarize_codex_shortlist(shortlist: CodexShortlistOutput) -> None:
    print("Codex/AI 粗筛摘要：")
    print(f"- 保留：{len(shortlist.keep_item_ids)}")
    print(f"- 备选：{len(shortlist.maybe_item_ids)}")
    print(f"- 丢弃：{len(shortlist.drop_item_ids)}")
    for index, item in enumerate(shortlist.items[:15], start=1):
        print(
            f"{index:02d}. {item.decision.upper()} "
            f"R={item.relevance_score} I={item.importance_score} "
            f"id={item.source_item_id}；{item.reason}"
        )


def summarize_enriched(candidates: list[CandidateItem]) -> None:
    with_content = [candidate for candidate in candidates if candidate.raw_item.content]
    failed = [candidate for candidate in candidates if candidate.raw_item.fetch_status == "failed"]
    print("正文补全摘要：")
    print(f"- 候选总数：{len(candidates)}")
    print(f"- 有正文：{len(with_content)}")
    print(f"- 正文失败：{len(failed)}")
    for candidate in failed[:5]:
        print(f"  - {candidate.raw_item.source_name}: {candidate.raw_item.error or candidate.raw_item.title}")


def summarize_selection(selection: CodexSelectionOutput) -> None:
    print("Codex 选题摘要：")
    print(f"- 头条候选：{len(selection.headlines)} 条，来源 {len(selection.headline_item_ids)} 个")
    for index, item in enumerate(selection.headlines, start=1):
        print(f"  H{index}: ids={','.join(item.source_item_ids)} R={item.relevance_score} I={item.importance_score}；{item.reason}")
    print(f"- 速览候选：{len(selection.briefs)} 条，来源 {len(selection.brief_item_ids)} 个")
    for index, item in enumerate(selection.briefs, start=1):
        print(f"  B{index}: ids={','.join(item.source_item_ids)} R={item.relevance_score} I={item.importance_score}；{item.reason}")
    print(f"- 丢弃：{len(selection.discarded)}")


def summarize_issue(issue: Issue) -> None:
    print("日报结构摘要：")
    print(f"- 头条：{len(issue.headlines)}")
    for index, article in enumerate(issue.headlines, start=1):
        print(f"  H{index}: {article.title_zh}")
    print(f"- 速览：{len(issue.briefs)}")
    for index, article in enumerate(issue.briefs[:10], start=1):
        print(f"  B{index}: {article.title_zh}")


def validate_selection_ids(selection: CodexSelectionOutput, candidates: list[CandidateItem]) -> None:
    candidate_ids = {candidate.raw_item.id for candidate in candidates}
    selected_ids = set(selection.headline_item_ids + selection.brief_item_ids)
    nested_ids: set[str] = set()
    for item in selection.headlines + selection.briefs:
        nested_ids.update(item.source_item_ids)
    missing = (selected_ids | nested_ids) - candidate_ids
    if missing:
        raise ValueError(f"Selection references unknown candidate ids: {', '.join(sorted(missing))}")


def validate_shortlist_ids(shortlist: CodexShortlistOutput, candidates: list[CandidateItem]) -> None:
    candidate_ids = {candidate.raw_item.id for candidate in candidates}
    top_level_ids = set(shortlist.keep_item_ids + shortlist.maybe_item_ids + shortlist.drop_item_ids)
    item_ids = {item.source_item_id for item in shortlist.items}
    missing = (top_level_ids | item_ids) - candidate_ids
    if missing:
        raise ValueError(f"Codex shortlist references unknown candidate ids: {', '.join(sorted(missing))}")
    missing_candidate_items = candidate_ids - item_ids
    if missing_candidate_items:
        raise ValueError(f"Codex shortlist ids are inconsistent: missing from items: {', '.join(sorted(missing_candidate_items))}")
    if top_level_ids != item_ids:
        missing_from_top = item_ids - top_level_ids
        missing_from_items = top_level_ids - item_ids
        details: list[str] = []
        if missing_from_top:
            details.append(f"missing from top-level lists: {', '.join(sorted(missing_from_top))}")
        if missing_from_items:
            details.append(f"missing from items: {', '.join(sorted(missing_from_items))}")
        raise ValueError("Codex shortlist ids are inconsistent: " + "; ".join(details))

    item_decisions = {item.source_item_id: item.decision for item in shortlist.items}
    for item_id in shortlist.keep_item_ids:
        if item_decisions.get(item_id) != "keep":
            raise ValueError(f"keep_item_ids contains id not marked keep: {item_id}")
    for item_id in shortlist.maybe_item_ids:
        if item_decisions.get(item_id) != "maybe":
            raise ValueError(f"maybe_item_ids contains id not marked maybe: {item_id}")
    for item_id in shortlist.drop_item_ids:
        if item_decisions.get(item_id) != "drop":
            raise ValueError(f"drop_item_ids contains id not marked drop: {item_id}")


def normalize_shortlist_top_level_ids(shortlist: CodexShortlistOutput) -> CodexShortlistOutput:
    keep_item_ids: list[str] = []
    maybe_item_ids: list[str] = []
    drop_item_ids: list[str] = []
    for item in shortlist.items:
        if item.decision == "keep":
            keep_item_ids.append(item.source_item_id)
        elif item.decision == "maybe":
            maybe_item_ids.append(item.source_item_id)
        elif item.decision == "drop":
            drop_item_ids.append(item.source_item_id)
    return shortlist.model_copy(
        update={
            "keep_item_ids": keep_item_ids,
            "maybe_item_ids": maybe_item_ids,
            "drop_item_ids": drop_item_ids,
        }
    )


def candidates_for_enrichment(run_id: str, local_prefilter: list[CandidateItem]) -> list[CandidateItem]:
    try:
        codex_shortlist = load_codex_shortlist(run_id)
    except FileNotFoundError:
        print("未找到 02_codex_shortlist.json，临时回退为本地预筛结果。")
        return local_prefilter

    validate_shortlist_ids(codex_shortlist, local_prefilter)
    candidate_by_id = {candidate.raw_item.id: candidate for candidate in local_prefilter}
    selected_ids = codex_shortlist.keep_item_ids + codex_shortlist.maybe_item_ids
    return [candidate_by_id[item_id] for item_id in selected_ids if item_id in candidate_by_id]


def merge_enriched_candidates(
    original_candidates: list[CandidateItem],
    enriched_items: list[RawItem],
) -> list[CandidateItem]:
    enriched_by_id = {item.id: item for item in enriched_items}
    return [
        candidate.model_copy(update={"raw_item": enriched_by_id.get(candidate.raw_item.id, candidate.raw_item)})
        for candidate in original_candidates
    ]


def validate_issue_content(issue: Issue) -> None:
    if not issue.headlines:
        raise ValueError("Issue must include at least one headline")
    if not issue.briefs:
        raise ValueError("Issue must include at least one brief")
    for article in issue.headlines:
        if not article.read_body_zh:
            raise ValueError(f"Headline missing read_body_zh: {article.title_zh}")
        if not article.ai_impact:
            raise ValueError(f"Headline missing ai_impact: {article.title_zh}")
    # BriefArticle has no read_body_zh field by model, which enforces the v1 rule.


def save_ai_debug(run_id: str, stage: str, ai_run: AIRunRecord, config: PipelineConfig) -> Path:
    return save_ai_task_run(
        run_id,
        stage,
        ai_run,
        save_attempts=config.logging.save_attempts,
        save_provider_events=config.logging.save_provider_events,
        append_metrics_jsonl=config.logging.append_metrics_jsonl,
    )


def mark_ai_run_failed(ai_run: AIRunRecord, error: Exception) -> AIRunRecord:
    finished_at = datetime.now(timezone.utc)
    return ai_run.model_copy(
        update={
            "status": "failed",
            "error": str(error),
            "finished_at": finished_at,
            "duration_ms": int((finished_at - ai_run.started_at).total_seconds() * 1000),
        }
    )


def resolve_provider(args: argparse.Namespace) -> ProviderName:
    pipeline_config = load_pipeline_config(Path(args.config) if args.config else None)
    return args.provider or pipeline_config.ai.default_provider


async def fetch_mvp(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    section = load_section(args.section)
    issue_date = parse_date(args.date)
    run_id = args.run_id or new_run_id(section.slug, issue_date)
    timeout_seconds = float(os.getenv("DAILY_NEWS_FETCH_TIMEOUT_SECONDS", "20"))
    print(f"Run ID: {run_id}")
    raw_items = await fetch_section_items(
        section,
        per_source_limit=args.per_source_limit,
        timeout_seconds=timeout_seconds,
    )
    path = save_raw_items(run_id, raw_items)
    print(f"Saved: {path}")
    summarize_raw_items(raw_items)
    return 0


def shortlist_mvp(args: argparse.Namespace) -> int:
    section = load_section(args.section)
    config = load_pipeline_config(Path(args.config) if args.config else None)
    issue_date = date_from_run_id(args.run_id, section.slug)
    history = build_prefilter_history(section_slug=section.slug, issue_date=issue_date, config=config)
    raw_items = load_raw_items(args.run_id)
    summarize_prefilter_history(
        history,
        lookback_days=config.dedupe.history_lookback_days,
        title_hash_enabled=config.dedupe.title_hash_enabled,
    )
    candidates = rank_candidates(
        raw_items,
        section,
        max_candidates=args.max_candidates,
        per_source_limit=args.per_source_limit,
        require_interest_match_when_over_capacity=False,
        historical_urls=history.urls,
        historical_title_hashes=history.title_hashes if config.dedupe.title_hash_enabled else None,
    )
    path = save_candidates(args.run_id, candidates)
    print(f"Saved: {path}")
    summarize_candidates(candidates)
    return 0


def shortlist_codex(args: argparse.Namespace) -> int:
    local_prefilter = load_shortlist(args.run_id)
    codex_shortlist = normalize_shortlist_top_level_ids(load_codex_shortlist(args.run_id))
    validate_shortlist_ids(codex_shortlist, local_prefilter)
    summarize_codex_shortlist(codex_shortlist)
    print(f"Validated: {artifact_path(args.run_id, '02_codex_shortlist.json')}")
    return 0


async def enrich_mvp(args: argparse.Namespace) -> int:
    timeout_seconds = float(os.getenv("DAILY_NEWS_FETCH_TIMEOUT_SECONDS", "20"))
    local_prefilter = load_shortlist(args.run_id)
    shortlist = candidates_for_enrichment(args.run_id, local_prefilter)
    body_candidates = args.body_candidates or len(shortlist)
    enriched_items = await enrich_candidate_content(
        [candidate.raw_item for candidate in shortlist],
        limit=body_candidates,
        timeout_seconds=timeout_seconds,
    )
    enriched_candidates = merge_enriched_candidates(shortlist, enriched_items)
    path = save_enriched_candidates(args.run_id, enriched_candidates)
    print(f"Saved: {path}")
    summarize_enriched(enriched_candidates)
    return 0


def select_codex(args: argparse.Namespace) -> int:
    candidates = load_enriched_candidates(args.run_id)
    selection = load_selection(args.run_id)
    validate_selection_ids(selection, candidates)
    summarize_selection(selection)
    print(f"Validated: {artifact_path(args.run_id, '04_selection.json')}")
    return 0


def compose_codex(args: argparse.Namespace) -> int:
    selection = load_selection(args.run_id)
    issue = load_issue_from_run(args.run_id)
    validate_selection_ids(selection, load_enriched_candidates(args.run_id))
    validate_issue_content(issue)
    summarize_issue(issue)
    print(f"Validated: {artifact_path(args.run_id, '05_issue.json')}")
    return 0


def run_ai_shortlist_stage(
    *,
    run_id: str,
    section: Any,
    config: PipelineConfig,
    provider: ProviderName,
) -> tuple[CodexShortlistOutput, Path, Path]:
    candidates = load_shortlist(run_id)
    candidates_path = artifact_path(run_id, "02_candidates.json").resolve()
    taste_profile_path = WEB_DIR / "profiles" / section.slug / "taste.md"
    prompt = build_shortlist_file_prompt(
        section,
        candidates_path,
        taste_profile_path=taste_profile_path if taste_profile_path.exists() else None,
    )
    try:
        shortlist, ai_run = run_ai_task(
            task_type="semantic_shortlist",
            prompt=prompt,
            output_model=CodexShortlistOutput,
            provider=provider,
            config=config,
            use_output_schema=False,
        )
    except AIEngineError as exc:
        if exc.record:
            debug_path = save_ai_debug(run_id, "02_ai_shortlist", exc.record, config)
            print(f"Debug: {debug_path}")
        raise
    try:
        shortlist = normalize_shortlist_top_level_ids(shortlist)
        validate_shortlist_ids(shortlist, candidates)
    except Exception as exc:
        failed_run = mark_ai_run_failed(ai_run, exc)
        debug_path = save_ai_debug(run_id, "02_ai_shortlist", failed_run, config)
        print(f"Debug: {debug_path}")
        raise
    saved_output_path = save_codex_shortlist(run_id, shortlist)
    debug_path = save_ai_debug(run_id, "02_ai_shortlist", ai_run, config)
    return shortlist, saved_output_path, debug_path


def ai_shortlist(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    section = load_section(args.section)
    config = load_pipeline_config(Path(args.config) if args.config else None)
    provider = args.provider or config.ai.default_provider
    shortlist, saved_output_path, debug_path = run_ai_shortlist_stage(
        run_id=args.run_id,
        section=section,
        config=config,
        provider=provider,
    )
    print(f"Provider: {provider}")
    print(f"Saved: {saved_output_path}")
    print(f"Debug: {debug_path}")
    summarize_codex_shortlist(shortlist)
    return 0


def run_ai_select_stage(
    *,
    run_id: str,
    section: Any,
    config: PipelineConfig,
    provider: ProviderName,
) -> tuple[CodexSelectionOutput, Path, Path]:
    candidates = load_enriched_candidates(run_id)
    enriched_candidates_path = artifact_path(run_id, "03_enriched_candidates.json").resolve()
    issue_date = date_from_run_id(run_id, section.slug)
    history_index = build_selection_history_index(section_slug=section.slug, issue_date=issue_date, config=config)
    history_index_path: Path | None = None
    if config.selection_history.enabled:
        history_index_path = save_selection_history_index(run_id, history_index).resolve()
    taste_profile_path = WEB_DIR / "profiles" / section.slug / "taste.md"
    prompt = build_selection_file_prompt(
        section,
        enriched_candidates_path,
        history_index_path=history_index_path,
        taste_profile_path=taste_profile_path if taste_profile_path.exists() else None,
    )
    try:
        selection, ai_run = run_ai_task(
            task_type="selection",
            prompt=prompt,
            output_model=CodexSelectionOutput,
            provider=provider,
            config=config,
            use_output_schema=False,
        )
    except AIEngineError as exc:
        if exc.record:
            debug_path = save_ai_debug(run_id, "04_ai_selection", exc.record, config)
            print(f"Debug: {debug_path}")
        raise
    try:
        validate_selection_ids(selection, candidates)
    except Exception as exc:
        failed_run = mark_ai_run_failed(ai_run, exc)
        debug_path = save_ai_debug(run_id, "04_ai_selection", failed_run, config)
        print(f"Debug: {debug_path}")
        raise
    saved_output_path = save_selection(run_id, selection)
    debug_path = save_ai_debug(run_id, "04_ai_selection", ai_run, config)
    return selection, saved_output_path, debug_path


def ai_select(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    section = load_section(args.section)
    config = load_pipeline_config(Path(args.config) if args.config else None)
    provider = args.provider or config.ai.default_provider
    selection, saved_output_path, debug_path = run_ai_select_stage(
        run_id=args.run_id,
        section=section,
        config=config,
        provider=provider,
    )
    print(f"Provider: {provider}")
    print(f"Saved: {saved_output_path}")
    print(f"Debug: {debug_path}")
    summarize_selection(selection)
    return 0


def run_ai_compose_stage(
    *,
    run_id: str,
    section: Any,
    issue_date: date,
    issue_number: int | None,
    config: PipelineConfig,
    provider: ProviderName,
    save_as_draft: bool = False,
) -> tuple[Issue, Path, Path]:
    candidates = load_enriched_candidates(run_id)
    selection = load_selection(run_id)
    validate_selection_ids(selection, candidates)
    selection_path = artifact_path(run_id, "04_selection.json").resolve()
    enriched_candidates_path = artifact_path(run_id, "03_enriched_candidates.json").resolve()
    style_profile_path = WEB_DIR / "profiles" / section.slug / "style.md"
    prompt = build_issue_file_prompt(
        section,
        selection_path,
        enriched_candidates_path,
        style_profile_path=style_profile_path if style_profile_path.exists() else None,
    )
    try:
        ai_output, ai_run = run_ai_task(
            task_type="issue_compose",
            prompt=prompt,
            output_model=AIIssueOutput,
            provider=provider,
            config=config,
            use_output_schema=False,
        )
    except AIEngineError as exc:
        if exc.record:
            debug_path = save_ai_debug(run_id, "05_ai_issue", exc.record, config)
            print(f"Debug: {debug_path}")
        raise
    issue = make_issue(
        ai_output,
        section_slug=section.slug,
        publication_name=section.publication_name,
        issue_date=issue_date,
        volume=section.issue_volume,
        number=issue_number or next_issue_number(section.slug),
    )
    try:
        validate_issue_content(issue)
    except Exception as exc:
        failed_run = mark_ai_run_failed(ai_run, exc)
        debug_path = save_ai_debug(run_id, "05_ai_issue", failed_run, config)
        print(f"Debug: {debug_path}")
        raise
    saved_output_path = save_issue_draft(run_id, issue) if save_as_draft else save_issue(run_id, issue)
    debug_path = save_ai_debug(run_id, "05_ai_issue", ai_run, config)
    return issue, saved_output_path, debug_path


def ai_compose(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    section = load_section(args.section)
    issue_date = parse_date(args.date) if args.date else date_from_run_id(args.run_id, section.slug)
    config = load_pipeline_config(Path(args.config) if args.config else None)
    provider = args.provider or config.ai.default_provider
    issue, saved_output_path, debug_path = run_ai_compose_stage(
        run_id=args.run_id,
        section=section,
        issue_date=issue_date,
        issue_number=args.issue_number,
        config=config,
        provider=provider,
    )
    print(f"Provider: {provider}")
    print(f"Saved: {saved_output_path}")
    print(f"Debug: {debug_path}")
    summarize_issue(issue)
    return 0


def run_ai_humanize_stage(
    *,
    run_id: str,
    config: PipelineConfig,
    provider: ProviderName,
) -> tuple[Issue, list[Path], HumanizeValidationReport]:
    draft_path = artifact_path(run_id, "05_issue_draft.json")
    if draft_path.exists():
        draft = load_issue_draft_from_run(run_id)
    else:
        # Compatibility for a run created before the draft/final split.
        draft_path = artifact_path(run_id, "05_issue.json")
        draft = load_issue_from_run(run_id)
        save_issue_draft(run_id, draft)
        draft_path = artifact_path(run_id, "05_issue_draft.json")
    validate_issue_content(draft)
    candidates_path = artifact_path(run_id, "03_enriched_candidates.json")
    if not candidates_path.exists():
        raise FileNotFoundError(f"Enriched candidates not found: {candidates_path}")
    rules_path = WEB_DIR / "prompts" / "zh_news_editor.md"
    if not rules_path.exists():
        raise FileNotFoundError(f"Chinese editor rules not found: {rules_path}")
    prompt = build_issue_hybrid_edit_prompt(
        draft_path.resolve(),
        candidates_path.resolve(),
        rules_path.resolve(),
    )
    outputs: list[Path] = []
    try:
        candidate_output, ai_run = run_ai_task(
            task_type="issue_humanize",
            prompt=prompt,
            output_model=AIIssueOutput,
            provider=provider,
            config=config,
            use_output_schema=False,
        )
        candidate_path = write_json(
            output_dir(run_id) / "05_issue_humanize_candidate.json",
            candidate_output.model_dump(mode="json"),
        )
        final_output, report = guarded_hybrid_output(issue_to_ai_output(draft), candidate_output)
        debug_path = save_ai_debug(run_id, "05_ai_issue_humanize", ai_run, config)
        outputs.extend([candidate_path, debug_path])
    except AIEngineError as exc:
        final_output = issue_to_ai_output(draft)
        report = HumanizeValidationReport(
            valid=True,
            fallback_used=True,
            violations=[f"issue_humanize 调用失败，整期使用事实稿: {exc}"],
            checks={
                "final_output_valid": True,
                "fallback_articles": ["整期"],
                "ai_call": "failed",
                "per_article_fallback": False,
            },
        )
        if exc.record:
            outputs.append(save_ai_debug(run_id, "05_ai_issue_humanize", exc.record, config))

    final_issue = with_ai_output(draft, final_output)
    validate_issue_content(final_issue)
    saved_output_path = save_issue(run_id, final_issue)
    validation_path = write_json(
        output_dir(run_id) / "05_humanize_validation.json",
        report.to_dict(),
    )
    outputs.extend([saved_output_path, validation_path])
    return final_issue, outputs, report


def ai_humanize(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    config = load_pipeline_config(Path(args.config) if args.config else None)
    provider = resolve_stage_provider(config, "issue_humanize", args.provider)
    issue, outputs, report = run_ai_humanize_stage(
        run_id=args.run_id,
        config=config,
        provider=provider,
    )
    print(f"Provider: {provider}")
    print(f"Fallback articles: {report.checks.get('fallback_articles', [])}")
    for path in outputs:
        print(f"Saved: {path}")
    summarize_issue(issue)
    return 0


def zh_editor_eval(args: argparse.Namespace) -> int:
    """Generate private A/B/C Chinese-editing variants without touching publication artifacts."""
    load_dotenv(WEB_DIR / ".env")
    section = load_section(args.section)
    config = load_pipeline_config(Path(args.config) if args.config else None)
    baseline_path = artifact_path(args.run_id, "05_issue.json")
    baseline_bytes = baseline_path.read_bytes()
    baseline = load_issue_from_run(args.run_id)
    validate_issue_content(baseline)
    selection = load_selection(args.run_id)
    candidates = load_enriched_candidates(args.run_id)
    validate_selection_ids(selection, candidates)

    rules_path = (WEB_DIR / "prompts" / "zh_news_editor.md").resolve()
    if not rules_path.exists():
        raise FileNotFoundError(f"Chinese editor rules not found: {rules_path}")

    eval_dir = run_dir(args.run_id) / "zh-editor-eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    baseline_output = issue_to_ai_output(baseline)
    variant_a_path = write_json(eval_dir / "variant-a-baseline.json", baseline.model_dump(mode="json"))

    compose_provider = resolve_stage_provider(config, "issue_compose", args.provider)
    compose_prompt = build_issue_file_prompt(
        section,
        artifact_path(args.run_id, "04_selection.json").resolve(),
        artifact_path(args.run_id, "03_enriched_candidates.json").resolve(),
        style_profile_path=(WEB_DIR / "profiles" / section.slug / "style.md"),
        chinese_editor_rules_path=rules_path,
    )
    variant_b_output, variant_b_run = run_ai_task(
        task_type="issue_compose",
        prompt=compose_prompt,
        output_model=AIIssueOutput,
        provider=compose_provider,
        config=config,
        use_output_schema=False,
    )
    variant_b = with_ai_output(baseline, variant_b_output)
    validate_issue_content(variant_b)
    variant_b_violations = validate_variant_against_sources(baseline, variant_b, candidates)
    if variant_b_violations:
        failed_run = mark_ai_run_failed(variant_b_run, ValueError("; ".join(variant_b_violations)))
        save_ai_debug(args.run_id, "05b_ai_issue_zh_rules", failed_run, config)
        raise ValueError("B variant changed the selected article structure: " + "; ".join(variant_b_violations))
    variant_b_path = write_json(eval_dir / "variant-b-compose.json", variant_b.model_dump(mode="json"))
    variant_b_debug = save_ai_debug(args.run_id, "05b_ai_issue_zh_rules", variant_b_run, config)

    humanize_provider = resolve_stage_provider(config, "issue_humanize", args.provider)
    humanize_prompt = build_issue_humanize_prompt(variant_a_path.resolve(), rules_path)
    variant_c_candidate_path: Path | None = None
    variant_c_debug: Path | None = None
    variant_c_duration_ms: int | None = None
    try:
        variant_c_candidate, variant_c_run = run_ai_task(
            task_type="issue_humanize",
            prompt=humanize_prompt,
            output_model=AIIssueOutput,
            provider=humanize_provider,
            config=config,
            use_output_schema=False,
        )
        variant_c_candidate_path = write_json(
            eval_dir / "variant-c-humanize-candidate.json",
            variant_c_candidate.model_dump(mode="json"),
        )
        variant_c_output, humanize_report = guarded_humanized_output(baseline_output, variant_c_candidate)
        variant_c_duration_ms = variant_c_run.duration_ms
        variant_c_debug = save_ai_debug(args.run_id, "05c_ai_issue_humanize", variant_c_run, config)
    except AIEngineError as exc:
        if exc.record:
            variant_c_debug = save_ai_debug(args.run_id, "05c_ai_issue_humanize", exc.record, config)
        variant_c_output = baseline_output
        humanize_report = HumanizeValidationReport(
            valid=False,
            fallback_used=True,
            violations=[f"issue_humanize 调用失败: {exc}"],
            checks={"ai_call": "failed"},
        )

    variant_c = with_ai_output(baseline, variant_c_output)
    validate_issue_content(variant_c)
    variant_c_path = write_json(eval_dir / "variant-c-humanize.json", variant_c.model_dump(mode="json"))
    validation_path = write_json(
        eval_dir / "validation.json",
        {
            "variant_b": {
                "valid": True,
                "same_selection": True,
                "sources_numbers_entities_checked": True,
                "violations": [],
            },
            "variant_c": humanize_report.to_dict(),
        },
    )

    mapping = build_blind_mapping(args.run_id)
    blind_map_path = write_json(eval_dir / "blind-map.json", mapping)
    blind_review_path = eval_dir / "blind-review.md"
    blind_review_path.write_text(
        build_blind_review(
            {"A": baseline, "B": variant_b, "C": variant_c},
            mapping,
            headline_limit=args.headlines,
            brief_limit=args.briefs,
        ),
        encoding="utf-8",
    )
    manifest_path = write_json(
        eval_dir / "manifest.json",
        {
            "run_id": args.run_id,
            "source_issue": str(baseline_path.resolve()),
            "source_issue_unchanged": baseline_path.read_bytes() == baseline_bytes,
            "rules": str(rules_path),
            "providers": {"variant_b": compose_provider, "variant_c": humanize_provider},
            "duration_ms": {
                "variant_b": variant_b_run.duration_ms,
                "variant_c": variant_c_duration_ms,
                "total": (variant_b_run.duration_ms or 0) + (variant_c_duration_ms or 0),
                "within_ten_minutes": (
                    (variant_b_run.duration_ms or 0) + (variant_c_duration_ms or 0) <= 600_000
                ),
            },
            "sample": {"headlines": args.headlines, "briefs": args.briefs},
            "variant_c_valid": humanize_report.valid,
            "variant_c_fallback_used": humanize_report.fallback_used,
            "artifacts": {
                "variant_a": str(variant_a_path),
                "variant_b": str(variant_b_path),
                "variant_c_candidate": str(variant_c_candidate_path) if variant_c_candidate_path else None,
                "variant_c": str(variant_c_path),
                "validation": str(validation_path),
                "blind_review": str(blind_review_path),
                "blind_map": str(blind_map_path),
                "variant_b_debug": str(variant_b_debug),
                "variant_c_debug": str(variant_c_debug) if variant_c_debug else None,
            },
        },
    )
    if baseline_path.read_bytes() != baseline_bytes:
        raise RuntimeError("Offline evaluation unexpectedly modified the source 05_issue.json")

    print(f"B provider: {compose_provider}")
    print(f"C provider: {humanize_provider}")
    print(f"C guard: {'passed' if humanize_report.valid else 'failed; baseline fallback used'}")
    print(f"Blind review: {blind_review_path}")
    print(f"Validation: {validation_path}")
    print(f"Manifest: {manifest_path}")
    return 0


def zh_editor_hybrid_eval(args: argparse.Namespace) -> int:
    """Generate the A-grounded, B-style hybrid variant D without publishing."""
    load_dotenv(WEB_DIR / ".env")
    config = load_pipeline_config(Path(args.config) if args.config else None)
    baseline_path = artifact_path(args.run_id, "05_issue.json")
    baseline_bytes = baseline_path.read_bytes()
    baseline = load_issue_from_run(args.run_id)
    validate_issue_content(baseline)
    eval_dir = run_dir(args.run_id) / "zh-editor-eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    variant_a_path = eval_dir / "variant-a-baseline.json"
    if not variant_a_path.exists():
        write_json(variant_a_path, baseline.model_dump(mode="json"))
    candidates_path = artifact_path(args.run_id, "03_enriched_candidates.json").resolve()
    rules_path = (WEB_DIR / "prompts" / "zh_news_editor.md").resolve()
    prompt = build_issue_hybrid_edit_prompt(variant_a_path.resolve(), candidates_path, rules_path)
    provider = resolve_stage_provider(config, "issue_humanize", args.provider)
    try:
        candidate_output, ai_run = run_ai_task(
            task_type="issue_hybrid_edit",
            prompt=prompt,
            output_model=AIIssueOutput,
            provider=provider,
            config=config,
            use_output_schema=False,
        )
    except AIEngineError as exc:
        if exc.record:
            debug_path = save_ai_debug(args.run_id, "05e_ai_issue_hybrid", exc.record, config)
            print(f"Debug: {debug_path}")
        raise

    candidate_path = write_json(
        eval_dir / "variant-d-hybrid-candidate.json",
        candidate_output.model_dump(mode="json"),
    )
    final_output, report = guarded_hybrid_output(issue_to_ai_output(baseline), candidate_output)
    variant_d = with_ai_output(baseline, final_output)
    validate_issue_content(variant_d)
    variant_d_path = write_json(eval_dir / "variant-d-hybrid.json", variant_d.model_dump(mode="json"))
    validation_path = write_json(eval_dir / "variant-d-validation.json", report.to_dict())
    debug_path = save_ai_debug(args.run_id, "05e_ai_issue_hybrid", ai_run, config)

    comparison_variants = {"D": variant_d}
    comparison_mapping = {"混合 D": "D"}
    variant_b_path = eval_dir / "variant-b-compose.json"
    variant_c_path = eval_dir / "variant-c-humanize.json"
    if variant_b_path.exists():
        comparison_variants["B"] = Issue.model_validate_json(variant_b_path.read_text(encoding="utf-8"))
        comparison_mapping = {"B 自由重写": "B", **comparison_mapping}
    if variant_c_path.exists():
        comparison_variants["C"] = Issue.model_validate_json(variant_c_path.read_text(encoding="utf-8"))
        comparison_mapping = {"C 保守编辑": "C", **comparison_mapping}
    review_path = eval_dir / "hybrid-review.md"
    review_path.write_text(
        build_blind_review(
            comparison_variants,
            comparison_mapping,
            headline_limit=args.headlines,
            brief_limit=args.briefs,
        ).replace("Codex 中文编辑盲评稿", "Codex 中文编辑 B/C/D 对比稿")
        .replace("请先不看方案映射，", ""),
        encoding="utf-8",
    )
    manifest_path = write_json(
        eval_dir / "variant-d-manifest.json",
        {
            "run_id": args.run_id,
            "provider": provider,
            "duration_ms": ai_run.duration_ms,
            "source_issue_unchanged": baseline_path.read_bytes() == baseline_bytes,
            "fallback_used": report.fallback_used,
            "fallback_articles": report.checks.get("fallback_articles", []),
            "candidate": str(candidate_path),
            "final": str(variant_d_path),
            "validation": str(validation_path),
            "review": str(review_path),
            "debug": str(debug_path),
        },
    )
    if baseline_path.read_bytes() != baseline_bytes:
        raise RuntimeError("Hybrid evaluation unexpectedly modified the source 05_issue.json")
    print(f"Provider: {provider}")
    print(f"Fallback articles: {report.checks.get('fallback_articles', [])}")
    print(f"Review: {review_path}")
    print(f"Validation: {validation_path}")
    print(f"Manifest: {manifest_path}")
    return 0


def debug_file_read_candidates() -> list[CandidateItem]:
    fetched_at = datetime.now(timezone.utc)
    samples = [
        RawItem(
            id="debug-keep",
            source_id="techcrunch",
            source_name="TechCrunch",
            source_language="en",
            title="Nvidia unveils new AI chip for data center training",
            url="https://example.com/nvidia-ai-chip",
            summary="Nvidia announced a new AI chip for data center model training, with higher throughput and support from major cloud providers.",
            fetched_at=fetched_at,
        ),
        RawItem(
            id="debug-maybe",
            source_id="the_verge",
            source_name="The Verge",
            source_language="en",
            title="Apple tests a small AI assistant feature in Messages",
            url="https://example.com/apple-ai-assistant",
            summary="Apple is testing a limited AI assistant feature that can summarize messages and suggest replies, but release timing remains unclear.",
            fetched_at=fetched_at,
        ),
        RawItem(
            id="debug-drop",
            source_id="ifanr",
            source_name="爱范儿",
            source_language="zh",
            title="一款耳机新配色上市，主打夏季穿搭",
            url="https://example.com/headphones-color",
            summary="某消费电子品牌发布耳机新配色，主要强调外观、穿搭和促销信息，没有新的 AI 或半导体进展。",
            fetched_at=fetched_at,
        ),
    ]
    return [
        CandidateItem(
            raw_item=samples[0],
            score=95,
            matched_terms=["英伟达", "AI芯片", "半导体"],
            reason="命中英伟达和 AI 芯片，事件重要度高。",
        ),
        CandidateItem(
            raw_item=samples[1],
            score=62,
            matched_terms=["苹果", "AI产品发布"],
            reason="命中苹果和 AI 产品，但信息量有限。",
        ),
        CandidateItem(
            raw_item=samples[2],
            score=5,
            matched_terms=[],
            reason="消费电子外观促销，弱相关。",
        ),
    ]


def build_file_read_test_prompt(input_path: Path) -> str:
    return f"""
你是《我的日报·科技》的第一轮新闻编辑。请读取本地 JSON 文件，并基于文件中的 candidates 字段做语义粗筛。

输入文件：
{input_path}

任务目标：
1. 用中文理解英文标题和摘要，不需要先翻译全文。
2. 每个输入 candidate 都必须给出 keep / maybe / drop 三选一。
3. keep 表示值得抓正文并大概率进入最终选题；maybe 表示值得抓正文但不确定；drop 表示不进入正文补全。
4. 命中“不想看”应明显降权，但如果事件重大，可以保留并说明理由。
5. 聚合类新闻需要判断其中是否包含真正命中关注清单的内容。

输出要求：
- 只输出一个 JSON 对象，不要 Markdown，不要解释。
- keep_item_ids、maybe_item_ids、drop_item_ids 三组加起来必须覆盖所有输入 id。
- items 必须包含所有输入 id，且 decision 与顶层列表一致。
- relevance_score 和 importance_score 都是 0-100 整数。
- 本次输入只有 3 条，输出必须同时包含 keep、maybe、drop 三类。

JSON schema 形状：
{{
  "keep_item_ids": ["..."],
  "maybe_item_ids": ["..."],
  "drop_item_ids": ["..."],
  "items": [
    {{
      "source_item_id": "...",
      "decision": "keep",
      "category": "AI 芯片",
      "relevance_score": 90,
      "importance_score": 88,
      "reason": "中文理由",
      "is_aggregate": false,
      "aggregate_highlights": []
    }}
  ]
}}
""".strip()


def ai_file_read_test(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    config = load_pipeline_config(Path(args.config) if args.config else None)
    provider = args.provider or config.ai.default_provider
    debug_run_id = "_debug_ai_file_read"
    debug_dir = run_dir(debug_run_id)
    candidates = debug_file_read_candidates()
    input_path = debug_dir / "input.json"
    input_payload = {
        "task": "semantic_shortlist_file_read_test",
        "candidates": [
            {
                "id": candidate.raw_item.id,
                "source": candidate.raw_item.source_name,
                "source_language": candidate.raw_item.source_language,
                "title": candidate.raw_item.title,
                "url": candidate.raw_item.url,
                "published_at": candidate.raw_item.published_at.isoformat() if candidate.raw_item.published_at else None,
                "rss_summary": candidate.raw_item.summary,
                "coarse_score": candidate.score,
                "coarse_reason": candidate.reason,
                "matched_terms": candidate.matched_terms,
                "avoided_terms": candidate.avoided_terms,
            }
            for candidate in candidates
        ],
    }
    debug_dir.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(input_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    prompt = build_file_read_test_prompt(input_path.resolve())
    started_at = datetime.now(timezone.utc)
    provider_result = None
    try:
        provider_result = run_provider(
            provider,
            prompt,
            CodexShortlistOutput,
            config,
            use_output_schema=False,
        )
        raw_output = provider_result.output_text
        if provider_result.return_code != 0:
            raise AIEngineError(f"AI command failed with code {provider_result.return_code}: {provider_result.stderr.strip()}")
        parsed = extract_json_object(raw_output)
        output = normalize_shortlist_top_level_ids(CodexShortlistOutput.model_validate(parsed))
        validate_shortlist_ids(output, candidates)
        decisions = {item.decision for item in output.items}
        if decisions != {"keep", "maybe", "drop"}:
            raise ValueError(f"Expected keep/maybe/drop decisions, got: {', '.join(sorted(decisions))}")
        finished_at = datetime.now(timezone.utc)
        output_path = debug_dir / "output.json"
        raw_path = debug_dir / "raw.txt"
        run_path = debug_dir / "run.json"
        events_path = debug_dir / "provider_events.jsonl"
        output_path.write_text(json.dumps(output.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        raw_path.write_text(raw_output, encoding="utf-8")
        if provider_result.provider_events:
            events_path.write_text(provider_result.provider_events, encoding="utf-8")
        run_payload = AIRunRecord(
            task_type="file_read_test",
            prompt_version="debug",
            prompt=prompt,
            raw_output=raw_output,
            parsed_output=output.model_dump(mode="json"),
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            provider=provider,
            model=provider_result.model,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            command=provider_result.command,
            return_code=provider_result.return_code,
            prompt_chars=len(prompt),
            raw_output_chars=len(raw_output),
            parsed_output_chars=len(json.dumps(output.model_dump(mode="json"), ensure_ascii=False)),
            input_tokens=provider_result.input_tokens,
            output_tokens=provider_result.output_tokens,
            cache_read_tokens=provider_result.cache_read_tokens,
            cache_write_tokens=provider_result.cache_write_tokens,
            total_tokens=provider_result.total_tokens,
            cost_usd=provider_result.cost_usd,
            provider_event_log=events_path.name if provider_result.provider_events else None,
        )
        run_path.write_text(run_payload.model_dump_json(indent=2), encoding="utf-8")
        print(f"Provider: {provider}")
        print(f"Input: {input_path}")
        print(f"Output: {output_path}")
        print(f"Run: {run_path}")
        summarize_codex_shortlist(output)
        return 0
    except Exception as exc:  # noqa: BLE001 - debug command should persist failure.
        finished_at = datetime.now(timezone.utc)
        raw_output = provider_result.output_text if provider_result else ""
        raw_path = debug_dir / "raw.txt"
        run_path = debug_dir / "run.json"
        events_path = debug_dir / "provider_events.jsonl"
        raw_path.write_text(raw_output, encoding="utf-8")
        if provider_result and provider_result.provider_events:
            events_path.write_text(provider_result.provider_events, encoding="utf-8")
        run_payload = AIRunRecord(
            task_type="file_read_test",
            prompt_version="debug",
            prompt=prompt,
            raw_output=raw_output,
            parsed_output=None,
            status="failed",
            error=str(exc),
            started_at=started_at,
            finished_at=finished_at,
            provider=provider,
            model=provider_result.model if provider_result else None,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            command=provider_result.command if provider_result else [],
            return_code=provider_result.return_code if provider_result else None,
            prompt_chars=len(prompt),
            raw_output_chars=len(raw_output),
            input_tokens=provider_result.input_tokens if provider_result else None,
            output_tokens=provider_result.output_tokens if provider_result else None,
            cache_read_tokens=provider_result.cache_read_tokens if provider_result else None,
            cache_write_tokens=provider_result.cache_write_tokens if provider_result else None,
            total_tokens=provider_result.total_tokens if provider_result else None,
            cost_usd=provider_result.cost_usd if provider_result else None,
            provider_event_log=events_path.name if provider_result and provider_result.provider_events else None,
        )
        run_path.write_text(run_payload.model_dump_json(indent=2), encoding="utf-8")
        print(f"Input: {input_path}")
        print(f"Raw: {raw_path}")
        print(f"Run: {run_path}")
        raise


def _parse_feedback_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    return datetime.min.replace(tzinfo=timezone.utc)


def _feedback_group_key(row: dict[str, Any]) -> tuple[Any, ...]:
    if row.get("scope") == "issue":
        return (row.get("issue_id"), "issue")
    return (row.get("issue_id"), "article", row.get("article_level"), row.get("article_index"))


def _latest_signal(rows: list[dict[str, Any]]) -> str | None:
    latest = rows[-1]
    if latest.get("signal") is None:
        return None
    for row in reversed(rows):
        signal = row.get("signal")
        if signal in {"up", "down"}:
            return signal
    return None


def _latest_note(rows: list[dict[str, Any]]) -> str | None:
    for row in reversed(rows):
        note = (row.get("note") or "").strip()
        if note:
            return note
    return None


def _load_issue_for_feedback(issue_id: str, issue_date: str) -> Issue:
    run_issue_path = run_dir("issues") / f"{issue_id}.json"
    if run_issue_path.exists():
        return Issue.model_validate_json(run_issue_path.read_text(encoding="utf-8"))
    dist_issue_path = DIST_DIR / "data" / "issues" / f"{issue_date}.json"
    if dist_issue_path.exists():
        return Issue.model_validate_json(dist_issue_path.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"Cannot find issue data for feedback: {issue_id} ({issue_date}); checked {run_issue_path} and {dist_issue_path}"
    )


def _article_context(issue: Issue, level: str, index: int) -> dict[str, Any]:
    articles = issue.headlines if level == "headline" else issue.briefs
    try:
        article = articles[index - 1]
    except IndexError as exc:
        raise ValueError(f"Feedback points to missing article: {issue.id} {level} #{index}") from exc
    return {
        "level": level,
        "index": index,
        "source_item_ids": article.source_item_ids,
        "title_zh": article.title_zh,
        "summary_zh": article.summary_zh,
        "ai_impact": article.ai_impact if level == "headline" else None,
    }


def prepare_digest_feedback_payload(
    *,
    section_slug: str,
    feedback_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], int]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    consumed_ids: list[str] = []
    for row in feedback_rows:
        grouped.setdefault(_feedback_group_key(row), []).append(row)
        if row.get("id"):
            consumed_ids.append(str(row["id"]))

    aggregated: list[dict[str, Any]] = []
    issues: dict[str, Issue] = {}
    for rows in grouped.values():
        rows.sort(key=lambda row: _parse_feedback_datetime(row.get("created_at")))
        latest = rows[-1]
        signal = _latest_signal(rows)
        note = _latest_note(rows)
        if not signal and not note:
            continue
        issue_id = str(latest["issue_id"])
        issue_date = str(latest["issue_date"])
        issue = issues.get(issue_id)
        if issue is None:
            issue = _load_issue_for_feedback(issue_id, issue_date)
            issues[issue_id] = issue
        item: dict[str, Any] = {
            "issue_id": issue_id,
            "issue_date": issue_date,
            "scope": latest["scope"],
            "signal": signal,
            "note": note,
            "event_count": len(rows),
        }
        if latest["scope"] == "article":
            item["article"] = _article_context(issue, str(latest["article_level"]), int(latest["article_index"]))
        else:
            item["issue_summary"] = {
                "headlines": [article.title_zh for article in issue.headlines],
                "briefs": [article.title_zh for article in issue.briefs],
            }
        aggregated.append(item)

    profiles = load_profiles(section_slug)
    return {
        "section_slug": section_slug,
        "feedback": aggregated,
        "current_profiles": profiles,
    }, consumed_ids, len(aggregated)


def digest_feedback(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    section = load_section(args.section)
    config = load_pipeline_config(Path(args.config) if args.config else None)
    provider = args.provider or resolve_stage_provider(config, "digest_feedback")
    run_id = args.run_id or f"digest-{section.slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    owner_token = os.getenv("OWNER_FEEDBACK_TOKEN", "").strip()
    if not owner_token:
        print("OWNER_FEEDBACK_TOKEN is not configured. Skipping taste feedback digestion.")
        return 0
    store = SupabaseStore.from_env()
    rows = store.fetch_undigested_feedback(
        section.slug,
        from_date=args.from_date,
        to_date=args.to_date,
        include_digested=args.redigest,
        owner_token=owner_token,
    )
    if not rows:
        print("No feedback to digest.")
        return 0

    payload, consumed_ids, aggregated_count = prepare_digest_feedback_payload(
        section_slug=section.slug,
        feedback_rows=rows,
    )
    if aggregated_count == 0:
        store.mark_feedback_digested(consumed_ids)
        print(f"No active feedback after aggregation. Marked {len(consumed_ids)} row(s) digested.")
        return 0

    input_path = logs_dir(run_id) / "digest_feedback_input.json"
    _write_json_file(input_path, payload)
    prompt = build_digest_file_prompt(section, input_path.resolve())
    try:
        output, ai_run = run_ai_task(
            task_type="digest_feedback",
            prompt=prompt,
            output_model=DigestFeedbackOutput,
            provider=provider,
            config=config,
            use_output_schema=False,
        )
    except AIEngineError as exc:
        if exc.record:
            debug_path = save_ai_debug(run_id, "06_ai_digest", exc.record, config)
            print(f"Debug: {debug_path}")
        raise

    previous_profiles = payload["current_profiles"]
    snapshot_dir = snapshot_profiles(section.slug, run_id)
    profile_paths = write_profiles(
        section.slug,
        taste_md=output.taste_md,
        style_md=output.style_md,
        seed_suggestions_append=output.seed_suggestions_append,
    )
    profile_update_log = {
        "run_id": run_id,
        "section_slug": section.slug,
        "snapshot_dir": str(snapshot_dir),
        "digested_feedback_groups": aggregated_count,
        "marked_feedback_rows": len(consumed_ids),
        "changes": output.changes,
        "before": {
            "taste": _profile_text_stats(previous_profiles.get("taste", "")),
            "style": _profile_text_stats(previous_profiles.get("style", "")),
            "seed_suggestions": _profile_text_stats(previous_profiles.get("seed_suggestions", "")),
        },
        "after": {
            "taste": _profile_text_stats(output.taste_md.rstrip() + "\n"),
            "style": _profile_text_stats(output.style_md.rstrip() + "\n"),
            "seed_suggestions_append": _profile_text_stats(output.seed_suggestions_append.strip()),
        },
        "limits": {
            "taste_chars_max": 6000,
            "style_chars_max": 6000,
            "seed_suggestions_append_chars_max": 2000,
            "changes_max": 20,
        },
    }
    profile_update_path = logs_dir(run_id) / "profile_update.json"
    _write_json_file(profile_update_path, profile_update_log)
    store.mark_feedback_digested(consumed_ids)
    debug_path = save_ai_debug(run_id, "06_ai_digest", ai_run, config)
    print(f"Provider: {provider}")
    print(f"Input: {input_path}")
    print(f"Debug: {debug_path}")
    print(f"Digested feedback groups: {aggregated_count}")
    print(f"Marked feedback rows: {len(consumed_ids)}")
    for name, path in profile_paths.items():
        print(f"{name}: {path}")
    print(f"Profile snapshot: {snapshot_dir}")
    print(f"Profile update log: {profile_update_path}")
    if output.changes:
        print("Changes:")
        for change in output.changes:
            print(f"- {change}")
    return 0


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def _profile_text_stats(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "lines": len(text.splitlines()),
        "non_empty_lines": sum(1 for line in text.splitlines() if line.strip()),
    }


class PipelineLogger:
    def __init__(self, *, run_id: str, section_slug: str, issue_date: date) -> None:
        self.run_id = run_id
        self.section_slug = section_slug
        self.issue_date = issue_date
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: datetime | None = None
        self.stage_records: list[dict[str, Any]] = []
        self.base_dir = logs_dir(run_id)
        self.stages_dir = self.base_dir / "stages"
        self.pipeline_log_path = self.base_dir / "pipeline.log"
        self.pipeline_json_path = self.base_dir / "pipeline.json"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"{timestamp} {message}"
        print(message, flush=True)
        self.pipeline_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.pipeline_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def start_stage(self, stage: PipelineStage) -> datetime:
        self.log(f"[{stage}] start")
        return datetime.now(timezone.utc)

    def finish_stage(
        self,
        *,
        stage: PipelineStage,
        started_at: datetime,
        status: Literal["success", "skipped", "failed"],
        inputs: list[Path] | None = None,
        outputs: list[Path] | None = None,
        metadata: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        finished_at = datetime.now(timezone.utc)
        record = {
            "stage": stage,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
            "inputs": [str(path) for path in inputs or []],
            "outputs": [str(path) for path in outputs or []],
            "metadata": metadata or {},
            "error": error,
        }
        self.stage_records.append(record)
        _write_json_file(self.stages_dir / f"{stage}.json", record)
        self.log(f"[{stage}] {status}")
        self.write_summary(status="running")
        return record

    def write_summary(
        self,
        *,
        status: Literal["running", "success", "failed", "stopped"],
        error: str | None = None,
    ) -> None:
        finished_at = None if status == "running" else datetime.now(timezone.utc)
        if finished_at:
            self.finished_at = finished_at
        payload = {
            "run_id": self.run_id,
            "section": self.section_slug,
            "issue_date": self.issue_date.isoformat(),
            "status": status,
            "error": error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": (
                int((self.finished_at - self.started_at).total_seconds() * 1000)
                if self.finished_at
                else None
            ),
            "outputs_dir": output_dir(self.run_id),
            "logs_dir": self.base_dir,
            "stages": self.stage_records,
        }
        _write_json_file(self.pipeline_json_path, payload)


class PipelineRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        load_dotenv(WEB_DIR / ".env")
        self.args = args
        self.section = load_section(args.section)
        self.issue_date = parse_date(args.date)
        self.run_id = args.run_id or new_run_id(self.section.slug, self.issue_date)
        self.config = load_pipeline_config(Path(args.config) if args.config else None)
        self.timeout_seconds = float(os.getenv("DAILY_NEWS_FETCH_TIMEOUT_SECONDS", "20"))
        self.logger = PipelineLogger(
            run_id=self.run_id,
            section_slug=self.section.slug,
            issue_date=self.issue_date,
        )

    async def run(self) -> int:
        self.logger.write_summary(status="running")
        self.logger.log(f"Run ID: {self.run_id}")
        self.logger.log(f"Outputs: {output_dir(self.run_id)}")
        self.logger.log(f"Logs: {logs_dir(self.run_id)}")
        try:
            for stage in PIPELINE_STAGES:
                await self._run_stage(stage)
                if stage == self.args.stop_after:
                    self.logger.write_summary(status="stopped")
                    self.logger.log(f"Stopped after stage: {stage}")
                    return 0
        except Exception as exc:  # noqa: BLE001 - pipeline failures must be persisted.
            self.logger.write_summary(status="failed", error=str(exc))
            self.logger.log(f"Pipeline failed: {exc}")
            raise
        self.logger.write_summary(status="success")
        self.logger.log("Pipeline success")
        return 0

    async def _run_stage(self, stage: PipelineStage) -> None:
        started_at = self.logger.start_stage(stage)
        try:
            if self.args.resume:
                try:
                    if self._can_skip_stage(stage):
                        self.logger.finish_stage(stage=stage, started_at=started_at, status="skipped")
                        return
                except FileNotFoundError:
                    pass
            result = await self._execute_stage(stage)
            self.logger.finish_stage(
                stage=stage,
                started_at=started_at,
                status="success",
                inputs=result.get("inputs", []),
                outputs=result.get("outputs", []),
                metadata=result.get("metadata", {}),
            )
        except Exception as exc:  # noqa: BLE001 - stage details are part of observability.
            self.logger.finish_stage(
                stage=stage,
                started_at=started_at,
                status="failed",
                error=f"{exc}\n{traceback.format_exc()}",
            )
            raise

    async def _execute_stage(self, stage: PipelineStage) -> dict[str, Any]:
        if stage == "fetch":
            raw_items = await fetch_section_items(
                self.section,
                per_source_limit=self.args.per_source_limit,
                timeout_seconds=self.timeout_seconds,
            )
            path = save_raw_items(self.run_id, raw_items)
            successful = [item for item in raw_items if item.fetch_status != "failed"]
            summarize_raw_items(raw_items)
            return {
                "outputs": [path],
                "metadata": {"raw_items": len(raw_items), "successful_raw_items": len(successful)},
            }

        if stage == "local_shortlist":
            raw_items = load_raw_items(self.run_id)
            history = build_prefilter_history(
                section_slug=self.section.slug,
                issue_date=self.issue_date,
                config=self.config,
            )
            summarize_prefilter_history(
                history,
                lookback_days=self.config.dedupe.history_lookback_days,
                title_hash_enabled=self.config.dedupe.title_hash_enabled,
            )
            candidates = rank_candidates(
                raw_items,
                self.section,
                max_candidates=self.args.max_candidates,
                per_source_limit=self.args.per_source_limit,
                require_interest_match_when_over_capacity=False,
                historical_urls=history.urls,
                historical_title_hashes=history.title_hashes if self.config.dedupe.title_hash_enabled else None,
            )
            path = save_candidates(self.run_id, candidates)
            summarize_candidates(candidates)
            return {
                "inputs": [artifact_path(self.run_id, "01_raw_items.json")],
                "outputs": [path],
                "metadata": {
                    "candidates": len(candidates),
                    "history_lookback_days": self.config.dedupe.history_lookback_days,
                    "history_issue_count": len(history.issue_ids),
                    "history_url_count": len(history.urls),
                    "history_title_hash_count": len(history.title_hashes) if self.config.dedupe.title_hash_enabled else 0,
                },
            }

        if stage == "ai_shortlist":
            provider = self._provider_for_stage(stage, self.args.ai_shortlist_provider)
            shortlist, saved_output_path, debug_path = run_ai_shortlist_stage(
                run_id=self.run_id,
                section=self.section,
                config=self.config,
                provider=provider,
            )
            summarize_codex_shortlist(shortlist)
            return {
                "inputs": [artifact_path(self.run_id, "02_candidates.json")],
                "outputs": [saved_output_path, debug_path],
                "metadata": {
                    "provider": provider,
                    "keep": len(shortlist.keep_item_ids),
                    "maybe": len(shortlist.maybe_item_ids),
                },
            }

        if stage == "enrich":
            local_prefilter = load_shortlist(self.run_id)
            shortlist = candidates_for_enrichment(self.run_id, local_prefilter)
            body_candidates = self.args.body_candidates or len(shortlist)
            enriched_items = await enrich_candidate_content(
                [candidate.raw_item for candidate in shortlist],
                limit=body_candidates,
                timeout_seconds=self.timeout_seconds,
            )
            enriched_candidates = merge_enriched_candidates(shortlist, enriched_items)
            path = save_enriched_candidates(self.run_id, enriched_candidates)
            summarize_enriched(enriched_candidates)
            return {
                "inputs": [
                    artifact_path(self.run_id, "02_candidates.json"),
                    artifact_path(self.run_id, "02_codex_shortlist.json"),
                ],
                "outputs": [path],
                "metadata": {
                    "enriched_candidates": len(enriched_candidates),
                    "body_candidates": body_candidates,
                },
            }

        if stage == "ai_select":
            provider = self._provider_for_stage(stage, self.args.ai_select_provider)
            selection, saved_output_path, debug_path = run_ai_select_stage(
                run_id=self.run_id,
                section=self.section,
                config=self.config,
                provider=provider,
            )
            summarize_selection(selection)
            history_index_path = artifact_path(self.run_id, "04_history_index.json")
            inputs = [artifact_path(self.run_id, "03_enriched_candidates.json")]
            if history_index_path.exists():
                inputs.append(history_index_path)
            return {
                "inputs": inputs,
                "outputs": [saved_output_path, debug_path],
                "metadata": {
                    "provider": provider,
                    "headlines": len(selection.headlines),
                    "briefs": len(selection.briefs),
                    "discarded": len(selection.discarded),
                    "history_index_enabled": self.config.selection_history.enabled,
                },
            }

        if stage == "ai_compose":
            provider = self._provider_for_stage(stage, self.args.ai_compose_provider)
            issue, saved_output_path, debug_path = run_ai_compose_stage(
                run_id=self.run_id,
                section=self.section,
                issue_date=self.issue_date,
                issue_number=self.args.issue_number,
                config=self.config,
                provider=provider,
                save_as_draft=True,
            )
            summarize_issue(issue)
            return {
                "inputs": [
                    artifact_path(self.run_id, "04_selection.json"),
                    artifact_path(self.run_id, "03_enriched_candidates.json"),
                ],
                "outputs": [saved_output_path, debug_path],
                "metadata": {
                    "provider": provider,
                    "headlines": len(issue.headlines),
                    "briefs": len(issue.briefs),
                },
            }

        if stage == "ai_humanize":
            provider = self._provider_for_stage(stage, self.args.ai_humanize_provider)
            issue, outputs, report = run_ai_humanize_stage(
                run_id=self.run_id,
                config=self.config,
                provider=provider,
            )
            summarize_issue(issue)
            return {
                "inputs": [
                    artifact_path(self.run_id, "05_issue_draft.json"),
                    artifact_path(self.run_id, "03_enriched_candidates.json"),
                    WEB_DIR / "prompts" / "zh_news_editor.md",
                ],
                "outputs": outputs,
                "metadata": {
                    "provider": provider,
                    "headlines": len(issue.headlines),
                    "briefs": len(issue.briefs),
                    "fallback_used": report.fallback_used,
                    "fallback_articles": report.checks.get("fallback_articles", []),
                },
            }

        if stage == "publish_frontend":
            issue = load_issue_from_run(self.run_id)
            validate_issue_content(issue)
            with temporary_feedback_mode("reader"):
                outputs = build_frontend_app(issue)
            owner_outputs: dict[str, Path] = {}
            if self.args.render_owner:
                with temporary_feedback_mode("owner"):
                    owner_outputs = build_frontend_app(issue)
                print(f"Generated owner app: {owner_outputs['index']}")
            return {
                "inputs": [artifact_path(self.run_id, "05_issue.json")],
                "outputs": list(outputs.values()) + list(owner_outputs.values()),
                "metadata": {
                    "issue_id": issue.id,
                    "issue_date": issue.issue_date.isoformat(),
                    "owner_rendered": bool(owner_outputs),
                },
            }

        raise ValueError(f"Unknown pipeline stage: {stage}")

    def _provider_for_stage(self, stage: AIStage, cli_provider: ProviderName | None) -> ProviderName:
        task_type = AI_STAGE_TASKS[stage]
        return resolve_stage_provider(self.config, task_type, cli_provider)

    def _can_skip_stage(self, stage: PipelineStage) -> bool:
        if stage == "fetch":
            return bool(load_raw_items(self.run_id))
        if stage == "local_shortlist":
            return bool(load_shortlist(self.run_id))
        if stage == "ai_shortlist":
            shortlist = normalize_shortlist_top_level_ids(load_codex_shortlist(self.run_id))
            validate_shortlist_ids(shortlist, load_shortlist(self.run_id))
            return True
        if stage == "enrich":
            return bool(load_enriched_candidates(self.run_id))
        if stage == "ai_select":
            selection = load_selection(self.run_id)
            validate_selection_ids(selection, load_enriched_candidates(self.run_id))
            return True
        if stage == "ai_compose":
            draft_path = artifact_path(self.run_id, "05_issue_draft.json")
            issue = (
                load_issue_draft_from_run(self.run_id)
                if draft_path.exists()
                else load_issue_from_run(self.run_id)
            )
            validate_issue_content(issue)
            return True
        if stage == "ai_humanize":
            issue = load_issue_from_run(self.run_id)
            validate_issue_content(issue)
            validation_path = artifact_path(self.run_id, "05_humanize_validation.json")
            return validation_path.exists()
        if stage == "publish_frontend":
            issue = load_issue_from_run(self.run_id)
            data_path = DIST_DIR / "data" / "issues" / f"{issue.issue_date.isoformat()}.json"
            route_path = DIST_DIR / "issues" / f"{issue.issue_date.isoformat()}.html"
            if not (data_path.exists() and route_path.exists()):
                return False
            if self.args.render_owner:
                owner_data_path = DIST_OWNER_DIR / "data" / "issues" / f"{issue.issue_date.isoformat()}.json"
                owner_route_path = DIST_OWNER_DIR / "issues" / f"{issue.issue_date.isoformat()}.html"
                return owner_data_path.exists() and owner_route_path.exists()
            return True
        raise ValueError(f"Unknown pipeline stage: {stage}")


async def run_pipeline(args: argparse.Namespace) -> int:
    runner = PipelineRunner(args)
    return await runner.run()


def render_mvp(args: argparse.Namespace) -> int:
    issue = load_issue_from_run(args.run_id)
    validate_issue_content(issue)
    outputs = build_frontend_app(issue)
    issue_html = outputs["issue"].read_text(encoding="utf-8")
    issue_data = outputs["data"].read_text(encoding="utf-8")
    dist_dir = outputs["index"].parent
    css = (dist_dir / "assets" / "app.css").read_text(encoding="utf-8")
    js = (dist_dir / "assets" / "app.js").read_text(encoding="utf-8")
    checks = {
        "viewport": 'name="viewport"' in issue_html,
        "app_root": 'id="app"' in issue_html,
        "mobile_520": "@media(max-width:520px)" in css,
        "data_headlines": '"headlines"' in issue_data,
        "data_briefs": '"briefs"' in issue_data,
        "frontend_renderer": "renderIssuePicker" in js,
    }
    print(f"Generated app: {outputs['index']}")
    print(f"Generated issue route: {outputs['issue']}")
    print(f"Generated data: {outputs['data']}")
    print("前端输出检查：")
    for name, ok in checks.items():
        print(f"- {name}: {'ok' if ok else 'missing'}")
    if not all(checks.values()):
        raise ValueError("Frontend output check failed")
    return 0


def sync_run(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    store = SupabaseStore.from_env()
    if not store.enabled:
        raise ValueError("Supabase env not configured")
    section = load_section(args.section)
    issue = load_issue_from_run(args.run_id)
    raw_items = load_raw_items(args.run_id)
    candidates = load_enriched_candidates(args.run_id)
    store.upsert_sources(section)
    store.create_fetch_run(args.run_id, section, issue.issue_date.isoformat())
    store.insert_raw_items(args.run_id, raw_items)
    store.insert_candidates(args.run_id, candidates)
    store.insert_issue(args.run_id, issue)
    store.finish_fetch_run(args.run_id, status="success")
    print(f"Synced Supabase run: {args.run_id}")
    return 0


def render_existing(args: argparse.Namespace) -> int:
    issue = load_issue(args.issue_id)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = DIST_DIR
    outputs = build_frontend_app(issue, dist_dir=output_dir)
    print(f"Rendered app {outputs['index']}")
    print(f"Rendered issue route {outputs['issue']}")
    print(f"Rendered data {outputs['data']}")
    return 0


def validate_config(_: argparse.Namespace) -> int:
    config = load_config()
    for slug, section in config.sections.items():
        if not section.enabled_sources:
            raise ValueError(f"Section '{slug}' has no enabled sources")
        if not section.interests.want.all_terms:
            raise ValueError(f"Section '{slug}' has no positive interest terms")
    print(f"Config OK: {len(config.sections)} section(s)")
    return 0


def clean_dist(_: argparse.Namespace) -> int:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    print(f"Removed {DIST_DIR}")
    return 0


LEGACY_LOG_PATTERNS = [
    "*_prompt.md",
    "*_raw.txt",
    "*_run.json",
    "*_attempts.json",
    "*_provider_events.jsonl",
    "*_output.json",
    "ai_metrics.jsonl",
    "03_prompt.md",
    "04_ai_raw.txt",
    "04_ai_output.json",
    "04_ai_run.json",
]
OUTPUT_ARTIFACT_FILENAMES = [
    "01_raw_items.json",
    "02_candidates.json",
    "02_codex_shortlist.json",
    "03_enriched_candidates.json",
    "04_selection.json",
    "05_issue_draft.json",
    "05_issue_humanize_candidate.json",
    "05_humanize_validation.json",
    "05_issue.json",
]


def _move_legacy_log_file(path: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        index = 1
        while True:
            candidate = target_dir / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                target = candidate
                break
            index += 1
    shutil.move(str(path), str(target))
    return target


def pack_logs(run_id: str) -> Path:
    base_dir = logs_dir(run_id)
    archive_dir = base_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{run_id}-logs.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for item in base_dir.iterdir():
            if item == archive_dir:
                continue
            archive.add(item, arcname=item.relative_to(base_dir))
    return archive_path


def clean_run(args: argparse.Namespace) -> int:
    base_run_dir = run_dir(args.run_id)
    if not base_run_dir.exists():
        raise FileNotFoundError(f"Run not found: {base_run_dir}")
    legacy_dir = logs_dir(args.run_id) / "legacy"
    moved: list[Path] = []
    for pattern in LEGACY_LOG_PATTERNS:
        for path in base_run_dir.glob(pattern):
            if path.is_file():
                moved.append(_move_legacy_log_file(path, legacy_dir))

    migrated_outputs: list[Path] = []
    target_output_dir = output_dir(args.run_id)
    target_output_dir.mkdir(parents=True, exist_ok=True)
    for filename in OUTPUT_ARTIFACT_FILENAMES:
        source = base_run_dir / filename
        target = target_output_dir / filename
        if source.exists() and source.is_file() and not target.exists():
            shutil.move(str(source), str(target))
            migrated_outputs.append(target)

    archive_path: Path | None = None
    if args.pack_logs:
        archive_path = pack_logs(args.run_id)

    print(f"Cleaned run: {args.run_id}")
    print(f"- moved legacy logs: {len(moved)}")
    print(f"- migrated outputs: {len(migrated_outputs)}")
    print(f"- outputs kept: {output_dir(args.run_id)}")
    print(f"- logs: {logs_dir(args.run_id)}")
    if archive_path:
        print(f"- archive: {archive_path}")
    return 0


def export_xhs(args: argparse.Namespace) -> int:
    issue = load_issue_for_xhs(args.date)
    output_dir = Path(args.output_dir) if args.output_dir else None
    config = load_pipeline_config(Path(args.config) if args.config else None)
    try:
        result = export_xhs_issue(
            issue,
            output_dir=output_dir,
            config=config,
            ai_condense=True,
            provider=args.provider,
            cover_template=args.cover_template,
            cover_headline=args.cover_headline,
        )
    except XHSExportConfigurationError as exc:
        print(f"小红书导出参数错误：{exc}", file=sys.stderr)
        return 2
    except XHSExportAIError as exc:
        print(f"小红书 AI 导出失败：{exc}", file=sys.stderr)
        print("未产出可发布图片或 caption；请恢复 AI 后重试。", file=sys.stderr)
        return 2
    print(f"小红书日报图组已导出（封面：{args.cover_template}）：{result.output_dir}")
    for path in result.image_paths:
        print(f"- {path}")
    print(f"- {result.caption_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-news")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_pipeline_parser = subparsers.add_parser("run-pipeline", help="Run the checkpoint pipeline end to end")
    run_pipeline_parser.add_argument("--section", default="tech")
    run_pipeline_parser.add_argument("--date")
    run_pipeline_parser.add_argument("--run-id")
    run_pipeline_parser.add_argument("--resume", action="store_true")
    run_pipeline_parser.add_argument("--stop-after", choices=PIPELINE_STAGES)
    run_pipeline_parser.add_argument("--per-source-limit", type=int, default=25)
    run_pipeline_parser.add_argument("--max-candidates", type=int, default=60)
    run_pipeline_parser.add_argument(
        "--body-candidates",
        type=int,
        help="Limit body extraction count; default extracts every AI-selected keep/maybe candidate",
    )
    run_pipeline_parser.add_argument("--issue-number", type=int)
    run_pipeline_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    run_pipeline_parser.add_argument("--ai-shortlist-provider", choices=["claude", "codex"])
    run_pipeline_parser.add_argument("--ai-select-provider", choices=["claude", "codex"])
    run_pipeline_parser.add_argument("--ai-compose-provider", choices=["claude", "codex"])
    run_pipeline_parser.add_argument("--ai-humanize-provider", choices=["claude", "codex"])
    run_pipeline_parser.add_argument(
        "--render-owner",
        action="store_true",
        help="Also render the local owner feedback app to web/dist-owner after the public app",
    )
    run_pipeline_parser.set_defaults(func=lambda args: asyncio.run(run_pipeline(args)))

    digest_parser = subparsers.add_parser("digest-feedback", help="Digest Supabase feedback into local taste/style profiles")
    digest_parser.add_argument("--section", default="tech")
    digest_parser.add_argument("--run-id")
    digest_parser.add_argument("--provider", choices=["claude", "codex"])
    digest_parser.add_argument("--from", dest="from_date")
    digest_parser.add_argument("--to", dest="to_date")
    digest_parser.add_argument("--redigest", action="store_true")
    digest_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    digest_parser.set_defaults(func=digest_feedback)

    fetch_parser = subparsers.add_parser("fetch-mvp", help="Checkpoint 1: fetch RSS items")
    fetch_parser.add_argument("--section", default="tech")
    fetch_parser.add_argument("--date")
    fetch_parser.add_argument("--run-id")
    fetch_parser.add_argument("--per-source-limit", type=int, default=3)
    fetch_parser.set_defaults(func=lambda args: asyncio.run(fetch_mvp(args)))

    shortlist_parser = subparsers.add_parser("shortlist-mvp", help="Checkpoint 2a: local light prefilter")
    shortlist_parser.add_argument("--section", default="tech")
    shortlist_parser.add_argument("--run-id", required=True)
    shortlist_parser.add_argument("--max-candidates", type=int, default=60)
    shortlist_parser.add_argument("--per-source-limit", type=int, default=12)
    shortlist_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    shortlist_parser.set_defaults(func=shortlist_mvp)

    shortlist_codex_parser = subparsers.add_parser("shortlist-codex", help="Checkpoint 2b: validate Codex rough shortlist JSON")
    shortlist_codex_parser.add_argument("--run-id", required=True)
    shortlist_codex_parser.set_defaults(func=shortlist_codex)

    ai_shortlist_parser = subparsers.add_parser("ai-shortlist", help="Generate 02_codex_shortlist.json with AI")
    ai_shortlist_parser.add_argument("--section", default="tech")
    ai_shortlist_parser.add_argument("--run-id", required=True)
    ai_shortlist_parser.add_argument("--provider", choices=["claude", "codex"])
    ai_shortlist_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    ai_shortlist_parser.set_defaults(func=ai_shortlist)

    enrich_parser = subparsers.add_parser("enrich-mvp", help="Checkpoint 3: enrich Codex-selected article bodies")
    enrich_parser.add_argument("--section", default="tech")
    enrich_parser.add_argument("--run-id", required=True)
    enrich_parser.add_argument(
        "--body-candidates",
        type=int,
        help="Limit body extraction count; default extracts every Codex-selected candidate",
    )
    enrich_parser.set_defaults(func=lambda args: asyncio.run(enrich_mvp(args)))

    select_codex_parser = subparsers.add_parser("select-codex", help="Checkpoint 4: validate Codex selection JSON")
    select_codex_parser.add_argument("--run-id", required=True)
    select_codex_parser.set_defaults(func=select_codex)

    ai_select_parser = subparsers.add_parser("ai-select", help="Generate 04_selection.json with AI")
    ai_select_parser.add_argument("--section", default="tech")
    ai_select_parser.add_argument("--run-id", required=True)
    ai_select_parser.add_argument("--provider", choices=["claude", "codex"])
    ai_select_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    ai_select_parser.set_defaults(func=ai_select)

    compose_codex_parser = subparsers.add_parser("compose-codex", help="Checkpoint 5: validate Codex issue JSON")
    compose_codex_parser.add_argument("--run-id", required=True)
    compose_codex_parser.set_defaults(func=compose_codex)

    ai_compose_parser = subparsers.add_parser("ai-compose", help="Generate 05_issue.json with AI")
    ai_compose_parser.add_argument("--section", default="tech")
    ai_compose_parser.add_argument("--run-id", required=True)
    ai_compose_parser.add_argument("--date", help="Issue date; inferred from run-id when omitted")
    ai_compose_parser.add_argument("--provider", choices=["claude", "codex"])
    ai_compose_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    ai_compose_parser.add_argument("--issue-number", type=int)
    ai_compose_parser.set_defaults(func=ai_compose)

    ai_humanize_parser = subparsers.add_parser(
        "ai-humanize",
        help="Rewrite a saved fact draft in natural Chinese and save the guarded final issue",
    )
    ai_humanize_parser.add_argument("--run-id", required=True)
    ai_humanize_parser.add_argument("--provider", choices=["claude", "codex"])
    ai_humanize_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    ai_humanize_parser.set_defaults(func=ai_humanize)

    zh_editor_eval_parser = subparsers.add_parser(
        "zh-editor-eval",
        help="Generate private A/B/C Chinese editing variants without publishing",
    )
    zh_editor_eval_parser.add_argument("--section", default="tech")
    zh_editor_eval_parser.add_argument("--run-id", required=True)
    zh_editor_eval_parser.add_argument("--provider", choices=["claude", "codex"])
    zh_editor_eval_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    zh_editor_eval_parser.add_argument("--headlines", type=int, default=4)
    zh_editor_eval_parser.add_argument("--briefs", type=int, default=6)
    zh_editor_eval_parser.set_defaults(func=zh_editor_eval)

    zh_editor_hybrid_parser = subparsers.add_parser(
        "zh-editor-hybrid-eval",
        help="Generate private A-grounded, B-style hybrid variant D without publishing",
    )
    zh_editor_hybrid_parser.add_argument("--run-id", required=True)
    zh_editor_hybrid_parser.add_argument("--provider", choices=["claude", "codex"])
    zh_editor_hybrid_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    zh_editor_hybrid_parser.add_argument("--headlines", type=int, default=4)
    zh_editor_hybrid_parser.add_argument("--briefs", type=int, default=6)
    zh_editor_hybrid_parser.set_defaults(func=zh_editor_hybrid_eval)

    ai_file_read_parser = subparsers.add_parser("ai-file-read-test", help="Debug AI local JSON file reading")
    ai_file_read_parser.add_argument("--provider", choices=["claude", "codex"])
    ai_file_read_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    ai_file_read_parser.set_defaults(func=ai_file_read_test)

    render_mvp_parser = subparsers.add_parser("render-mvp", help="Checkpoint 6: render frontend app and issue data")
    render_mvp_parser.add_argument("--run-id", required=True)
    render_mvp_parser.set_defaults(func=render_mvp)

    sync_parser = subparsers.add_parser("sync", help="Checkpoint 7: sync a confirmed run to Supabase")
    sync_parser.add_argument("--section", default="tech")
    sync_parser.add_argument("--run-id", required=True)
    sync_parser.set_defaults(func=sync_run)

    render_parser = subparsers.add_parser("render", help="Render a saved local issue snapshot")
    render_parser.add_argument("--issue-id", required=True)
    render_parser.add_argument("--output-dir")
    render_parser.set_defaults(func=render_existing)

    export_xhs_parser = subparsers.add_parser("export-xhs", help="Export a daily issue as Xiaohongshu image cards")
    export_xhs_parser.add_argument("--date", required=True, help="Issue date in YYYY-MM-DD format")
    export_xhs_parser.add_argument("--output-dir", help="Override output directory; defaults to web/runs/xhs/<date>")
    export_xhs_parser.add_argument("--config", help="Pipeline config path; defaults to web/config/pipeline.yaml")
    export_xhs_parser.add_argument("--provider", choices=["claude", "codex"], help="Override all XHS AI task providers")
    export_xhs_parser.add_argument(
        "--cover-template",
        choices=["classic", "single-hook", "v2"],
        default="classic",
        help="Cover template; alternate templates write to separate <date>-<template> directories by default",
    )
    export_xhs_parser.add_argument(
        "--cover-headline",
        type=int,
        default=1,
        help="1-based original headline number to feature and place first in the XHS export (default: 1)",
    )
    export_xhs_parser.set_defaults(func=export_xhs)

    validate_parser = subparsers.add_parser("validate-config", help="Validate section config")
    validate_parser.set_defaults(func=validate_config)

    clean_parser = subparsers.add_parser("clean-dist", help="Remove public dist output")
    clean_parser.set_defaults(func=clean_dist)

    clean_run_parser = subparsers.add_parser("clean-run", help="Clean and optionally pack private run logs")
    clean_run_parser.add_argument("--run-id", required=True)
    clean_run_parser.add_argument("--pack-logs", action="store_true")
    clean_run_parser.set_defaults(func=clean_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

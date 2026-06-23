from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from daily_news.ai_engine import build_issue_prompt, generate_issue_output
from daily_news.config import load_config, load_section
from daily_news.fetch.rss import enrich_candidate_content, fetch_section_items
from daily_news.models import (
    AIIssueOutput,
    CandidateItem,
    CodexSelectionOutput,
    CodexShortlistOutput,
    Issue,
    RawItem,
)
from daily_news.paths import DIST_DIR, RUNS_DIR, WEB_DIR
from daily_news.render import build_frontend_app
from daily_news.scoring import rank_candidates
from daily_news.storage.local import (
    load_issue,
    load_codex_shortlist,
    load_enriched_candidates,
    load_issue_from_run,
    load_raw_items,
    load_selection,
    load_shortlist,
    run_dir,
    save_ai_run,
    save_candidates,
    save_enriched_candidates,
    save_issue,
    save_prompt,
    save_raw_items,
)
from daily_news.storage.supabase import SupabaseStore


WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


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


def load_ai_output(path: Path) -> AIIssueOutput:
    return AIIssueOutput.model_validate_json(path.read_text(encoding="utf-8"))


def log_step(step: str, message: str) -> None:
    print(f"[{step}] {message}", flush=True)


def new_run_id(section_slug: str, issue_date: date) -> str:
    return f"{section_slug}-{issue_date.isoformat()}-{datetime.now().strftime('%H%M%S')}"


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
    raw_items = load_raw_items(args.run_id)
    candidates = rank_candidates(
        raw_items,
        section,
        max_candidates=args.max_candidates,
        per_source_limit=args.per_source_limit,
        require_interest_match_when_over_capacity=False,
    )
    path = save_candidates(args.run_id, candidates)
    print(f"Saved: {path}")
    summarize_candidates(candidates)
    return 0


def shortlist_codex(args: argparse.Namespace) -> int:
    local_prefilter = load_shortlist(args.run_id)
    codex_shortlist = load_codex_shortlist(args.run_id)
    validate_shortlist_ids(codex_shortlist, local_prefilter)
    summarize_codex_shortlist(codex_shortlist)
    print(f"Validated: {run_dir(args.run_id) / '02_codex_shortlist.json'}")
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
    print(f"Validated: {run_dir(args.run_id) / '04_selection.json'}")
    return 0


def compose_codex(args: argparse.Namespace) -> int:
    selection = load_selection(args.run_id)
    issue = load_issue_from_run(args.run_id)
    validate_selection_ids(selection, load_enriched_candidates(args.run_id))
    validate_issue_content(issue)
    summarize_issue(issue)
    print(f"Validated: {run_dir(args.run_id) / '05_issue.json'}")
    return 0


def render_mvp(args: argparse.Namespace) -> int:
    issue = load_issue_from_run(args.run_id)
    validate_issue_content(issue)
    outputs = build_frontend_app(issue)
    issue_html = outputs["issue"].read_text(encoding="utf-8")
    issue_data = outputs["data"].read_text(encoding="utf-8")
    css = (DIST_DIR / "assets" / "app.css").read_text(encoding="utf-8")
    js = (DIST_DIR / "assets" / "app.js").read_text(encoding="utf-8")
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


async def generate(args: argparse.Namespace) -> int:
    load_dotenv(WEB_DIR / ".env")
    section = load_section(args.section)
    issue_date = parse_date(args.date)
    run_id = f"{section.slug}-{issue_date.isoformat()}-{datetime.now().strftime('%H%M%S')}"
    timeout_seconds = float(os.getenv("DAILY_NEWS_FETCH_TIMEOUT_SECONDS", "20"))

    store = SupabaseStore.from_env()

    log_step("1/6", f"抓取 RSS：{len(section.enabled_sources)} 个源，每源最多 {args.per_source_limit} 条")
    raw_items = await fetch_section_items(
        section,
        per_source_limit=args.per_source_limit,
        timeout_seconds=timeout_seconds,
    )
    raw_path = save_raw_items(run_id, raw_items)
    successful_raw_items = [item for item in raw_items if item.fetch_status != "failed"]
    log_step("1/6", f"抓取完成：{len(successful_raw_items)} 条有效新闻，保存 {raw_path}")

    log_step("2/6", f"本地粗筛：最多保留 {args.max_candidates} 条候选")
    initial_candidates = rank_candidates(raw_items, section, max_candidates=args.max_candidates)
    log_step("2/6", f"正文提取：只对前 {args.body_candidates} 条高潜候选抓正文")
    enriched_items = await enrich_candidate_content(
        [candidate.raw_item for candidate in initial_candidates],
        limit=args.body_candidates,
        timeout_seconds=timeout_seconds,
    )
    candidates = rank_candidates(enriched_items, section, max_candidates=args.max_candidates)
    candidates_path = save_candidates(run_id, candidates)
    log_step("2/6", f"候选完成：{len(candidates)} 条，保存 {candidates_path}")

    log_step("3/6", "准备 Claude 输入")
    prompt = build_issue_prompt(section, candidates)
    prompt_path = save_prompt(run_id, prompt)
    log_step("3/6", f"Prompt 保存：{prompt_path}")

    if args.from_ai_json:
        ai_output = load_ai_output(Path(args.from_ai_json))
        ai_run = None
        log_step("4/6", f"使用本地 AI JSON：{args.from_ai_json}")
    else:
        log_step("4/6", "调用 Claude 生成结构化日报 JSON")
        ai_output, ai_run = generate_issue_output(section, candidates, prompt=prompt)
        save_ai_run(run_id, ai_run)
        log_step("4/6", f"AI 输出完成：头条 {len(ai_output.headlines)} 条，速览 {len(ai_output.briefs)} 条")

    issue = make_issue(
        ai_output,
        section_slug=section.slug,
        publication_name=section.publication_name,
        issue_date=issue_date,
        volume=section.issue_volume,
        number=args.issue_number or next_issue_number(section.slug),
    )
    issue_path = save_issue(run_id, issue)
    log_step("5/6", f"日报结构保存：{issue_path}")

    if args.dry_run:
        print(f"Dry-run complete. Private snapshot: {run_dir(run_id)}")
        return 0

    outputs = build_frontend_app(issue)
    rendered_issue_path = outputs["issue"]
    log_step("5/6", f"前端应用与数据生成完成：{rendered_issue_path}")

    if store.enabled and not args.no_supabase:
        log_step("6/6", "同步 Supabase")
        store.upsert_sources(section)
        store.create_fetch_run(run_id, section, issue_date.isoformat())
        store.insert_raw_items(run_id, raw_items)
        store.insert_candidates(run_id, candidates)
        if ai_run:
            store.insert_ai_run(run_id, ai_run)
        store.insert_issue(run_id, issue)
        store.finish_fetch_run(run_id, status="success")
        log_step("6/6", "Supabase 同步完成")
    elif not args.no_supabase:
        print("Supabase env not configured; skipped cloud persistence.")
    else:
        log_step("6/6", "已按参数跳过 Supabase 同步")

    print(f"Run snapshot: {run_dir(run_id)}")
    print(f"Generated {rendered_issue_path}")
    print(f"Published index {DIST_DIR / 'index.html'}")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-news")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate one issue")
    generate_parser.add_argument("--section", default="tech")
    generate_parser.add_argument("--date")
    generate_parser.add_argument("--dry-run", action="store_true")
    generate_parser.add_argument("--no-supabase", action="store_true")
    generate_parser.add_argument("--per-source-limit", type=int, default=15)
    generate_parser.add_argument("--max-candidates", type=int, default=30)
    generate_parser.add_argument("--body-candidates", type=int, default=20)
    generate_parser.add_argument("--issue-number", type=int)
    generate_parser.add_argument("--from-ai-json", help="Use a saved AI JSON response instead of calling Claude")
    generate_parser.set_defaults(func=lambda args: asyncio.run(generate(args)))

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
    shortlist_parser.set_defaults(func=shortlist_mvp)

    shortlist_codex_parser = subparsers.add_parser("shortlist-codex", help="Checkpoint 2b: validate Codex rough shortlist JSON")
    shortlist_codex_parser.add_argument("--run-id", required=True)
    shortlist_codex_parser.set_defaults(func=shortlist_codex)

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

    compose_codex_parser = subparsers.add_parser("compose-codex", help="Checkpoint 5: validate Codex issue JSON")
    compose_codex_parser.add_argument("--run-id", required=True)
    compose_codex_parser.set_defaults(func=compose_codex)

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

    validate_parser = subparsers.add_parser("validate-config", help="Validate section config")
    validate_parser.set_defaults(func=validate_config)

    clean_parser = subparsers.add_parser("clean-dist", help="Remove public dist output")
    clean_parser.set_defaults(func=clean_dist)
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

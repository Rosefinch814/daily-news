from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from daily_news.models import (
    AIRunRecord,
    CandidateItem,
    CodexSelectionOutput,
    CodexShortlistOutput,
    Issue,
    RawItem,
)
from daily_news.paths import LOGS_DIR, RUNS_DIR
from daily_news.paths import PROFILES_DIR
from daily_news.scoring import dedupe_url_key, title_dedupe_hash


@dataclass(frozen=True)
class IssueHistory:
    urls: set[str]
    title_hashes: set[str]
    issue_ids: list[str]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def output_dir(run_id: str) -> Path:
    return run_dir(run_id) / "outputs"


def logs_dir(run_id: str) -> Path:
    return LOGS_DIR / run_id


def ai_logs_dir(run_id: str) -> Path:
    return logs_dir(run_id) / "ai"


def profile_dir(section_slug: str) -> Path:
    return PROFILES_DIR / section_slug


def default_profile_text(section_slug: str, name: str) -> str:
    title = {
        "taste.md": "选题口味档案",
        "style.md": "写作口味档案",
        "seed-suggestions.md": "关注清单待确认建议",
    }[name]
    return f"# {title} · {section_slug}\n\n暂无记录。\n"


def ensure_profile_files(section_slug: str) -> dict[str, Path]:
    directory = profile_dir(section_slug)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "taste": directory / "taste.md",
        "style": directory / "style.md",
        "seed_suggestions": directory / "seed-suggestions.md",
    }
    for key, path in paths.items():
        if not path.exists():
            filename = "seed-suggestions.md" if key == "seed_suggestions" else f"{key}.md"
            _write_text(path, default_profile_text(section_slug, filename))
    return paths


def load_profiles(section_slug: str) -> dict[str, str]:
    paths = ensure_profile_files(section_slug)
    return {key: path.read_text(encoding="utf-8") for key, path in paths.items()}


def snapshot_profiles(section_slug: str, snapshot_id: str) -> Path:
    paths = ensure_profile_files(section_slug)
    snapshot_dir = profile_dir(section_slug) / "history" / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for key, path in paths.items():
        filename = "seed-suggestions.md" if key == "seed_suggestions" else f"{key}.md"
        _write_text(snapshot_dir / filename, path.read_text(encoding="utf-8"))
    return snapshot_dir


def write_profiles(
    section_slug: str,
    *,
    taste_md: str,
    style_md: str,
    seed_suggestions_append: str = "",
) -> dict[str, Path]:
    paths = ensure_profile_files(section_slug)
    _write_text(paths["taste"], taste_md.rstrip() + "\n")
    _write_text(paths["style"], style_md.rstrip() + "\n")
    append_text = seed_suggestions_append.strip()
    if append_text:
        existing = paths["seed_suggestions"].read_text(encoding="utf-8").rstrip()
        _write_text(paths["seed_suggestions"], existing + "\n\n" + append_text + "\n")
    return paths


def output_path(run_id: str, filename: str) -> Path:
    return output_dir(run_id) / filename


def artifact_path(run_id: str, filename: str) -> Path:
    current_path = output_path(run_id, filename)
    if current_path.exists():
        return current_path
    legacy_path = run_dir(run_id) / filename
    if legacy_path.exists():
        return legacy_path
    return current_path


def save_raw_items(run_id: str, raw_items: list[RawItem]) -> Path:
    path = output_path(run_id, "01_raw_items.json")
    _write_json(path, [item.model_dump(mode="json") for item in raw_items])
    return path


def save_candidates(run_id: str, candidates: list[CandidateItem]) -> Path:
    path = output_path(run_id, "02_candidates.json")
    _write_json(path, [item.model_dump(mode="json") for item in candidates])
    return path


def save_enriched_candidates(run_id: str, candidates: list[CandidateItem]) -> Path:
    path = output_path(run_id, "03_enriched_candidates.json")
    _write_json(path, [item.model_dump(mode="json") for item in candidates])
    return path


def save_codex_shortlist(run_id: str, shortlist: CodexShortlistOutput) -> Path:
    path = output_path(run_id, "02_codex_shortlist.json")
    _write_json(path, shortlist.model_dump(mode="json"))
    return path


def save_selection(run_id: str, selection: CodexSelectionOutput) -> Path:
    path = output_path(run_id, "04_selection.json")
    _write_json(path, selection.model_dump(mode="json"))
    return path


def save_ai_task_run(
    run_id: str,
    stage: str,
    ai_run: AIRunRecord,
    *,
    save_attempts: bool = True,
    save_provider_events: bool = True,
    append_metrics_jsonl: bool = True,
) -> Path:
    base_dir = ai_logs_dir(run_id)
    _write_text(base_dir / f"{stage}_prompt.md", ai_run.prompt)
    _write_text(base_dir / f"{stage}_raw.txt", ai_run.raw_output)
    if ai_run.parsed_output is not None:
        _write_json(base_dir / f"{stage}_output.json", ai_run.parsed_output)
    provider_events = ai_run.provider_events
    event_log_path: str | None = None
    if save_provider_events and provider_events:
        event_log = base_dir / f"{stage}_provider_events.jsonl"
        _write_text(event_log, provider_events)
        event_log_path = event_log.name
    persisted = ai_run.model_copy(update={"provider_events": None, "provider_event_log": event_log_path})
    if save_attempts:
        _write_json(base_dir / f"{stage}_attempts.json", persisted.attempts)
    _write_json(base_dir / f"{stage}_run.json", persisted.model_dump(mode="json"))
    if append_metrics_jsonl:
        metrics = {
            "stage": stage,
            "task_type": persisted.task_type,
            "status": persisted.status,
            "provider": persisted.provider,
            "model": persisted.model,
            "attempt_count": persisted.attempt_count,
            "repair_used": persisted.repair_used,
            "duration_ms": persisted.duration_ms,
            "prompt_chars": persisted.prompt_chars,
            "raw_output_chars": persisted.raw_output_chars,
            "parsed_output_chars": persisted.parsed_output_chars,
            "input_tokens": persisted.input_tokens,
            "output_tokens": persisted.output_tokens,
            "cache_read_tokens": persisted.cache_read_tokens,
            "cache_write_tokens": persisted.cache_write_tokens,
            "total_tokens": persisted.total_tokens,
            "cost_usd": persisted.cost_usd,
            "error": persisted.error,
            "started_at": persisted.started_at.isoformat(),
            "finished_at": persisted.finished_at.isoformat(),
            "provider_event_log": persisted.provider_event_log,
        }
        _append_jsonl(logs_dir(run_id) / "ai_metrics.jsonl", metrics)
    return base_dir / f"{stage}_run.json"


def save_issue(run_id: str, issue: Issue) -> Path:
    path = output_path(run_id, "05_issue.json")
    _write_json(path, issue.model_dump(mode="json"))
    issues_dir = RUNS_DIR / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)
    _write_json(issues_dir / f"{issue.id}.json", issue.model_dump(mode="json"))
    return path


def load_raw_items(run_id: str) -> list[RawItem]:
    path = artifact_path(run_id, "01_raw_items.json")
    if not path.exists():
        raise FileNotFoundError(f"Raw items not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RawItem.model_validate(item) for item in payload]


def load_shortlist(run_id: str) -> list[CandidateItem]:
    path = artifact_path(run_id, "02_candidates.json")
    if not path.exists():
        raise FileNotFoundError(f"Local prefilter not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [CandidateItem.model_validate(item) for item in payload]


def load_codex_shortlist(run_id: str) -> CodexShortlistOutput:
    path = artifact_path(run_id, "02_codex_shortlist.json")
    if not path.exists():
        raise FileNotFoundError(f"Codex shortlist not found: {path}")
    return CodexShortlistOutput.model_validate_json(path.read_text(encoding="utf-8"))


def load_enriched_candidates(run_id: str) -> list[CandidateItem]:
    path = artifact_path(run_id, "03_enriched_candidates.json")
    if not path.exists():
        raise FileNotFoundError(f"Enriched candidates not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [CandidateItem.model_validate(item) for item in payload]


def load_selection(run_id: str) -> CodexSelectionOutput:
    path = artifact_path(run_id, "04_selection.json")
    if not path.exists():
        raise FileNotFoundError(f"Codex selection not found: {path}")
    return CodexSelectionOutput.model_validate_json(path.read_text(encoding="utf-8"))


def load_issue_from_run(run_id: str) -> Issue:
    path = artifact_path(run_id, "05_issue.json")
    if not path.exists():
        raise FileNotFoundError(f"Issue snapshot not found: {path}")
    return Issue.model_validate_json(path.read_text(encoding="utf-8"))


def save_run_snapshot(
    run_id: str,
    *,
    raw_items: list[RawItem],
    candidates: list[CandidateItem],
    ai_run: AIRunRecord | None = None,
    issue: Issue | None = None,
) -> Path:
    run_dir = RUNS_DIR / run_id
    _write_json(run_dir / "raw_items.json", [item.model_dump(mode="json") for item in raw_items])
    _write_json(run_dir / "candidates.json", [item.model_dump(mode="json") for item in candidates])
    if ai_run:
        _write_json(run_dir / "ai_run.json", ai_run.model_dump(mode="json"))
    if issue:
        _write_json(run_dir / "issue.json", issue.model_dump(mode="json"))
        issues_dir = RUNS_DIR / "issues"
        issues_dir.mkdir(parents=True, exist_ok=True)
        _write_json(issues_dir / f"{issue.id}.json", issue.model_dump(mode="json"))
    return run_dir


def load_issue(issue_id: str) -> Issue:
    path = RUNS_DIR / "issues" / f"{issue_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Local issue snapshot not found: {path}")
    return Issue.model_validate_json(path.read_text(encoding="utf-8"))


def load_recent_issue_history(
    *,
    section_slug: str,
    before_date: date,
    lookback_days: int,
    include_title_hashes: bool = True,
) -> IssueHistory:
    if lookback_days <= 0:
        return IssueHistory(urls=set(), title_hashes=set(), issue_ids=[])

    issues_dir = RUNS_DIR / "issues"
    if not issues_dir.exists():
        return IssueHistory(urls=set(), title_hashes=set(), issue_ids=[])

    start_date = before_date - timedelta(days=lookback_days)
    urls: set[str] = set()
    title_hashes: set[str] = set()
    issue_ids: list[str] = []
    for path in sorted(issues_dir.glob("*.json")):
        issue = Issue.model_validate_json(path.read_text(encoding="utf-8"))
        if issue.section_slug != section_slug:
            continue
        if not (start_date <= issue.issue_date < before_date):
            continue
        issue_ids.append(issue.id)
        for article in [*issue.headlines, *issue.briefs]:
            for source in article.sources:
                urls.add(dedupe_url_key(source.url))
            if include_title_hashes:
                title_hash = title_dedupe_hash(article.title_zh)
                if title_hash:
                    title_hashes.add(title_hash)
    return IssueHistory(urls=urls, title_hashes=title_hashes, issue_ids=issue_ids)

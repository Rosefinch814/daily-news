from __future__ import annotations

import json
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
from daily_news.paths import RUNS_DIR


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def save_raw_items(run_id: str, raw_items: list[RawItem]) -> Path:
    path = run_dir(run_id) / "01_raw_items.json"
    _write_json(path, [item.model_dump(mode="json") for item in raw_items])
    return path


def save_candidates(run_id: str, candidates: list[CandidateItem]) -> Path:
    path = run_dir(run_id) / "02_candidates.json"
    _write_json(path, [item.model_dump(mode="json") for item in candidates])
    return path


def save_enriched_candidates(run_id: str, candidates: list[CandidateItem]) -> Path:
    path = run_dir(run_id) / "03_enriched_candidates.json"
    _write_json(path, [item.model_dump(mode="json") for item in candidates])
    return path


def save_codex_shortlist(run_id: str, shortlist: CodexShortlistOutput) -> Path:
    path = run_dir(run_id) / "02_codex_shortlist.json"
    _write_json(path, shortlist.model_dump(mode="json"))
    return path


def save_prompt(run_id: str, prompt: str) -> Path:
    path = run_dir(run_id) / "03_prompt.md"
    _write_text(path, prompt)
    return path


def save_ai_run(run_id: str, ai_run: AIRunRecord) -> Path:
    base_dir = run_dir(run_id)
    _write_text(base_dir / "04_ai_raw.txt", ai_run.raw_output)
    if ai_run.parsed_output is not None:
        _write_json(base_dir / "04_ai_output.json", ai_run.parsed_output)
    _write_json(base_dir / "04_ai_run.json", ai_run.model_dump(mode="json"))
    return base_dir / "04_ai_run.json"


def save_issue(run_id: str, issue: Issue) -> Path:
    path = run_dir(run_id) / "05_issue.json"
    _write_json(path, issue.model_dump(mode="json"))
    issues_dir = RUNS_DIR / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)
    _write_json(issues_dir / f"{issue.id}.json", issue.model_dump(mode="json"))
    return path


def load_raw_items(run_id: str) -> list[RawItem]:
    path = run_dir(run_id) / "01_raw_items.json"
    if not path.exists():
        raise FileNotFoundError(f"Raw items not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RawItem.model_validate(item) for item in payload]


def load_shortlist(run_id: str) -> list[CandidateItem]:
    path = run_dir(run_id) / "02_candidates.json"
    if not path.exists():
        raise FileNotFoundError(f"Local prefilter not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [CandidateItem.model_validate(item) for item in payload]


def load_codex_shortlist(run_id: str) -> CodexShortlistOutput:
    path = run_dir(run_id) / "02_codex_shortlist.json"
    if not path.exists():
        raise FileNotFoundError(f"Codex shortlist not found: {path}")
    return CodexShortlistOutput.model_validate_json(path.read_text(encoding="utf-8"))


def load_enriched_candidates(run_id: str) -> list[CandidateItem]:
    path = run_dir(run_id) / "03_enriched_candidates.json"
    if not path.exists():
        raise FileNotFoundError(f"Enriched candidates not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [CandidateItem.model_validate(item) for item in payload]


def load_selection(run_id: str) -> CodexSelectionOutput:
    path = run_dir(run_id) / "04_selection.json"
    if not path.exists():
        raise FileNotFoundError(f"Codex selection not found: {path}")
    return CodexSelectionOutput.model_validate_json(path.read_text(encoding="utf-8"))


def load_issue_from_run(run_id: str) -> Issue:
    path = run_dir(run_id) / "05_issue.json"
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

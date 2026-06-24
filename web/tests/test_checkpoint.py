from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_news.main import (
    ai_compose,
    ai_select,
    ai_shortlist,
    ai_file_read_test,
    build_parser,
    merge_enriched_candidates,
    validate_selection_ids,
    validate_shortlist_ids,
)
from daily_news.ai_engine import ProviderRunResult
from daily_news.models import AIIssueOutput, CandidateItem, CodexSelectionOutput, CodexShortlistOutput, RawItem
from daily_news.models import AIRunRecord
from daily_news.storage import local as local_storage


def _candidate(item_id: str) -> CandidateItem:
    raw_item = RawItem(
        id=item_id,
        source_id="the_verge",
        source_name="The Verge",
        source_language="en",
        title=f"Title {item_id}",
        url=f"https://example.com/{item_id}",
        summary="英伟达 AI芯片",
        fetched_at=datetime.now(timezone.utc),
    )
    return CandidateItem(raw_item=raw_item, score=80, reason="命中关注")


def test_selection_fixture_validates_against_candidates() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample_selection.json"
    selection = CodexSelectionOutput.model_validate_json(fixture.read_text(encoding="utf-8"))

    validate_selection_ids(selection, [_candidate("item-1"), _candidate("item-2"), _candidate("item-3")])


def test_codex_shortlist_fixture_validates_against_candidates() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample_codex_shortlist.json"
    shortlist = CodexShortlistOutput.model_validate_json(fixture.read_text(encoding="utf-8"))

    validate_shortlist_ids(shortlist, [_candidate("item-1"), _candidate("item-2"), _candidate("item-3")])


def test_codex_shortlist_rejects_inconsistent_top_level_ids() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample_codex_shortlist.json"
    shortlist = CodexShortlistOutput.model_validate_json(fixture.read_text(encoding="utf-8"))
    broken = shortlist.model_copy(update={"drop_item_ids": []})

    with pytest.raises(ValueError, match="missing from top-level lists"):
        validate_shortlist_ids(broken, [_candidate("item-1"), _candidate("item-2"), _candidate("item-3")])


def test_merge_enriched_candidates_preserves_codex_selected_candidates() -> None:
    candidates = [_candidate("item-1"), _candidate("item-2")]
    enriched_raw = candidates[0].raw_item.model_copy(update={"content": "full text", "fetch_status": "content"})

    merged = merge_enriched_candidates(candidates, [enriched_raw])

    assert [candidate.raw_item.id for candidate in merged] == ["item-1", "item-2"]
    assert merged[0].raw_item.content == "full text"
    assert merged[1].raw_item.content == ""
    assert merged[0].score == candidates[0].score


def test_checkpoint_commands_are_registered() -> None:
    parser = build_parser()
    commands = [
        ["fetch-mvp"],
        ["shortlist-mvp", "--run-id", "tech-2026-06-23-000000"],
        ["shortlist-codex", "--run-id", "tech-2026-06-23-000000"],
        ["ai-shortlist", "--run-id", "tech-2026-06-23-000000", "--provider", "codex"],
        ["enrich-mvp", "--run-id", "tech-2026-06-23-000000"],
        ["select-codex", "--run-id", "tech-2026-06-23-000000"],
        ["ai-select", "--run-id", "tech-2026-06-23-000000", "--provider", "codex"],
        ["compose-codex", "--run-id", "tech-2026-06-23-000000"],
        ["ai-compose", "--run-id", "tech-2026-06-23-000000", "--provider", "codex"],
        ["ai-file-read-test", "--provider", "codex"],
        ["render-mvp", "--run-id", "tech-2026-06-23-000000"],
        ["sync", "--run-id", "tech-2026-06-23-000000"],
    ]
    for argv in commands:
        parsed = parser.parse_args(argv)
        assert parsed.command == argv[0]


def test_save_ai_task_run_writes_monitoring_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    record = AIRunRecord(
        task_type="semantic_shortlist",
        prompt_version="test",
        prompt="prompt",
        raw_output='{"ok": true}',
        parsed_output={"ok": True},
        status="success",
        started_at=now,
        finished_at=now,
        provider="codex",
        attempt_count=1,
        duration_ms=12,
        prompt_chars=6,
        raw_output_chars=12,
        total_tokens=42,
        attempts=[{"status": "success"}],
        provider_events='{"type":"usage"}\n',
    )

    local_storage.save_ai_task_run("run-1", "02_ai_shortlist", record)

    run_dir = tmp_path / "run-1"
    assert (run_dir / "02_ai_shortlist_run.json").exists()
    assert (run_dir / "02_ai_shortlist_attempts.json").exists()
    assert (run_dir / "02_ai_shortlist_provider_events.jsonl").exists()
    assert (run_dir / "ai_metrics.jsonl").exists()
    assert '"total_tokens": 42' in (run_dir / "ai_metrics.jsonl").read_text(encoding="utf-8")


def test_ai_file_read_test_writes_debug_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)

    output = {
        "keep_item_ids": ["debug-keep"],
        "maybe_item_ids": ["debug-maybe"],
        "drop_item_ids": ["debug-drop"],
        "items": [
            {
                "source_item_id": "debug-keep",
                "decision": "keep",
                "category": "AI 芯片",
                "relevance_score": 95,
                "importance_score": 92,
                "reason": "英伟达 AI 芯片进展，值得进入正文补全。",
                "is_aggregate": False,
                "aggregate_highlights": [],
            },
            {
                "source_item_id": "debug-maybe",
                "decision": "maybe",
                "category": "AI 产品",
                "relevance_score": 68,
                "importance_score": 52,
                "reason": "苹果 AI 功能相关，但信息量有限。",
                "is_aggregate": False,
                "aggregate_highlights": [],
            },
            {
                "source_item_id": "debug-drop",
                "decision": "drop",
                "category": "消费电子",
                "relevance_score": 8,
                "importance_score": 5,
                "reason": "耳机配色和促销信息，弱相关。",
                "is_aggregate": False,
                "aggregate_highlights": [],
            },
        ],
    }

    def fake_run_provider(*args, **kwargs) -> ProviderRunResult:  # noqa: ANN002, ANN003
        assert kwargs["use_output_schema"] is False
        return ProviderRunResult(
            output_text=CodexShortlistOutput.model_validate(output).model_dump_json(),
            stdout='{"type":"usage","total_tokens":123}\n',
            stderr="",
            command=["codex", "exec", "--json", "-"],
            return_code=0,
            duration_ms=23,
            model="fake-model",
            total_tokens=123,
            provider_events='{"type":"usage","total_tokens":123}\n',
        )

    monkeypatch.setattr("daily_news.main.run_provider", fake_run_provider)
    args = build_parser().parse_args(["ai-file-read-test", "--provider", "codex"])

    assert ai_file_read_test(args) == 0

    debug_dir = tmp_path / "_debug_ai_file_read"
    assert (debug_dir / "input.json").exists()
    assert (debug_dir / "output.json").exists()
    assert (debug_dir / "raw.txt").exists()
    assert (debug_dir / "run.json").exists()
    assert (debug_dir / "provider_events.jsonl").exists()
    run_payload = (debug_dir / "run.json").read_text(encoding="utf-8")
    assert '"status": "success"' in run_payload
    assert '"total_tokens": 123' in run_payload


def test_ai_shortlist_reads_candidate_file_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    run_id = "tech-2026-06-24-101218"
    candidates = [_candidate(f"item-{index}") for index in range(60)]
    local_storage.save_candidates(run_id, candidates)

    output = {
        "keep_item_ids": [candidate.raw_item.id for candidate in candidates[:20]],
        "maybe_item_ids": [candidate.raw_item.id for candidate in candidates[20:40]],
        "drop_item_ids": [candidate.raw_item.id for candidate in candidates[40:]],
        "items": [
            {
                "source_item_id": candidate.raw_item.id,
                "decision": "keep" if index < 20 else "maybe" if index < 40 else "drop",
                "category": "AI 芯片",
                "relevance_score": 80,
                "importance_score": 70,
                "reason": "测试输出覆盖全部候选。",
                "is_aggregate": False,
                "aggregate_highlights": [],
            }
            for index, candidate in enumerate(candidates)
        ],
    }
    shortlist = CodexShortlistOutput.model_validate(output)

    def fake_run_ai_task(*, prompt: str, use_output_schema: bool, **kwargs):  # noqa: ANN003
        assert use_output_schema is False
        assert str((tmp_path / run_id / "02_candidates.json").resolve()) in prompt
        assert "Title item-0" not in prompt
        now = datetime.now(timezone.utc)
        return shortlist, AIRunRecord(
            task_type="semantic_shortlist",
            prompt_version="test",
            prompt=prompt,
            raw_output=shortlist.model_dump_json(),
            parsed_output=shortlist.model_dump(mode="json"),
            status="success",
            started_at=now,
            finished_at=now,
            provider="codex",
            attempt_count=1,
            duration_ms=10,
            prompt_chars=len(prompt),
            raw_output_chars=len(shortlist.model_dump_json()),
        )

    monkeypatch.setattr("daily_news.main.run_ai_task", fake_run_ai_task)
    args = build_parser().parse_args(["ai-shortlist", "--run-id", run_id, "--provider", "codex"])

    assert ai_shortlist(args) == 0

    run_dir = tmp_path / run_id
    assert (run_dir / "02_codex_shortlist.json").exists()
    assert (run_dir / "02_ai_shortlist_prompt.md").exists()
    saved = CodexShortlistOutput.model_validate_json((run_dir / "02_codex_shortlist.json").read_text(encoding="utf-8"))
    assert len(saved.items) == 60


def test_ai_shortlist_validation_failure_persists_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    run_id = "tech-2026-06-24-101218"
    candidates = [_candidate("item-1"), _candidate("item-2")]
    local_storage.save_candidates(run_id, candidates)
    broken = CodexShortlistOutput.model_validate(
        {
            "keep_item_ids": ["item-1"],
            "maybe_item_ids": ["item-2"],
            "drop_item_ids": [],
            "items": [
                {
                    "source_item_id": "item-1",
                    "decision": "keep",
                    "category": "AI 芯片",
                    "relevance_score": 80,
                    "importance_score": 70,
                    "reason": "测试输出缺少 item-2 明细。",
                    "is_aggregate": False,
                    "aggregate_highlights": [],
                }
            ],
        }
    )

    def fake_run_ai_task(*, prompt: str, **kwargs):  # noqa: ANN003
        now = datetime.now(timezone.utc)
        return broken, AIRunRecord(
            task_type="semantic_shortlist",
            prompt_version="test",
            prompt=prompt,
            raw_output=broken.model_dump_json(),
            parsed_output=broken.model_dump(mode="json"),
            status="success",
            started_at=now,
            finished_at=now,
            provider="claude",
            attempt_count=1,
            duration_ms=10,
            prompt_chars=len(prompt),
            raw_output_chars=len(broken.model_dump_json()),
        )

    monkeypatch.setattr("daily_news.main.run_ai_task", fake_run_ai_task)
    args = build_parser().parse_args(["ai-shortlist", "--run-id", run_id, "--provider", "claude"])

    with pytest.raises(ValueError, match="missing from items: item-2"):
        ai_shortlist(args)

    run_dir = tmp_path / run_id
    assert not (run_dir / "02_codex_shortlist.json").exists()
    assert (run_dir / "02_ai_shortlist_run.json").exists()
    assert (run_dir / "02_ai_shortlist_raw.txt").exists()
    run_payload = (run_dir / "02_ai_shortlist_run.json").read_text(encoding="utf-8")
    assert '"status": "failed"' in run_payload
    assert "missing from items: item-2" in run_payload


def test_ai_select_reads_enriched_candidate_file_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    run_id = "tech-2026-06-24-101218-select-claude"
    candidates = [_candidate(f"item-{index}") for index in range(3)]
    local_storage.save_enriched_candidates(run_id, candidates)
    selection = CodexSelectionOutput(
        headline_item_ids=["item-0"],
        brief_item_ids=["item-1"],
        headlines=[
            {
                "source_item_ids": ["item-0"],
                "relevance_score": 90,
                "importance_score": 80,
                "reason": "头条理由",
            }
        ],
        briefs=[
            {
                "source_item_ids": ["item-1"],
                "relevance_score": 70,
                "importance_score": 60,
                "reason": "速览理由",
            }
        ],
        discarded=[
            {
                "source_item_ids": ["item-2"],
                "reason": "丢弃理由",
                "relevance_score": 10,
                "importance_score": 20,
            }
        ],
        merged_sources=[],
    )

    def fake_run_ai_task(*, prompt: str, use_output_schema: bool, **kwargs):  # noqa: ANN003
        assert use_output_schema is False
        assert str((tmp_path / run_id / "03_enriched_candidates.json").resolve()) in prompt
        assert "Title item-0" not in prompt
        now = datetime.now(timezone.utc)
        return selection, AIRunRecord(
            task_type="selection",
            prompt_version="test",
            prompt=prompt,
            raw_output=selection.model_dump_json(),
            parsed_output=selection.model_dump(mode="json"),
            status="success",
            started_at=now,
            finished_at=now,
            provider="claude",
            attempt_count=1,
            duration_ms=10,
            prompt_chars=len(prompt),
            raw_output_chars=len(selection.model_dump_json()),
        )

    monkeypatch.setattr("daily_news.main.run_ai_task", fake_run_ai_task)
    args = build_parser().parse_args(["ai-select", "--run-id", run_id, "--provider", "claude"])

    assert ai_select(args) == 0

    run_dir = tmp_path / run_id
    assert (run_dir / "04_selection.json").exists()
    assert (run_dir / "04_ai_selection_prompt.md").exists()


def test_ai_compose_reads_selection_and_enriched_file_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    run_id = "tech-2026-06-24-101218"
    candidates = [_candidate("item-1"), _candidate("item-2")]
    local_storage.save_enriched_candidates(run_id, candidates)
    selection = CodexSelectionOutput(
        headline_item_ids=["item-1"],
        brief_item_ids=["item-2"],
        headlines=[
            {
                "source_item_ids": ["item-1"],
                "relevance_score": 90,
                "importance_score": 80,
                "reason": "头条理由",
            }
        ],
        briefs=[
            {
                "source_item_ids": ["item-2"],
                "relevance_score": 70,
                "importance_score": 60,
                "reason": "速览理由",
            }
        ],
        discarded=[],
        merged_sources=[],
    )
    local_storage.save_selection(run_id, selection)
    issue_output = AIIssueOutput.model_validate(
        {
            "headlines": [
                {
                    "source_item_ids": ["item-1"],
                    "kicker": "芯片 · 发布",
                    "title_zh": "英伟达 AI 芯片新闻",
                    "summary_zh": "英伟达发布面向数据中心训练的 AI 芯片。",
                    "read_body_zh": ["英伟达表示，新芯片面向数据中心训练负载。"],
                    "pullquote": None,
                    "ai_impact": "这会影响 AI 训练算力供给。",
                    "sources": [{"name": "The Verge", "url": "https://example.com/item-1"}],
                    "relevance_score": 90,
                    "importance_score": 80,
                }
            ],
            "briefs": [
                {
                    "source_item_ids": ["item-2"],
                    "title_zh": "另一条芯片速览",
                    "summary_zh": "另一条候选进入速览。",
                    "sources": [{"name": "The Verge", "url": "https://example.com/item-2"}],
                    "relevance_score": 70,
                    "importance_score": 60,
                }
            ],
            "discarded": [],
            "merged_sources": [],
        }
    )

    def fake_run_ai_task(*, prompt: str, use_output_schema: bool, **kwargs):  # noqa: ANN003
        assert use_output_schema is False
        assert str((tmp_path / run_id / "04_selection.json").resolve()) in prompt
        assert str((tmp_path / run_id / "03_enriched_candidates.json").resolve()) in prompt
        assert "Title item-1" not in prompt
        now = datetime.now(timezone.utc)
        return issue_output, AIRunRecord(
            task_type="issue_compose",
            prompt_version="test",
            prompt=prompt,
            raw_output=issue_output.model_dump_json(),
            parsed_output=issue_output.model_dump(mode="json"),
            status="success",
            started_at=now,
            finished_at=now,
            provider="claude",
            attempt_count=1,
            duration_ms=10,
            prompt_chars=len(prompt),
            raw_output_chars=len(issue_output.model_dump_json()),
        )

    monkeypatch.setattr("daily_news.main.run_ai_task", fake_run_ai_task)
    args = build_parser().parse_args(["ai-compose", "--run-id", run_id, "--provider", "claude"])

    assert ai_compose(args) == 0

    run_dir = tmp_path / run_id
    assert (run_dir / "05_issue.json").exists()
    assert (run_dir / "05_ai_issue_prompt.md").exists()

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_news.main import (
    ai_compose,
    ai_select,
    ai_shortlist,
    ai_file_read_test,
    build_parser,
    clean_run,
    digest_feedback,
    make_issue,
    merge_enriched_candidates,
    resolve_stage_provider,
    run_pipeline,
    temporary_feedback_mode,
    validate_selection_ids,
    validate_shortlist_ids,
)
from daily_news.ai_engine import ProviderRunResult
from daily_news.config import PipelineConfig
from daily_news.models import (
    AIIssueOutput,
    CandidateItem,
    CodexSelectionOutput,
    CodexShortlistOutput,
    DigestFeedbackOutput,
    RawItem,
)
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
        ["run-pipeline", "--date", "2026-06-24", "--ai-compose-provider", "claude", "--render-owner"],
        ["digest-feedback", "--section", "tech", "--provider", "codex"],
        ["clean-run", "--run-id", "tech-2026-06-23-000000", "--pack-logs"],
    ]
    for argv in commands:
        parsed = parser.parse_args(argv)
        assert parsed.command == argv[0]


def test_temporary_feedback_mode_restores_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEEDBACK_MODE", "owner")

    with temporary_feedback_mode("reader"):
        assert os.environ["FEEDBACK_MODE"] == "reader"

    assert os.environ["FEEDBACK_MODE"] == "owner"


def test_temporary_feedback_mode_removes_missing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEEDBACK_MODE", raising=False)

    with temporary_feedback_mode("owner"):
        assert os.environ["FEEDBACK_MODE"] == "owner"

    assert "FEEDBACK_MODE" not in os.environ


def test_save_ai_task_run_writes_monitoring_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
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

    ai_logs_dir = tmp_path / "logs" / "run-1" / "ai"
    assert (ai_logs_dir / "02_ai_shortlist_run.json").exists()
    assert (ai_logs_dir / "02_ai_shortlist_attempts.json").exists()
    assert (ai_logs_dir / "02_ai_shortlist_provider_events.jsonl").exists()
    assert (tmp_path / "logs" / "run-1" / "ai_metrics.jsonl").exists()
    assert '"total_tokens": 42' in (tmp_path / "logs" / "run-1" / "ai_metrics.jsonl").read_text(encoding="utf-8")


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
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
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
        assert str((tmp_path / run_id / "outputs" / "02_candidates.json").resolve()) in prompt
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

    output_dir = tmp_path / run_id / "outputs"
    log_dir = tmp_path / "logs" / run_id / "ai"
    assert (output_dir / "02_codex_shortlist.json").exists()
    assert (log_dir / "02_ai_shortlist_prompt.md").exists()
    saved = CodexShortlistOutput.model_validate_json((output_dir / "02_codex_shortlist.json").read_text(encoding="utf-8"))
    assert len(saved.items) == 60


def test_ai_shortlist_validation_failure_persists_debug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
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

    output_dir = tmp_path / run_id / "outputs"
    log_dir = tmp_path / "logs" / run_id / "ai"
    assert not (output_dir / "02_codex_shortlist.json").exists()
    assert (log_dir / "02_ai_shortlist_run.json").exists()
    assert (log_dir / "02_ai_shortlist_raw.txt").exists()
    run_payload = (log_dir / "02_ai_shortlist_run.json").read_text(encoding="utf-8")
    assert '"status": "failed"' in run_payload
    assert "missing from items: item-2" in run_payload


def test_ai_select_reads_enriched_candidate_file_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
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
        assert str((tmp_path / run_id / "outputs" / "03_enriched_candidates.json").resolve()) in prompt
        assert str((tmp_path / run_id / "outputs" / "04_history_index.json").resolve()) in prompt
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

    assert (tmp_path / run_id / "outputs" / "04_selection.json").exists()
    assert (tmp_path / run_id / "outputs" / "04_history_index.json").exists()
    assert (tmp_path / "logs" / run_id / "ai" / "04_ai_selection_prompt.md").exists()


def test_ai_compose_reads_selection_and_enriched_file_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
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
        assert str((tmp_path / run_id / "outputs" / "04_selection.json").resolve()) in prompt
        assert str((tmp_path / run_id / "outputs" / "03_enriched_candidates.json").resolve()) in prompt
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

    assert (tmp_path / run_id / "outputs" / "05_issue.json").exists()
    assert (tmp_path / "logs" / run_id / "ai" / "05_ai_issue_prompt.md").exists()


def test_stage_provider_priority() -> None:
    config = PipelineConfig()
    config.ai.default_provider = "claude"
    config.ai.stage_providers["selection"] = "codex"

    assert resolve_stage_provider(config, "semantic_shortlist") == "claude"
    assert resolve_stage_provider(config, "selection") == "codex"
    assert resolve_stage_provider(config, "selection", "claude") == "claude"


def test_digest_feedback_writes_profiles_and_marks_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNER_FEEDBACK_TOKEN", "owner-secret")
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(local_storage, "PROFILES_DIR", tmp_path / "profiles")
    fixture = Path(__file__).parent / "fixtures" / "sample_ai_output.json"
    issue_output = AIIssueOutput.model_validate_json(fixture.read_text(encoding="utf-8"))
    issue = make_issue(
        issue_output,
        section_slug="tech",
        publication_name="我的日报·科技",
        issue_date=datetime(2026, 6, 23, tzinfo=timezone.utc).date(),
        volume=1,
        number=7,
    )
    local_storage.save_issue("run-1", issue)

    class FakeStore:
        def __init__(self) -> None:
            self.marked: list[str] = []
            self.owner_token: str | None = None

        def fetch_undigested_feedback(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.owner_token = kwargs.get("owner_token")
            return [
                {
                    "id": "fb-1",
                    "issue_id": issue.id,
                    "issue_date": "2026-06-23",
                    "section_slug": "tech",
                    "scope": "article",
                    "article_level": "headline",
                    "article_index": 1,
                    "source_item_ids": issue.headlines[0].source_item_ids,
                    "signal": "up",
                    "note": None,
                    "created_at": "2026-06-24T00:00:00+00:00",
                },
                {
                    "id": "fb-2",
                    "issue_id": issue.id,
                    "issue_date": "2026-06-23",
                    "section_slug": "tech",
                    "scope": "article",
                    "article_level": "headline",
                    "article_index": 1,
                    "source_item_ids": issue.headlines[0].source_item_ids,
                    "signal": "up",
                    "note": "多看这类 AI 芯片供应链新闻",
                    "created_at": "2026-06-24T00:01:00+00:00",
                },
            ]

        def mark_feedback_digested(self, ids):  # noqa: ANN001
            self.marked.extend(ids)

    fake_store = FakeStore()
    monkeypatch.setattr("daily_news.main.SupabaseStore.from_env", lambda: fake_store)

    digest_output = DigestFeedbackOutput(
        taste_md="# 选题口味档案 · tech\n\n- 多看 AI 芯片供应链新闻。\n",
        style_md="# 写作口味档案 · tech\n\n- 保持专业简报体。\n",
        seed_suggestions_append="- 建议确认：是否把 AI 芯片供应链加入想看主题。",
        changes=["提高 AI 芯片供应链权重"],
    )

    def fake_run_ai_task(*, prompt: str, use_output_schema: bool, **kwargs):  # noqa: ANN003
        assert use_output_schema is False
        assert "digest_feedback_input.json" in prompt
        now = datetime.now(timezone.utc)
        return digest_output, AIRunRecord(
            task_type="digest_feedback",
            prompt_version="test",
            prompt=prompt,
            raw_output=digest_output.model_dump_json(),
            parsed_output=digest_output.model_dump(mode="json"),
            status="success",
            started_at=now,
            finished_at=now,
            provider="codex",
            attempt_count=1,
            duration_ms=10,
            prompt_chars=len(prompt),
            raw_output_chars=len(digest_output.model_dump_json()),
        )

    monkeypatch.setattr("daily_news.main.run_ai_task", fake_run_ai_task)
    args = build_parser().parse_args(
        ["digest-feedback", "--section", "tech", "--run-id", "digest-test", "--provider", "codex"]
    )

    assert digest_feedback(args) == 0
    assert fake_store.owner_token == "owner-secret"
    assert fake_store.marked == ["fb-1", "fb-2"]
    assert "AI 芯片供应链" in (tmp_path / "profiles" / "tech" / "taste.md").read_text(encoding="utf-8")
    assert "专业简报体" in (tmp_path / "profiles" / "tech" / "style.md").read_text(encoding="utf-8")
    assert "建议确认" in (tmp_path / "profiles" / "tech" / "seed-suggestions.md").read_text(encoding="utf-8")
    assert (tmp_path / "profiles" / "tech" / "history" / "digest-test" / "taste.md").exists()
    assert (tmp_path / "logs" / "digest-test" / "digest_feedback_input.json").exists()
    profile_update = json.loads((tmp_path / "logs" / "digest-test" / "profile_update.json").read_text(encoding="utf-8"))
    assert profile_update["snapshot_dir"].endswith("profiles/tech/history/digest-test")
    assert profile_update["limits"]["taste_chars_max"] == 6000
    assert (tmp_path / "logs" / "digest-test" / "ai" / "06_ai_digest_run.json").exists()


def test_digest_feedback_skips_without_owner_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OWNER_FEEDBACK_TOKEN", "")

    class FakeStore:
        def fetch_undigested_feedback(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("digest must not read feedback without OWNER_FEEDBACK_TOKEN")

    monkeypatch.setattr("daily_news.main.SupabaseStore.from_env", lambda: FakeStore())
    args = build_parser().parse_args(["digest-feedback", "--section", "tech", "--provider", "codex"])

    assert digest_feedback(args) == 0
    assert "OWNER_FEEDBACK_TOKEN is not configured" in capsys.readouterr().out


def test_load_recent_issue_history_filters_by_window_and_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path / "runs")
    fixture = Path(__file__).parent / "fixtures" / "sample_ai_output.json"
    issue_output = AIIssueOutput.model_validate_json(fixture.read_text(encoding="utf-8"))
    in_window = make_issue(
        issue_output,
        section_slug="tech",
        publication_name="我的日报·科技",
        issue_date=datetime(2026, 6, 23, tzinfo=timezone.utc).date(),
        volume=1,
        number=7,
    )
    outside_window = make_issue(
        issue_output,
        section_slug="tech",
        publication_name="我的日报·科技",
        issue_date=datetime(2026, 6, 22, tzinfo=timezone.utc).date(),
        volume=1,
        number=6,
    )
    other_section = make_issue(
        issue_output,
        section_slug="finance",
        publication_name="我的日报·财经",
        issue_date=datetime(2026, 6, 24, tzinfo=timezone.utc).date(),
        volume=1,
        number=1,
    )
    local_storage.save_issue("run-in-window", in_window)
    local_storage.save_issue("run-outside-window", outside_window)
    local_storage.save_issue("run-other-section", other_section)

    history = local_storage.load_recent_issue_history(
        section_slug="tech",
        before_date=datetime(2026, 6, 30, tzinfo=timezone.utc).date(),
        lookback_days=7,
        include_title_hashes=True,
    )

    assert history.issue_ids == [in_window.id]
    assert "https://example.com/nvidia" in history.urls
    assert history.title_hashes


def test_load_recent_issue_selection_index_is_bounded_and_lightweight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path / "runs")
    fixture = Path(__file__).parent / "fixtures" / "sample_ai_output.json"
    issue_output = AIIssueOutput.model_validate_json(fixture.read_text(encoding="utf-8"))
    in_window = make_issue(
        issue_output,
        section_slug="tech",
        publication_name="我的日报·科技",
        issue_date=datetime(2026, 6, 29, tzinfo=timezone.utc).date(),
        volume=1,
        number=8,
    )
    outside_window = make_issue(
        issue_output,
        section_slug="tech",
        publication_name="我的日报·科技",
        issue_date=datetime(2026, 6, 20, tzinfo=timezone.utc).date(),
        volume=1,
        number=7,
    )
    local_storage.save_issue("run-in-window", in_window)
    local_storage.save_issue("run-outside-window", outside_window)

    index = local_storage.load_recent_issue_selection_index(
        section_slug="tech",
        before_date=datetime(2026, 7, 1, tzinfo=timezone.utc).date(),
        lookback_days=3,
        max_items=2,
    )

    assert len(index) == 2
    assert index[0]["issue_id"] == in_window.id
    assert index[0]["level"] == "headline"
    assert "title_zh" in index[0]
    assert "source_urls" in index[0]
    assert "summary_zh" not in index[0]
    assert "read_body_zh" not in index[0]


def test_run_pipeline_stops_after_ai_shortlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
    run_id = "tech-2026-06-24-101218"

    async def fake_fetch_section_items(*args, **kwargs):  # noqa: ANN002, ANN003
        return [
            RawItem(
                id=f"item-{index}",
                source_id="the_verge",
                source_name="The Verge",
                source_language="en",
                title=f"Nvidia AI chip news {index}",
                url=f"https://example.com/{index}",
                summary="Nvidia releases AI chip for data centers.",
                fetched_at=datetime.now(timezone.utc),
            )
            for index in range(3)
        ]

    def fake_run_ai_shortlist_stage(*, run_id: str, provider: str, **kwargs):  # noqa: ANN003
        candidates = local_storage.load_shortlist(run_id)
        output = CodexShortlistOutput(
            keep_item_ids=[candidates[0].raw_item.id],
            maybe_item_ids=[candidates[1].raw_item.id],
            drop_item_ids=[candidates[2].raw_item.id],
            items=[
                {
                    "source_item_id": candidate.raw_item.id,
                    "decision": "keep" if index == 0 else "maybe" if index == 1 else "drop",
                    "category": "AI 芯片",
                    "relevance_score": 80,
                    "importance_score": 70,
                    "reason": "测试",
                    "is_aggregate": False,
                    "aggregate_highlights": [],
                }
                for index, candidate in enumerate(candidates)
            ],
        )
        saved_output = local_storage.save_codex_shortlist(run_id, output)
        debug_path = local_storage.ai_logs_dir(run_id) / "02_ai_shortlist_run.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text("{}", encoding="utf-8")
        assert provider == "codex"
        return output, saved_output, debug_path

    monkeypatch.setattr("daily_news.main.fetch_section_items", fake_fetch_section_items)
    monkeypatch.setattr("daily_news.main.run_ai_shortlist_stage", fake_run_ai_shortlist_stage)

    args = build_parser().parse_args(
        [
            "run-pipeline",
            "--section",
            "tech",
            "--date",
            "2026-06-24",
            "--run-id",
            run_id,
            "--stop-after",
            "ai_shortlist",
            "--ai-shortlist-provider",
            "codex",
        ]
    )

    assert asyncio.run(run_pipeline(args)) == 0

    outputs_dir = tmp_path / "runs" / run_id / "outputs"
    logs_dir = tmp_path / "logs" / run_id
    assert (outputs_dir / "01_raw_items.json").exists()
    assert (outputs_dir / "02_candidates.json").exists()
    assert (outputs_dir / "02_codex_shortlist.json").exists()
    assert not (outputs_dir / "03_enriched_candidates.json").exists()
    pipeline_payload = (logs_dir / "pipeline.json").read_text(encoding="utf-8")
    assert '"status": "stopped"' in pipeline_payload
    assert (logs_dir / "stages" / "ai_shortlist.json").exists()


def test_clean_run_packs_logs_without_deleting_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
    run_id = "tech-2026-06-24-101218"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    outputs_dir = run_dir / "outputs"
    (run_dir / "05_issue.json").write_text("{}", encoding="utf-8")
    (run_dir / "05_ai_issue_raw.txt").write_text("legacy raw", encoding="utf-8")
    (tmp_path / "logs" / run_id).mkdir(parents=True)
    (tmp_path / "logs" / run_id / "pipeline.log").write_text("ok", encoding="utf-8")

    args = build_parser().parse_args(["clean-run", "--run-id", run_id, "--pack-logs"])

    assert clean_run(args) == 0
    assert (outputs_dir / "05_issue.json").exists()
    assert not (run_dir / "05_issue.json").exists()
    assert not (run_dir / "05_ai_issue_raw.txt").exists()
    assert (tmp_path / "logs" / run_id / "legacy" / "05_ai_issue_raw.txt").exists()
    assert (tmp_path / "logs" / run_id / "archive" / f"{run_id}-logs.tar.gz").exists()

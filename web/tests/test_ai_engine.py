import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_news.ai_engine import (
    ProviderRunResult,
    _provider_result_from_claude,
    _provider_result_from_codex,
    build_issue_file_prompt,
    build_provider_command,
    build_selection_file_prompt,
    build_selection_prompt,
    build_shortlist_prompt,
    extract_json_object,
    run_ai_task,
)
from subprocess import CompletedProcess
from daily_news.config import PipelineConfig, load_section
from daily_news.models import AIIssueOutput, CandidateItem, CodexSelectionOutput, CodexShortlistOutput, RawItem


def test_extract_json_object_from_plain_json() -> None:
    payload = {"headlines": [], "briefs": [], "discarded": [], "merged_sources": []}

    assert extract_json_object(json.dumps(payload)) == payload


def test_validate_sample_ai_output() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample_ai_output.json"
    output = AIIssueOutput.model_validate_json(fixture.read_text(encoding="utf-8"))

    assert output.headlines[0].title_zh == "英伟达发布新一代 AI 芯片 Rubin"
    assert output.briefs[0].relevance_score == 82


def _candidate(item_id: str) -> CandidateItem:
    raw_item = RawItem(
        id=item_id,
        source_id="the_verge",
        source_name="The Verge",
        source_language="en",
        title=f"Nvidia AI chip news {item_id}",
        url=f"https://example.com/{item_id}",
        summary="Nvidia releases AI chip for data centers.",
        content="Nvidia said the new AI chip targets data center training workloads.",
        fetched_at=datetime.now(timezone.utc),
    )
    return CandidateItem(raw_item=raw_item, score=80, reason="命中英伟达和 AI 芯片")


def test_build_ai_prompts_include_candidates_and_interests() -> None:
    section = load_section("tech")
    config = PipelineConfig()
    candidates = [_candidate("item-1")]

    shortlist_prompt = build_shortlist_prompt(section, candidates, config)
    selection_prompt = build_selection_prompt(section, candidates, config)

    assert "keep_item_ids" in shortlist_prompt
    assert "Nvidia AI chip news item-1" in shortlist_prompt
    assert "英伟达" in shortlist_prompt
    assert "headline_item_ids" in selection_prompt
    assert "同一事件多源报道必须合并" in selection_prompt


def test_build_selection_file_prompt_uses_path_without_candidates() -> None:
    section = load_section("tech")
    prompt = build_selection_file_prompt(section, Path("/tmp/03_enriched_candidates.json"))

    assert "/tmp/03_enriched_candidates.json" in prompt
    assert "headline_item_ids" in prompt
    assert "同一事件多源报道必须合并" in prompt
    assert "Nvidia AI chip news" not in prompt


def test_build_issue_file_prompt_uses_paths_without_candidates() -> None:
    section = load_section("tech")
    prompt = build_issue_file_prompt(
        section,
        Path("/tmp/04_selection.json"),
        Path("/tmp/03_enriched_candidates.json"),
    )

    assert "/tmp/04_selection.json" in prompt
    assert "/tmp/03_enriched_candidates.json" in prompt
    assert "ai_impact" in prompt
    assert "Nvidia AI chip news" not in prompt


def test_build_codex_provider_command_is_read_only() -> None:
    config = PipelineConfig()
    command = build_provider_command(
        "codex",
        config,
        schema_path=Path("/tmp/schema.json"),
        output_path=Path("/tmp/last-message.txt"),
    )

    assert command[:2] == ["codex", "exec"]
    assert "--sandbox" in command
    assert "read-only" in command
    assert "--json" in command
    assert "--output-schema" in command
    assert "--output-last-message" in command
    assert command[-1] == "-"


def test_build_claude_provider_command_supports_model_and_budget() -> None:
    config = PipelineConfig()
    config.ai.claude.model = "claude-test"
    config.ai.claude.max_budget_usd = 0.25

    command = build_provider_command("claude", config)

    assert command[:2] == ["claude", "-p"]
    assert "--output-format" in command
    assert "json" in command
    assert command[-4:] == ["--model", "claude-test", "--max-budget-usd", "0.25"]


def test_run_ai_task_repairs_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PipelineConfig()
    config.ai.repair_attempts = 1
    fixture = Path(__file__).parent / "fixtures" / "sample_codex_shortlist.json"
    valid_output = fixture.read_text(encoding="utf-8")
    calls = iter(["not json", valid_output])

    def fake_run_provider(*_: object, **__: object) -> ProviderRunResult:
        output = next(calls)
        return ProviderRunResult(
            output_text=output,
            stdout=output,
            stderr="",
            command=["fake-ai"],
            return_code=0,
            duration_ms=10,
            model="fake-model",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )

    monkeypatch.setattr("daily_news.ai_engine.run_provider", fake_run_provider)

    output, record = run_ai_task(
        task_type="semantic_shortlist",
        prompt="prompt",
        output_model=CodexShortlistOutput,
        provider="codex",
        config=config,
    )

    assert output.keep_item_ids == ["item-1"]
    assert record.task_type == "semantic_shortlist_repair"
    assert record.provider == "codex"
    assert record.model == "fake-model"
    assert record.attempt_count == 2
    assert record.repair_used is True
    assert record.input_tokens == 100
    assert record.total_tokens == 120
    assert len(record.attempts) == 2


def test_claude_json_envelope_usage_is_parsed() -> None:
    stdout = json.dumps(
        {
            "result": '{"ok": true}',
            "model": "claude-test",
            "duration_ms": 1234,
            "total_cost_usd": 0.02,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        }
    )
    completed = CompletedProcess(args=["claude"], returncode=0, stdout=stdout, stderr="")

    result = _provider_result_from_claude(completed=completed, command=["claude"], duration_ms=2000)

    assert result.output_text == '{"ok": true}'
    assert result.model == "claude-test"
    assert result.duration_ms == 1234
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.cache_read_tokens == 3
    assert result.cache_write_tokens == 2
    assert result.total_tokens == 20
    assert result.cost_usd == 0.02


def test_claude_structured_output_is_preferred() -> None:
    structured_output = {
        "headline_item_ids": ["item-1"],
        "brief_item_ids": ["item-2"],
        "headlines": [
            {
                "source_item_ids": ["item-1"],
                "relevance_score": 90,
                "importance_score": 80,
                "reason": "头条理由",
            }
        ],
        "briefs": [
            {
                "source_item_ids": ["item-2"],
                "relevance_score": 70,
                "importance_score": 60,
                "reason": "速览理由",
            }
        ],
        "discarded": [],
        "merged_sources": [],
    }
    stdout = json.dumps(
        {
            "result": "已输出合法 JSON。选题与分层结果：",
            "structured_output": structured_output,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
        ensure_ascii=False,
    )
    completed = CompletedProcess(args=["claude"], returncode=0, stdout=stdout, stderr="")

    result = _provider_result_from_claude(completed=completed, command=["claude"], duration_ms=2000)
    parsed = CodexSelectionOutput.model_validate_json(result.output_text)

    assert parsed.headline_item_ids == ["item-1"]
    assert result.extra["used_structured_output"] is True


def test_codex_jsonl_events_usage_is_optional() -> None:
    stdout = "\n".join(
        [
            json.dumps({"type": "session", "model": "gpt-test"}),
            json.dumps({"type": "usage", "usage": {"input_tokens": 11, "output_tokens": 7}}),
        ]
    )
    completed = CompletedProcess(args=["codex"], returncode=0, stdout=stdout, stderr="")

    result = _provider_result_from_codex(
        output_text='{"ok": true}',
        completed=completed,
        command=["codex"],
        duration_ms=300,
    )

    assert result.output_text == '{"ok": true}'
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert result.total_tokens == 18
    assert result.extra["event_count"] == 2

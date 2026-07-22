import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_news.ai_engine import (
    ProviderRunResult,
    _provider_result_from_claude,
    _provider_result_from_codex,
    _without_model_arg,
    build_issue_file_prompt,
    build_issue_hybrid_edit_prompt,
    build_issue_humanize_prompt,
    build_provider_command,
    build_repair_prompt,
    build_selection_file_prompt,
    build_selection_prompt,
    build_shortlist_file_prompt,
    build_shortlist_prompt,
    extract_json_object,
    run_ai_task,
    run_provider,
)
from subprocess import CompletedProcess
from daily_news.config import PipelineConfig, load_section
from daily_news.models import AIIssueOutput, CandidateItem, CodexSelectionOutput, CodexShortlistOutput, RawItem


def test_extract_json_object_from_plain_json() -> None:
    payload = {"headlines": [], "briefs": [], "discarded": [], "merged_sources": []}

    assert extract_json_object(json.dumps(payload)) == payload


def test_extract_json_object_prefers_fenced_full_object_over_inline_json() -> None:
    output = """
`pullquote` must be {"text": "...", "cite": "..."}.

```json
{"headlines": [], "briefs": [], "discarded": [], "merged_sources": []}
```
"""

    assert extract_json_object(output) == {"headlines": [], "briefs": [], "discarded": [], "merged_sources": []}


def test_validate_sample_ai_output() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample_ai_output.json"
    output = AIIssueOutput.model_validate_json(fixture.read_text(encoding="utf-8"))

    assert output.headlines[0].title_zh == "英伟达发布新一代 AI 芯片 Rubin"
    assert output.briefs[0].relevance_score == 82


def test_ai_issue_output_accepts_string_pullquote() -> None:
    payload = {
        "headlines": [
            {
                "source_item_ids": ["item-1"],
                "kicker": "AI · 芯片",
                "title_zh": "标题",
                "summary_zh": "摘要",
                "read_body_zh": ["正文"],
                "pullquote": "一句话 —— 来源",
                "ai_impact": "影响",
                "sources": [{"name": "Source", "url": "https://example.com"}],
                "relevance_score": 80,
                "importance_score": 70,
            }
        ],
        "briefs": [],
        "discarded": [],
        "merged_sources": [],
    }

    output = AIIssueOutput.model_validate(payload)

    assert output.headlines[0].pullquote is not None
    assert output.headlines[0].pullquote.text == "一句话"
    assert output.headlines[0].pullquote.cite == "来源"


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


def test_build_selection_file_prompt_includes_history_index_path() -> None:
    section = load_section("tech")
    prompt = build_selection_file_prompt(
        section,
        Path("/tmp/03_enriched_candidates.json"),
        history_index_path=Path("/tmp/04_history_index.json"),
    )

    assert "/tmp/04_history_index.json" in prompt
    assert "最近已发布日报索引" in prompt
    assert "不包含历史正文" in prompt
    assert "不得选为头条" in prompt


def test_file_prompts_include_taste_profile_when_present(tmp_path: Path) -> None:
    section = load_section("tech")
    taste_path = tmp_path / "taste.md"
    taste_path.write_text("# 选题口味档案 · tech\n\n- 多看 AI 基础设施。\n- 少看发布会通稿。\n", encoding="utf-8")

    shortlist_prompt = build_shortlist_file_prompt(
        section,
        Path("/tmp/02_candidates.json"),
        taste_profile_path=taste_path,
    )
    selection_prompt = build_selection_file_prompt(
        section,
        Path("/tmp/03_enriched_candidates.json"),
        taste_profile_path=taste_path,
    )

    assert "taste_profile" in shortlist_prompt
    assert "soft_preference_weights" in shortlist_prompt
    assert "多看 AI 基础设施" in shortlist_prompt
    assert "interests.avoid 仍是硬边界" in shortlist_prompt
    assert "taste_profile" in selection_prompt
    assert "少看发布会通稿" in selection_prompt
    assert "interests.avoid 仍是硬边界" in selection_prompt


def test_file_prompts_ignore_missing_taste_profile() -> None:
    section = load_section("tech")
    prompt = build_shortlist_file_prompt(
        section,
        Path("/tmp/02_candidates.json"),
        taste_profile_path=Path("/tmp/missing-taste.md"),
    )

    assert "soft_preference_weights" not in prompt
    assert "content_md" not in prompt
    assert "多看 AI 基础设施" not in prompt


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
    assert "pullquote 默认输出 null" in prompt
    assert "绝不能输出字符串" in prompt
    assert "目标接近前两天日报而不是长报告" in prompt
    assert "速览要像速览" in prompt
    assert "Nvidia AI chip news" not in prompt


def test_issue_file_prompt_includes_style_profile_when_present(tmp_path: Path) -> None:
    section = load_section("tech")
    style_path = tmp_path / "style.md"
    style_path.write_text("# 写作口味档案 · tech\n\n- 翻译更口语。\n- 精读段落更短。\n", encoding="utf-8")

    prompt = build_issue_file_prompt(
        section,
        Path("/tmp/04_selection.json"),
        Path("/tmp/03_enriched_candidates.json"),
        style_profile_path=style_path,
    )

    assert "style_profile" in prompt
    assert "writing_style_preferences" in prompt
    assert "翻译更口语" in prompt
    assert "精读段落更短" in prompt
    assert "不能覆盖以上事实红线和字段边界" in prompt


def test_issue_file_prompt_can_include_project_chinese_editor_rules() -> None:
    section = load_section("tech")
    prompt = build_issue_file_prompt(
        section,
        Path("/tmp/04_selection.json"),
        Path("/tmp/03_enriched_candidates.json"),
        chinese_editor_rules_path=Path("/tmp/zh_news_editor.md"),
    )

    assert "/tmp/zh_news_editor.md" in prompt
    assert "完整读取该规则文件" in prompt


def test_issue_humanize_prompt_locks_non_text_fields() -> None:
    prompt = build_issue_humanize_prompt(
        Path("/tmp/variant-a.json"),
        Path("/tmp/zh_news_editor.md"),
    )

    assert "/tmp/variant-a.json" in prompt
    assert "唯一可修改的字段" in prompt
    assert "source_item_ids、sources、relevance_score、importance_score" in prompt
    assert "禁止新增数字、公司、人物" in prompt


def test_issue_hybrid_prompt_balances_rewrite_freedom_and_fact_scope() -> None:
    prompt = build_issue_hybrid_edit_prompt(
        Path("/tmp/variant-a.json"),
        Path("/tmp/03_enriched_candidates.json"),
        Path("/tmp/zh_news_editor.md"),
    )

    assert "/tmp/variant-a.json" in prompt
    assert "/tmp/03_enriched_candidates.json" in prompt
    assert "允许拆句、合句、调整信息顺序" in prompt
    assert "事实稿决定“这篇要讲哪些事实”" in prompt
    assert "已成功/已发生/已取得" in prompt
    assert "没有明确依据的否定句不能新增" in prompt


def test_issue_file_prompt_ignores_missing_style_profile() -> None:
    section = load_section("tech")
    prompt = build_issue_file_prompt(
        section,
        Path("/tmp/04_selection.json"),
        Path("/tmp/03_enriched_candidates.json"),
        style_profile_path=Path("/tmp/missing-style.md"),
    )

    assert "writing_style_preferences" not in prompt
    assert "content_md" not in prompt
    assert "翻译更口语" not in prompt


def test_repair_prompt_forbids_markdown_and_string_pullquote() -> None:
    prompt = build_repair_prompt(
        "原始任务",
        '{"pullquote": "一句话 —— 来源"}',
        "pullquote should be object",
    )

    assert "输出必须从 {" in prompt
    assert "不要 ```json 代码块" in prompt
    assert "pullquote 只能是 null 或对象" in prompt
    assert "绝不能是字符串" in prompt


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


def test_build_codex_provider_command_supports_model() -> None:
    config = PipelineConfig()
    config.ai.codex.model = "gpt-5.6-sol"

    command = build_provider_command("codex", config)

    assert "--model" in command
    assert "gpt-5.6-sol" in command
    assert "--model" not in _without_model_arg(command)


def test_codex_provider_falls_back_to_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    config = PipelineConfig()
    config.ai.codex.model = "gpt-5.6-sol"
    calls: list[list[str]] = []

    def fake_run_command(command: list[str], prompt: str, timeout: int):  # noqa: ANN001
        calls.append(command)
        if len(calls) == 1:
            return CompletedProcess(args=command, returncode=1, stdout="", stderr="model unavailable"), 10
        return CompletedProcess(args=command, returncode=0, stdout='{"ok":true}', stderr=""), 20

    monkeypatch.setattr("daily_news.ai_engine._run_command", fake_run_command)

    result = run_provider("codex", "prompt", CodexShortlistOutput, config, use_output_schema=False)

    assert len(calls) == 2
    assert "--model" in calls[0]
    assert "--model" not in calls[1]
    assert result.return_code == 0
    assert result.model == "codex-default"
    assert result.duration_ms == 30
    assert result.extra["fallback_used"] is True
    assert result.extra["fallback_from_model"] == "gpt-5.6-sol"


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
        configured_model="gpt-configured",
    )

    assert result.output_text == '{"ok": true}'
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert result.total_tokens == 18
    assert result.model == "gpt-test"
    assert result.extra["event_count"] == 2

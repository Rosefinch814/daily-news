import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from daily_news.main import build_parser, make_issue, zh_editor_eval
from daily_news.models import AIIssueOutput, AIRunRecord, CandidateItem, CodexSelectionOutput, RawItem
from daily_news.storage import local as local_storage
from daily_news.zh_editor import (
    build_blind_mapping,
    build_blind_review,
    guarded_hybrid_output,
    guarded_humanized_output,
    validate_humanized_output,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _output() -> AIIssueOutput:
    return AIIssueOutput.model_validate_json((FIXTURE_DIR / "sample_ai_output.json").read_text(encoding="utf-8"))


def test_humanize_guard_accepts_wording_only_changes() -> None:
    original = _output()
    edited_headline = original.headlines[0].model_copy(
        update={"title_zh": "英伟达推出新一代 AI 芯片 Rubin"}
    )
    edited = original.model_copy(update={"headlines": [edited_headline]})

    report = validate_humanized_output(original, edited)

    assert report.valid is True
    assert report.fallback_used is False


def test_humanize_guard_rejects_new_number_and_falls_back() -> None:
    original = _output()
    edited_headline = original.headlines[0].model_copy(
        update={"summary_zh": original.headlines[0].summary_zh + " 2027年还将再升级。"}
    )
    edited = original.model_copy(update={"headlines": [edited_headline]})

    guarded, report = guarded_humanized_output(original, edited)

    assert guarded == original
    assert report.valid is False
    assert report.fallback_used is True
    assert any("新增数字" in violation for violation in report.violations)


def test_humanize_guard_rejects_new_entity() -> None:
    original = _output()
    edited_headline = original.headlines[0].model_copy(
        update={"summary_zh": original.headlines[0].summary_zh + " OpenAI也会采用。"}
    )
    edited = original.model_copy(update={"headlines": [edited_headline]})

    report = validate_humanized_output(original, edited)

    assert report.valid is False
    assert any("新增英文/混合主体" in violation for violation in report.violations)


def test_humanize_guard_rejects_locked_field_change() -> None:
    original = _output()
    edited_headline = original.headlines[0].model_copy(update={"relevance_score": 90})
    edited = original.model_copy(update={"headlines": [edited_headline]})

    report = validate_humanized_output(original, edited)

    assert report.valid is False
    assert any("可编辑字段以外" in violation for violation in report.violations)


def test_humanize_guard_rejects_dropped_factual_qualifier() -> None:
    original = _output()
    edited_headline = original.headlines[0].model_copy(
        update={"summary_zh": original.headlines[0].summary_zh.replace("并计划明年量产", "明年量产")}
    )
    edited = original.model_copy(update={"headlines": [edited_headline]})

    report = validate_humanized_output(original, edited)

    assert report.valid is False
    assert any("丢失事实限定词" in violation for violation in report.violations)


def test_hybrid_guard_falls_back_only_invalid_article() -> None:
    original = _output()
    unsafe_headline = original.headlines[0].model_copy(
        update={"summary_zh": original.headlines[0].summary_zh + " 2027年已经量产。"}
    )
    safer_brief = original.briefs[0].model_copy(
        update={"summary_zh": "苹果继续让更多 AI 功能直接在设备上运行，以减少等待并保护隐私。"}
    )
    edited = original.model_copy(update={"headlines": [unsafe_headline], "briefs": [safer_brief]})

    final_output, report = guarded_hybrid_output(original, edited)

    assert final_output.headlines[0] == original.headlines[0]
    assert final_output.briefs[0] == safer_brief
    assert report.valid is True
    assert report.fallback_used is True
    assert report.checks["fallback_articles"] == ["头条 1"]


def test_blind_review_is_stable_and_hides_variant_letters() -> None:
    output = _output()
    issue = make_issue(
        output,
        section_slug="tech",
        publication_name="我的日报·科技",
        issue_date=date(2026, 7, 22),
        volume=1,
        number=1,
    )
    mapping = build_blind_mapping("tech-2026-07-22-test")
    review = build_blind_review({"A": issue, "B": issue, "C": issue}, mapping)

    assert mapping == build_blind_mapping("tech-2026-07-22-test")
    assert "方案甲" in review
    assert "基线" not in review
    assert "方案 A" not in review


def test_golden_set_covers_required_news_types_and_claude_positive() -> None:
    payload = json.loads((FIXTURE_DIR / "zh_editor_golden.json").read_text(encoding="utf-8"))
    categories = {item["category"] for item in payload}

    assert {"网络安全", "模型发布", "电力", "政策"} <= categories
    assert any(item["source_date"] == "2026-07-21" for item in payload)
    assert all(item["preferred"].strip() for item in payload)


def test_offline_eval_writes_private_variants_without_touching_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "tech-2026-07-22-test"
    monkeypatch.setattr(local_storage, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(local_storage, "LOGS_DIR", tmp_path / "logs")
    output = _output()
    issue = make_issue(
        output,
        section_slug="tech",
        publication_name="我的日报·科技",
        issue_date=date(2026, 7, 22),
        volume=1,
        number=1,
    )
    local_storage.save_issue(run_id, issue)
    fetched_at = datetime.now(timezone.utc)
    candidates = [
        CandidateItem(
            raw_item=RawItem(
                id=item_id,
                source_id="source",
                source_name="Source",
                source_language="zh",
                title=item_id,
                url=f"https://example.com/{item_id}",
                summary="测试新闻",
                content="测试新闻正文",
                fetched_at=fetched_at,
            ),
            score=80,
            reason="测试",
        )
        for item_id in ["item-1", "item-2"]
    ]
    local_storage.save_enriched_candidates(run_id, candidates)
    local_storage.save_selection(
        run_id,
        CodexSelectionOutput.model_validate(
            {
                "headline_item_ids": ["item-1"],
                "brief_item_ids": ["item-2"],
                "headlines": [
                    {
                        "source_item_ids": ["item-1"],
                        "relevance_score": 95,
                        "importance_score": 96,
                        "reason": "头条",
                    }
                ],
                "briefs": [
                    {
                        "source_item_ids": ["item-2"],
                        "relevance_score": 82,
                        "importance_score": 70,
                        "reason": "速览",
                    }
                ],
            }
        ),
    )
    baseline_path = local_storage.artifact_path(run_id, "05_issue.json")
    baseline_before = baseline_path.read_bytes()
    outputs = [
        output.model_copy(
            update={
                "headlines": [
                    output.headlines[0].model_copy(
                        update={"title_zh": "英伟达推出新一代 AI 芯片 Rubin"}
                    )
                ]
            }
        ),
        output.model_copy(
            update={
                "headlines": [
                    output.headlines[0].model_copy(
                        update={"title_zh": "英伟达新一代 AI 芯片 Rubin 亮相"}
                    )
                ]
            }
        ),
    ]

    def fake_run_ai_task(*, task_type: str, prompt: str, **kwargs):  # noqa: ANN003
        result = outputs.pop(0)
        now = datetime.now(timezone.utc)
        return result, AIRunRecord(
            task_type=task_type,
            prompt_version="test",
            prompt=prompt,
            raw_output=result.model_dump_json(),
            parsed_output=result.model_dump(mode="json"),
            status="success",
            started_at=now,
            finished_at=now,
            provider="codex",
            duration_ms=1,
            prompt_chars=len(prompt),
            raw_output_chars=len(result.model_dump_json()),
        )

    monkeypatch.setattr("daily_news.main.run_ai_task", fake_run_ai_task)
    args = build_parser().parse_args(["zh-editor-eval", "--run-id", run_id, "--provider", "codex"])

    assert zh_editor_eval(args) == 0
    eval_dir = tmp_path / "runs" / run_id / "zh-editor-eval"
    assert (eval_dir / "variant-b-compose.json").exists()
    assert (eval_dir / "variant-c-humanize.json").exists()
    assert (eval_dir / "blind-review.md").exists()
    assert json.loads((eval_dir / "validation.json").read_text(encoding="utf-8"))["variant_c"]["valid"] is True
    assert baseline_path.read_bytes() == baseline_before

from datetime import date
from pathlib import Path

from daily_news.ai_engine import AIEngineError, XHSCondenseOutput, build_xhs_condense_file_prompt
from daily_news.config import PipelineConfig
from daily_news.main import make_issue
from daily_news.models import AIIssueOutput
from daily_news.xhs_export import (
    BRIEF_PAGE_MAX_ITEMS,
    CondenseRequest,
    SLOT_RANGES,
    XHS_PUBLICATION_NAME,
    XHSCondenser,
    build_xhs_condense_input,
    build_caption,
    build_cards,
    collect_condense_slots,
    condense_slot,
    paginate_briefs,
    prepare_xhs_condenser,
    render_cards_html,
)


def sample_issue():
    fixture = Path(__file__).parent / "fixtures" / "sample_ai_output.json"
    output = AIIssueOutput.model_validate_json(fixture.read_text(encoding="utf-8"))
    return make_issue(
        output,
        section_slug="tech",
        publication_name="Tourbillion News",
        issue_date=date(2026, 6, 23),
        volume=1,
        number=7,
    )


def test_build_xhs_cards_uses_design_cards_and_dynamic_briefs() -> None:
    issue = sample_issue()

    cards = build_cards(issue)

    assert cards[0].kind == "cover"
    assert [card.kind for card in cards].count("headline") == min(3, len(issue.headlines))
    assert [card.kind for card in cards].count("briefs") == len(paginate_briefs(issue.briefs))
    assert all(len(page) <= BRIEF_PAGE_MAX_ITEMS for page in paginate_briefs(issue.briefs))
    assert XHS_PUBLICATION_NAME in cards[0].html_body
    assert "今日科技日报" not in cards[0].html_body
    assert "Tourbillion News" not in "".join(card.html_body for card in cards)
    assert "为什么重要 · AI 分析" in cards[1].html_body
    assert '<span class="chip">AI</span>' in cards[1].html_body
    assert "条快扫" not in "".join(card.html_body for card in cards)
    assert "…" not in "".join(card.html_body for card in cards)


def test_condense_slot_keeps_complete_text_inside_contract() -> None:
    text = (
        "韩国存储大厂SK海力士周一宣布，将在美国发行近1780万股美国存托凭证，每份ADR相当于1/10普通股，"
        "预计周四定价、周五开始交易；按上周五首尔收盘价估算，可能募资约280亿美元。"
        "公司一季度营收同比增长近200%，年内股价上涨约260%。"
    )
    min_chars, max_chars = SLOT_RANGES["headline_summary"]

    result = condense_slot(
        text,
        slot_type="headline_summary",
        min_chars=min_chars,
        max_chars=max_chars,
    )

    assert min_chars <= len(result) <= max_chars
    assert result.endswith("。")
    assert "…" not in result
    assert "..." not in result


def test_condense_slot_returns_in_range_text_without_rewrite() -> None:
    text = "平台称每天约一半部署由编码 Agent 触发，AI 网关日均流经超过 1 万亿 token。"
    min_chars, max_chars = SLOT_RANGES["brief_summary"]

    assert condense_slot(text, slot_type="brief_summary", min_chars=min_chars, max_chars=max_chars) == text


def test_condense_slot_uses_ai_condenser_seam() -> None:
    min_chars, max_chars = SLOT_RANGES["brief_summary"]
    condenser = XHSCondenser({"brief_01_summary": "iOS 27 beta 启用 Siri 语速和表达度调节。"})

    result = condense_slot(
        "苹果在最新开发者测试版中启用了此前标注即将推出的 Siri 语速和表达度两项语音控制，用户可以用滑块调节 Siri 说话的快慢和情感丰富程度。",
        slot_id="brief_01_summary",
        slot_type="brief_summary",
        min_chars=min_chars,
        max_chars=max_chars,
        title="苹果 iOS 27 beta 开放 Siri 语速与表达度调节",
        condenser=condenser,
    )

    assert result == "iOS 27 beta 启用 Siri 语速和表达度调节。"


def test_build_xhs_condense_input_contains_ordered_slots_and_contract() -> None:
    issue = sample_issue()
    slots = collect_condense_slots(issue)

    payload = build_xhs_condense_input(issue, slots)

    assert payload["publication_name"] == XHS_PUBLICATION_NAME
    assert payload["slot_ranges"] == {
        "headline_summary": {"target_min": 90, "target_max": 155},
        "headline_impact": {"target_min": 85, "target_max": 145},
        "brief_summary": {"target_min": 22, "target_max": 52},
    }
    payload_slots = payload["slots"]
    assert isinstance(payload_slots, list)
    assert [slot["id"] for slot in payload_slots[:3]] == [
        "headline_01_summary",
        "headline_01_impact",
        "brief_01_summary",
    ]
    assert payload_slots[0]["sources"]
    assert "read_body_zh" not in payload_slots[0]


def test_xhs_condense_file_prompt_contains_design_contract(tmp_path: Path) -> None:
    input_path = tmp_path / "xhs_condense_input.json"
    input_path.write_text("{}", encoding="utf-8")

    prompt = build_xhs_condense_file_prompt(input_path)

    assert str(input_path) in prompt
    assert "headline_summary：90-155" in prompt
    assert "headline_impact：85-145" in prompt
    assert "brief_summary：22-52" in prompt
    assert "target_max 是硬上限" in prompt


def test_prepare_xhs_condenser_falls_back_when_batch_ai_fails(monkeypatch, tmp_path: Path) -> None:
    issue = sample_issue()

    def fail_run_ai_task(**kwargs):  # noqa: ANN001
        raise AIEngineError("provider unavailable")

    monkeypatch.setattr("daily_news.xhs_export.run_ai_task", fail_run_ai_task)
    condenser = prepare_xhs_condenser(issue, out_dir=tmp_path, config=PipelineConfig())
    request = CondenseRequest(
        slot_id="brief_01_summary",
        slot_type="brief_summary",
        title="测试标题",
        original_text="这是一段超过目标长度的原始文本，用来验证 AI provider 失败时导出仍然可以回落到确定性兜底，不会崩溃。",
        min_chars=22,
        max_chars=52,
    )

    assert condenser.condense(request, "确定性兜底文本。") == "确定性兜底文本。"
    assert (tmp_path / "xhs_condense_input.json").exists()


def test_xhs_condense_schema_is_strict_for_codex_response_format() -> None:
    schema = XHSCondenseOutput.model_json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["slots"]["type"] == "array"


def test_render_xhs_cards_html_contains_fixed_card_size_and_prototype_classes() -> None:
    issue = sample_issue()
    html = render_cards_html(issue, build_cards(issue))

    assert "width:1080px" in html
    assert "height:1440px" in html
    assert ".hl-body" in html
    assert ".brief-list" in html
    assert XHS_PUBLICATION_NAME in html
    assert "Tourbillion News" not in html
    assert 'id="card-1"' in html


def test_build_xhs_caption_uses_xhs_publication_name() -> None:
    issue = sample_issue()

    caption = build_caption(issue)

    assert f"{XHS_PUBLICATION_NAME}｜2026.06.23" in caption
    assert "今日头条：" in caption
    assert issue.headlines[0].title_zh in caption
    assert f"#{XHS_PUBLICATION_NAME}" in caption

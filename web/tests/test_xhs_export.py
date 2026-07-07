from datetime import date
from pathlib import Path

from daily_news.ai_engine import AIEngineError
from daily_news.config import PipelineConfig
from daily_news.main import make_issue
from daily_news.models import AIIssueOutput
from daily_news.xhs_export import (
    BRIEF_PAGE_MAX_ITEMS,
    CondenseRequest,
    SLOT_RANGES,
    XHS_PUBLICATION_NAME,
    XHSCondenser,
    build_caption,
    build_cards,
    condense_slot,
    paginate_briefs,
    render_cards_html,
)


class FakeCondenser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[CondenseRequest] = []

    def condense(self, request: CondenseRequest, fallback: str) -> str:
        self.requests.append(request)
        assert fallback
        return self.text


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
    condenser = FakeCondenser("苹果启用 Siri 语速和表达度调节。")

    result = condense_slot(
        "苹果在最新开发者测试版中启用了此前标注即将推出的 Siri 语速和表达度两项语音控制，用户可以用滑块调节 Siri 说话的快慢和情感丰富程度。",
        slot_type="brief_summary",
        min_chars=min_chars,
        max_chars=max_chars,
        title="苹果 iOS 27 beta 开放 Siri 语速与表达度调节",
        condenser=condenser,  # type: ignore[arg-type]
    )

    assert result == "苹果启用 Siri 语速和表达度调节。"
    assert condenser.requests[0].slot_type == "brief_summary"
    assert condenser.requests[0].title.startswith("苹果 iOS")


def test_xhs_condenser_falls_back_when_ai_provider_fails(monkeypatch) -> None:
    issue = sample_issue()
    condenser = XHSCondenser(issue, config=PipelineConfig())
    request = CondenseRequest(
        slot_type="brief_summary",
        title="测试标题",
        original_text="这是一段超过目标长度的原始文本，用来验证 AI provider 失败时导出仍然可以回落到确定性兜底，不会崩溃。",
        min_chars=22,
        max_chars=52,
    )

    def fail_run_ai_task(**kwargs):  # noqa: ANN001
        raise AIEngineError("provider unavailable")

    monkeypatch.setattr("daily_news.xhs_export.run_ai_task", fail_run_ai_task)

    assert condenser.condense(request, "确定性兜底文本。") == "确定性兜底文本。"


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

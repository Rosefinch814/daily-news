import html
import re
from datetime import date
from pathlib import Path

from daily_news.ai_engine import (
    AIEngineError,
    XHSCondenseOutput,
    XHSMagnetizeOutput,
    XHSNoteTitleOutput,
    build_xhs_condense_file_prompt,
    build_xhs_magnetize_prompt,
    build_xhs_note_title_prompt,
)
from daily_news.config import PipelineConfig
from daily_news.main import make_issue
from daily_news.models import AIIssueOutput
from daily_news.xhs_export import (
    BRIEF_PAGE_MAX_ITEMS,
    CondenseRequest,
    SLOT_RANGES,
    NOTE_HASHTAGS,
    XHS_PUBLICATION_NAME,
    XHSCondenser,
    XHSCoverTitleVariants,
    build_xhs_condense_input,
    build_xhs_magnetize_input,
    build_xhs_note_title_input,
    build_caption,
    build_cards,
    build_note_title,
    collect_condense_slots,
    condense_slot,
    emphasize_cover_text,
    emphasize_v2_cover_text,
    export_xhs_issue,
    fallback_note_title,
    is_valid_note_title,
    paginate_briefs,
    prepare_xhs_condenser,
    prepare_v2_cover_title_variants,
    render_cards_html,
    validate_magnetized_title,
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


def test_single_hook_cover_is_additive_and_keeps_content_cards_identical() -> None:
    issue = sample_issue()

    classic_cards = build_cards(issue, cover_template="classic")
    single_hook_cards = build_cards(issue, cover_template="single-hook")

    assert classic_cards[0].kind == "cover"
    assert single_hook_cards[0].kind == "cover2"
    assert classic_cards[1:] == single_hook_cards[1:]
    assert 'class="cv2-big m"' in single_hook_cards[0].html_body
    assert "cv2-head" in single_hook_cards[0].html_body
    assert "lead-label" not in single_hook_cards[0].html_body
    assert f"+{max(0, min(3, len(issue.headlines)) - 1)} 条头条" in single_hook_cards[0].html_body


def test_v2_cover_is_additive_and_keeps_existing_templates_unchanged() -> None:
    issue = sample_issue()

    classic_cards = build_cards(issue, cover_template="classic")
    single_hook_cards = build_cards(issue, cover_template="single-hook")
    v2_cards = build_cards(issue, cover_template="v2")

    assert classic_cards[0].kind == "cover"
    assert single_hook_cards[0].kind == "cover2"
    assert v2_cards[0].kind == "coverv2"
    assert classic_cards[1:] == single_hook_cards[1:] == v2_cards[1:]
    assert 'class="title3"' in v2_cards[0].html_body
    assert 'class="eyebrow"' in v2_cards[0].html_body
    assert 'class="bar"' not in v2_cards[0].html_body
    assert 'class="cv2-big m"' in single_hook_cards[0].html_body


def test_single_hook_cover_uses_large_size_for_short_hook() -> None:
    issue = sample_issue()
    issue.headlines[0].title_zh = "芯片巨头集体涨价"

    cover = build_cards(issue, cover_template="single-hook")[0]

    assert 'class="cv2-big l"' in cover.html_body


def test_single_hook_output_directory_does_not_replace_classic(monkeypatch, tmp_path: Path) -> None:
    issue = sample_issue()
    monkeypatch.setattr("daily_news.xhs_export.RUNS_DIR", tmp_path)
    monkeypatch.setattr("daily_news.xhs_export.render_card_images", lambda html_path, output_dir, count: [])

    classic = export_xhs_issue(issue, cover_template="classic")
    single_hook = export_xhs_issue(issue, cover_template="single-hook")
    v2 = export_xhs_issue(issue, cover_template="v2")

    assert classic.output_dir == tmp_path / "xhs" / "2026-06-23"
    assert single_hook.output_dir == tmp_path / "xhs" / "2026-06-23-single-hook"
    assert v2.output_dir == tmp_path / "xhs" / "2026-06-23-v2"
    assert "class=\"card cover\"" in classic.html_path.read_text(encoding="utf-8")
    assert "class=\"card cover2\"" in single_hook.html_path.read_text(encoding="utf-8")
    assert "class=\"card coverv2\"" in v2.html_path.read_text(encoding="utf-8")


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


def test_single_hook_condense_input_adds_cover_slots_only_when_requested() -> None:
    issue = sample_issue()
    slots = collect_condense_slots(issue, include_cover=True)

    payload = build_xhs_condense_input(issue, slots)

    assert [slot["id"] for slot in payload["slots"][:2]] == ["cover_hook", "cover_sub"]
    assert payload["slot_ranges"]["cover_hook"] == {"target_min": 12, "target_max": 24}
    assert payload["slot_ranges"]["cover_sub"] == {"target_min": 28, "target_max": 46}
    assert slots[0].request.original_text == issue.headlines[0].title_zh
    assert slots[1].request.original_text == issue.headlines[0].summary_zh


def test_xhs_condense_file_prompt_contains_design_contract(tmp_path: Path) -> None:
    input_path = tmp_path / "xhs_condense_input.json"
    input_path.write_text("{}", encoding="utf-8")

    prompt = build_xhs_condense_file_prompt(input_path)

    assert str(input_path) in prompt
    assert "headline_summary：90-155" in prompt
    assert "headline_impact：85-145" in prompt
    assert "brief_summary：22-52" in prompt
    assert "cover_hook：12-24" in prompt
    assert "cover_sub：28-46" in prompt
    assert "emphasis_terms" in prompt
    assert "target_max 是硬上限" in prompt


def test_xhs_note_title_prompt_contains_hard_limit_and_input_path(tmp_path: Path) -> None:
    input_path = tmp_path / "xhs_note_title_input.json"
    input_path.write_text("{}", encoding="utf-8")

    prompt = build_xhs_note_title_prompt(input_path)

    assert str(input_path) in prompt
    assert "不超过 20 个中文字符" in prompt
    assert "忠实、不标题党、不新增事实" in prompt
    assert "不要概括整期" in prompt
    assert "AI科技日报今日看点" in prompt
    assert '{"title": "不超过20字的中文标题"}' in prompt


def test_xhs_magnetize_prompt_contains_contract_and_examples(tmp_path: Path) -> None:
    input_path = tmp_path / "xhs_magnetize_input.json"
    input_path.write_text("{}", encoding="utf-8")

    prompt = build_xhs_magnetize_prompt(input_path)

    assert str(input_path) in prompt
    assert "restrained" in prompt
    assert "punchy" in prompt
    assert "12-24 字" in prompt
    assert "事实层铁律" in prompt
    assert "智谱估值暴涨15倍" in prompt


def test_build_xhs_note_title_input_contains_only_needed_issue_context() -> None:
    issue = sample_issue()

    payload = build_xhs_note_title_input(issue)

    assert payload["publication_name"] == XHS_PUBLICATION_NAME
    assert payload["title_max_chars"] == 20
    assert payload["headlines"][0]["title"] == issue.headlines[0].title_zh
    assert payload["headlines"][0]["summary_zh"] == issue.headlines[0].summary_zh
    assert payload["brief_titles"] == [article.title_zh for article in issue.briefs]
    assert "read_body_zh" not in payload["headlines"][0]


def test_build_xhs_magnetize_input_contains_only_headline_one_facts() -> None:
    issue = sample_issue()

    payload = build_xhs_magnetize_input(issue)

    assert payload["title_zh"] == issue.headlines[0].title_zh
    assert payload["summary_zh"] == issue.headlines[0].summary_zh
    assert payload["target_min"] == 12
    assert payload["target_max"] == 24
    assert "ai_impact" not in payload


def test_note_title_validator_rejects_overlimit_and_new_numbers() -> None:
    issue = sample_issue()

    assert is_valid_note_title("Agent开始接管部署", issue)
    assert not is_valid_note_title("这是一条明确超过二十个中文字符的小红书标题", issue)
    assert not is_valid_note_title("新增9999亿订单", issue)
    assert not is_valid_note_title("AI日报...", issue)
    assert not is_valid_note_title("AI科技日报今日看点", issue)
    assert not is_valid_note_title("今日AI速览", issue)


def test_build_note_title_falls_back_when_ai_disabled_or_provider_fails(monkeypatch, tmp_path: Path) -> None:
    issue = sample_issue()

    assert build_note_title(issue, out_dir=tmp_path, config=PipelineConfig(), ai_enabled=False) == fallback_note_title(issue)

    def fail_run_ai_task(**kwargs):  # noqa: ANN001
        raise AIEngineError("provider unavailable")

    monkeypatch.setattr("daily_news.xhs_export.run_ai_task", fail_run_ai_task)

    assert build_note_title(issue, out_dir=tmp_path, config=PipelineConfig(), ai_enabled=True) == fallback_note_title(issue)
    assert (tmp_path / "xhs_note_title_input.json").exists()


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


def test_prepare_v2_cover_title_variants_uses_valid_ai_output(monkeypatch, tmp_path: Path) -> None:
    issue = sample_issue()
    issue.headlines[0].title_zh = "谷歌自研新芯片Frozen v2曝光，目标2028年让Gemini能效提升6至10倍"
    issue.headlines[0].summary_zh = "谷歌计划在2028年前后推出Frozen v2，目标能效提升6至10倍。"

    monkeypatch.setattr(
        "daily_news.xhs_export.run_ai_task",
        lambda **kwargs: (
            XHSMagnetizeOutput(
                restrained="谷歌自研芯片曝光，能效目标提升6至10倍",
                punchy="6到10倍！谷歌要给Gemini换颗自研芯",
            ),
            object(),
        ),
    )
    monkeypatch.setattr("daily_news.xhs_export.save_ai_task_run", lambda *args, **kwargs: None)

    variants = prepare_v2_cover_title_variants(
        issue,
        out_dir=tmp_path,
        condenser=None,
        config=PipelineConfig(),
        ai_enabled=True,
    )

    assert variants.source == "ai"
    assert variants.restrained.startswith("谷歌")
    assert variants.punchy is not None
    assert not variants.rejection_reasons
    assert (tmp_path / "xhs_magnetize_input.json").exists()


def test_prepare_v2_cover_title_variants_falls_back_when_provider_fails(monkeypatch, tmp_path: Path) -> None:
    issue = sample_issue()

    def fail_run_ai_task(**kwargs):  # noqa: ANN001
        raise AIEngineError("provider unavailable")

    monkeypatch.setattr("daily_news.xhs_export.run_ai_task", fail_run_ai_task)

    variants = prepare_v2_cover_title_variants(
        issue,
        out_dir=tmp_path,
        condenser=None,
        config=PipelineConfig(),
        ai_enabled=True,
    )

    assert variants.source == "fallback"
    assert variants.restrained == variants.fallback
    assert variants.punchy is None
    assert any("provider 失败" in reason for reason in variants.rejection_reasons)


def test_magnetize_validator_rejects_drift_and_restrained_hype() -> None:
    source_title = "谷歌自研新芯片曝光，目标能效提升6至10倍"
    summary = "谷歌计划在2028年前后推出Frozen v2，目标能效提升6至10倍。"

    valid = validate_magnetized_title(
        "谷歌自研芯片曝光，能效目标提升6至10倍",
        original_title=source_title,
        summary=summary,
        restrained=True,
    )
    new_number = validate_magnetized_title(
        "谷歌自研芯片曝光，能效目标提升20倍",
        original_title=source_title,
        summary=summary,
        restrained=True,
    )
    wrong_subject = validate_magnetized_title(
        "微软自研芯片曝光，能效目标提升6至10倍",
        original_title=source_title,
        summary=summary,
        restrained=True,
    )
    hype = validate_magnetized_title(
        "震惊！谷歌芯片能效目标提升6至10倍",
        original_title=source_title,
        summary=summary,
        restrained=True,
    )
    lost_plan = validate_magnetized_title(
        "谷歌自研芯片能效提升6至10倍",
        original_title=source_title,
        summary=summary,
        restrained=True,
    )

    assert valid == []
    assert any("数字" in reason for reason in new_number)
    assert any("主体" in reason for reason in wrong_subject)
    assert any("情绪词" in reason for reason in hype)
    assert any("未来语气" in reason for reason in lost_plan)


def test_magnetize_validator_accepts_supported_absolute_and_emotional_prefix() -> None:
    copyright_title = "Anthropic 15亿美元版权和解获终审批准，每部赔3000美元"
    kimi_title = "Kimi K3发布48小时算力告急：月之暗面暂停新订阅"

    assert (
        validate_magnetized_title(
            "美国版权史最大和解：Anthropic赔15亿",
            original_title=copyright_title,
            summary="这是美国版权史上最大规模的AI版权和解。",
            restrained=False,
        )
        == []
    )
    assert (
        validate_magnetized_title(
            "太火了！Kimi K3发布48小时，算力就顶不住",
            original_title=kimi_title,
            summary="Kimi K3发布后算力告急。",
            restrained=False,
        )
        == []
    )


def test_only_v2_prepares_magnetized_title_variants(monkeypatch, tmp_path: Path) -> None:
    issue = sample_issue()
    calls: list[str] = []

    monkeypatch.setattr("daily_news.xhs_export.RUNS_DIR", tmp_path)
    monkeypatch.setattr("daily_news.xhs_export.render_card_images", lambda html_path, output_dir, count: [])
    monkeypatch.setattr("daily_news.xhs_export.prepare_xhs_condenser", lambda *args, **kwargs: XHSCondenser({}))
    monkeypatch.setattr("daily_news.xhs_export.build_note_title", lambda *args, **kwargs: fallback_note_title(issue))

    def fake_variants(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append("v2")
        return XHSCoverTitleVariants(
            original=issue.headlines[0].title_zh,
            fallback="原标题收敛兜底文案",
            restrained="克制版封面标题文案",
            punchy="冲版封面标题文案",
            source="ai",
        )

    monkeypatch.setattr("daily_news.xhs_export.prepare_v2_cover_title_variants", fake_variants)

    export_xhs_issue(issue, config=PipelineConfig(), ai_condense=True, cover_template="classic")
    export_xhs_issue(issue, config=PipelineConfig(), ai_condense=True, cover_template="single-hook")
    v2 = export_xhs_issue(issue, config=PipelineConfig(), ai_condense=True, cover_template="v2")

    assert calls == ["v2"]
    assert not (tmp_path / "xhs" / "2026-06-23" / "cover_title_variants.txt").exists()
    assert not (tmp_path / "xhs" / "2026-06-23-single-hook" / "cover_title_variants.txt").exists()
    assert (v2.output_dir / "cover_title_variants.txt").exists()
    assert "克制版封面标题文案" in v2.html_path.read_text(encoding="utf-8")


def test_xhs_condense_schema_is_strict_for_codex_response_format() -> None:
    schema = XHSCondenseOutput.model_json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["slots"]["type"] == "array"
    slot_schema = schema["$defs"]["XHSCondenseSlotOutput"]
    assert slot_schema["additionalProperties"] is False
    assert "emphasis_terms" in slot_schema["required"]


def test_cover_emphasis_only_adds_markup_without_changing_text() -> None:
    original = "SK海力士拟发行1780万股ADR"

    marked = emphasize_cover_text(original, ["SK海力士"])
    numeric_fallback = emphasize_cover_text(original, [])

    assert "<em>SK海力士</em>" in marked
    assert "<em>1780万</em>" in numeric_fallback
    assert html.unescape(re.sub(r"</?em>", "", marked)) == original
    assert html.unescape(re.sub(r"</?em>", "", numeric_fallback)) == original


def test_v2_cover_emphasis_marks_subject_and_key_number_without_changing_text() -> None:
    original = "SK海力士拟发行1780万股ADR"

    marked = emphasize_v2_cover_text(original, ["SK海力士", "1780万"])
    numeric_fallback = emphasize_v2_cover_text(original, [])

    assert marked.count('class="mark"') == 2
    assert '<span class="mark">SK海力士</span>' in marked
    assert '<span class="mark">1780万股</span>' in marked
    assert '<span class="mark">1780万股</span>' in numeric_fallback
    assert html.unescape(re.sub(r'</?span(?: class="mark")?>', "", marked)) == original
    assert html.unescape(re.sub(r'</?span(?: class="mark")?>', "", numeric_fallback)) == original


def test_v2_cover_emphasis_prefers_numeric_range_and_caps_at_two_marks() -> None:
    original = "谷歌自研新芯片曝光，能效目标提升6至10倍"

    marked = emphasize_v2_cover_text(original, ["谷歌", "自研新芯片"])

    assert marked.count('class="mark"') == 2
    assert '<span class="mark">谷歌</span>' in marked
    assert '<span class="mark">6至10倍</span>' in marked
    assert html.unescape(re.sub(r'</?span(?: class="mark")?>', "", marked)) == original


def test_xhs_note_title_schema_is_strict_for_codex_response_format() -> None:
    schema = XHSNoteTitleOutput.model_json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["title"]["type"] == "string"


def test_xhs_magnetize_schema_is_strict_for_codex_response_format() -> None:
    schema = XHSMagnetizeOutput.model_json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"restrained", "punchy"}


def test_render_xhs_cards_html_contains_fixed_card_size_and_prototype_classes() -> None:
    issue = sample_issue()
    html = render_cards_html(issue, build_cards(issue))

    assert "width:1080px" in html
    assert "height:1440px" in html
    assert ".hl-body" in html
    assert ".brief-list" in html
    assert "white-space:nowrap" in html
    assert XHS_PUBLICATION_NAME in html
    assert "Tourbillion News" not in html
    assert 'id="card-1"' in html


def test_build_xhs_caption_uses_xhs_publication_name() -> None:
    issue = sample_issue()

    caption = build_caption(issue)
    lines = caption.splitlines()

    assert lines[0] == "AI科技日报 · 6月23日"
    assert NOTE_HASHTAGS in caption
    assert "今日头条：" in caption
    for idx, article in enumerate(issue.headlines[:3], start=1):
        assert f"{idx}. {article.title_zh}" in caption
    assert "速览还包括" not in caption
    assert len(caption) <= 1000

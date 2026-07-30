from datetime import datetime, timezone

from daily_news.config import load_section
from daily_news.models import RawItem
from daily_news.scoring import dedupe_url_key, rank_candidates, title_dedupe_hash, score_item


def _item(title: str, summary: str) -> RawItem:
    return RawItem(
        id="item-1",
        source_id="the_verge",
        source_name="The Verge",
        source_language="en",
        title=title,
        url="https://example.com/article",
        summary=summary,
        fetched_at=datetime.now(timezone.utc),
    )


def test_score_rewards_interest_terms() -> None:
    section = load_section("tech")
    item = _item("Nvidia announces AI chip", "英伟达发布 AI芯片，面向大模型训练。")

    candidate = score_item(item, section)

    assert candidate.score > 30
    assert "英伟达" in candidate.matched_terms
    assert "AI芯片" in candidate.matched_terms


def test_score_matches_english_aliases() -> None:
    section = load_section("tech")
    item = _item("OpenAI launches new initiative for LLM security", "Nvidia AI chips and GPUs are in focus.")

    candidate = score_item(item, section)

    assert "英伟达" in candidate.matched_terms
    assert "AI芯片" in candidate.matched_terms
    assert "大模型进展" in candidate.matched_terms


def test_score_penalizes_avoid_terms() -> None:
    section = load_section("tech")
    item = _item("Startup raises seed round", "一家小公司融资，内容偏纯营销稿。")

    candidate = score_item(item, section)

    assert candidate.score < 0
    assert candidate.avoided_terms == ["小公司融资", "纯营销稿"]


def test_score_penalizes_aggregate_news() -> None:
    section = load_section("tech")
    item = _item("8点1氪丨英伟达、苹果、微软最新消息", "早报聚合多家公司动态。")

    candidate = score_item(item, section)

    assert "聚合/快讯降权" in candidate.reason
    assert candidate.score <= 35


def test_score_caps_keyword_stuffed_aggregate_news() -> None:
    section = load_section("tech")
    item = _item(
        "8点1氪丨三星、SK海力士、美光、英伟达、苹果、OpenAI、特斯拉最新消息",
        "今日热点导览聚合半导体、AI芯片、大模型、自动驾驶、数据中心等多条旧闻。",
    )

    candidate = score_item(item, section)

    assert candidate.score <= 35


def test_score_rescues_storage_earnings_inside_noisy_aggregate() -> None:
    section = load_section("tech")
    item = _item(
        "8点1氪丨多家公司动态与今日热点导览",
        (
            "TOP 3大新闻包含股价、市值、融资和大会消息。" + "其他公司动态。" * 80
            + "大公司财报：SK海力士第二季度营业利润同比增长557.2%，销售额和净利润同步增长。"
        ),
    )

    candidate = score_item(item, section)

    assert candidate.entered_ai is True
    assert candidate.score >= 42
    assert candidate.priority_signals
    assert candidate.priority_signals[0].startswith("SK海力士×")
    assert "重点存储事件保护" in candidate.reason


def test_score_does_not_rescue_storage_stock_chatter() -> None:
    section = load_section("tech")
    item = _item(
        "8点1氪丨SK海力士ADR盘中涨超5%",
        "今日热点导览只有股价、市值和市场情绪，没有经营或产业进展。",
    )

    candidate = score_item(item, section)

    assert candidate.priority_signals == []
    assert candidate.score <= 35


def test_score_does_not_join_company_and_unrelated_event_across_sentences() -> None:
    section = load_section("tech")
    item = _item(
        "8点1氪丨长鑫科技上市动态；泡泡玛特城市乐园涨价",
        "今日热点导览：“工业维生素”价格暴涨，长鑫科技中签号出炉。另一家公司公布季度财报。",
    )

    candidate = score_item(item, section)

    assert candidate.priority_signals == []
    assert candidate.score <= 35


def test_rank_candidates_reserves_review_for_storage_priority_event() -> None:
    section = load_section("tech")
    regular_items = []
    for index in range(5):
        raw = _item(
            f"Nvidia AI chip update {index}",
            "OpenAI model and data center GPU news.",
        )
        regular_items.append(
            raw.model_copy(
                update={
                    "id": f"regular-{index}",
                    "url": f"https://example.com/regular-{index}",
                }
            )
        )
    storage_item = _item(
        "8点1氪丨今日公司动态",
        "财报：美光季度营收增长，并上调HBM出货预期。",
    ).model_copy(
        update={
            "id": "storage-event",
            "url": "https://example.com/storage-event",
        }
    )

    ranked = rank_candidates(
        [*regular_items, storage_item],
        section,
        max_candidates=4,
        per_source_limit=4,
        require_interest_match_when_over_capacity=False,
    )

    assert "storage-event" in [candidate.raw_item.id for candidate in ranked]
    assert ranked[0].raw_item.id == "storage-event"


def test_score_penalizes_consumer_deals() -> None:
    section = load_section("tech")
    item = _item("Apple Watch SE Prime Day deal sale", "A consumer gadget discount.")

    candidate = score_item(item, section)

    assert "消费/娱乐弱相关降权" in candidate.reason


def test_score_boosts_high_value_terms() -> None:
    section = load_section("tech")
    item = _item("Nvidia data center GPU update", "AI chip and semiconductor supply are in focus.")

    candidate = score_item(item, section)

    assert "高价值主题加权" in candidate.reason


def test_rank_candidates_deduplicates_urls() -> None:
    section = load_section("tech")
    items = [
        _item("英伟达 AI芯片", "大模型进展"),
        _item("英伟达 AI芯片 duplicate", "大模型进展"),
    ]
    items[1] = items[1].model_copy(update={"id": "item-2", "url": "https://example.com/article"})

    ranked = rank_candidates(items, section)

    assert len(ranked) == 1


def test_rank_candidates_filters_historical_urls() -> None:
    section = load_section("tech")
    item = _item("英伟达 AI芯片", "大模型进展")

    ranked = rank_candidates(
        [item],
        section,
        historical_urls={dedupe_url_key(item.url)},
    )

    assert ranked == []


def test_rank_candidates_filters_historical_title_hashes() -> None:
    section = load_section("tech")
    old_title_hash = title_dedupe_hash("独家：Nvidia announces AI chip")
    item = _item("Nvidia announces AI chip", "大模型进展")

    ranked = rank_candidates(
        [item.model_copy(update={"url": "https://example.com/new-url"})],
        section,
        historical_title_hashes={old_title_hash} if old_title_hash else set(),
    )

    assert ranked == []


def test_rank_candidates_keeps_same_title_when_title_hash_disabled() -> None:
    section = load_section("tech")
    old_title_hash = title_dedupe_hash("Nvidia announces AI chip")
    item = _item("Nvidia announces AI chip", "大模型进展")

    ranked = rank_candidates(
        [item.model_copy(update={"url": "https://example.com/new-url"})],
        section,
        historical_title_hashes=None if old_title_hash else set(),
    )

    assert len(ranked) == 1


def test_rank_candidates_can_keep_unmatched_items_for_ai_prefilter() -> None:
    section = load_section("tech")
    items = []
    for index in range(5):
        raw = _item(f"General technology news {index}", "No explicit configured interest term.")
        items.append(
            raw.model_copy(
                update={
                    "id": f"item-{index}",
                    "url": f"https://example.com/article-{index}",
                }
            )
        )

    ranked = rank_candidates(
        items,
        section,
        max_candidates=3,
        per_source_limit=3,
        require_interest_match_when_over_capacity=False,
    )

    assert len(ranked) == 3

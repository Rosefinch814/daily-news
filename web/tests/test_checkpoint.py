from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_news.main import (
    build_parser,
    merge_enriched_candidates,
    validate_selection_ids,
    validate_shortlist_ids,
)
from daily_news.models import CandidateItem, CodexSelectionOutput, CodexShortlistOutput, RawItem


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
        ["enrich-mvp", "--run-id", "tech-2026-06-23-000000"],
        ["select-codex", "--run-id", "tech-2026-06-23-000000"],
        ["compose-codex", "--run-id", "tech-2026-06-23-000000"],
        ["render-mvp", "--run-id", "tech-2026-06-23-000000"],
        ["sync", "--run-id", "tech-2026-06-23-000000"],
    ]
    for argv in commands:
        parsed = parser.parse_args(argv)
        assert parsed.command == argv[0]

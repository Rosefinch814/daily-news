import json
from pathlib import Path

from daily_news.ai_engine import extract_json_object
from daily_news.models import AIIssueOutput


def test_extract_json_object_from_plain_json() -> None:
    payload = {"headlines": [], "briefs": [], "discarded": [], "merged_sources": []}

    assert extract_json_object(json.dumps(payload)) == payload


def test_validate_sample_ai_output() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample_ai_output.json"
    output = AIIssueOutput.model_validate_json(fixture.read_text(encoding="utf-8"))

    assert output.headlines[0].title_zh == "英伟达发布新一代 AI 芯片 Rubin"
    assert output.briefs[0].relevance_score == 82

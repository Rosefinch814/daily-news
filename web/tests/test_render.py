from datetime import date
from pathlib import Path

from daily_news.models import AIIssueOutput
from daily_news.main import make_issue
from daily_news.render import render_index, render_issue


def test_render_issue_and_index(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample_ai_output.json"
    output = AIIssueOutput.model_validate_json(fixture.read_text(encoding="utf-8"))
    issue = make_issue(
        output,
        section_slug="tech",
        publication_name="我的日报·科技",
        issue_date=date(2026, 6, 23),
        volume=1,
        number=7,
    )

    issue_path = render_issue(issue, dist_dir=tmp_path)
    index_path = render_index(issue, dist_dir=tmp_path)
    html = issue_path.read_text(encoding="utf-8")

    assert issue_path == tmp_path / "issues" / "2026-06-23.html"
    assert index_path == tmp_path / "index.html"
    assert "Tourbillion" in html
    assert "Technology" in html
    assert "英伟达发布新一代 AI 芯片 Rubin" in html
    assert "影响 · AI 分析（非原文事实）" in html
    assert "@media(max-width:520px)" in html
    assert 'name="viewport"' in html

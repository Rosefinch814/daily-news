from datetime import date
from pathlib import Path

from daily_news.models import AIIssueOutput
from daily_news.main import make_issue
from daily_news.render import build_frontend_app


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

    outputs = build_frontend_app(issue, dist_dir=tmp_path)
    issue_html = outputs["issue"].read_text(encoding="utf-8")
    index_html = outputs["index"].read_text(encoding="utf-8")
    issue_data = outputs["data"].read_text(encoding="utf-8")
    app_css = (tmp_path / "assets" / "app.css").read_text(encoding="utf-8")
    app_js = (tmp_path / "assets" / "app.js").read_text(encoding="utf-8")
    manifest = (tmp_path / "data" / "manifest.json").read_text(encoding="utf-8")

    assert outputs["issue"] == tmp_path / "issues" / "2026-06-23.html"
    assert outputs["index"] == tmp_path / "index.html"
    assert outputs["latest"] == tmp_path / "latest.html"
    assert outputs["data"] == tmp_path / "data" / "issues" / "2026-06-23.json"
    assert "Tourbillion" in issue_html
    assert "Technology" in issue_html
    assert 'name="viewport"' in issue_html
    assert 'id="app"' in index_html
    assert "英伟达发布新一代 AI 芯片 Rubin" in issue_data
    assert "影响 · AI 分析（非原文事实）" in app_js
    assert "renderIssuePicker" in app_js
    assert "@media(max-width:520px)" in app_css
    assert '"latest_issue_date": "2026-06-23"' in manifest

from __future__ import annotations

import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from daily_news.models import Issue
from daily_news.paths import DIST_DIR, TEMPLATES_DIR


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_issue(issue: Issue, *, dist_dir: Path = DIST_DIR) -> Path:
    issue_dir = dist_dir / "issues"
    issue_dir.mkdir(parents=True, exist_ok=True)
    output_path = issue_dir / f"{issue.issue_date.isoformat()}.html"
    template = _env().get_template("issue.html.j2")
    output_path.write_text(template.render(issue=issue), encoding="utf-8")
    return output_path


def render_index(issue: Issue, *, dist_dir: Path = DIST_DIR) -> Path:
    dist_dir.mkdir(parents=True, exist_ok=True)
    template = _env().get_template("index.html.j2")
    output_path = dist_dir / "index.html"
    latest_href = f"issues/{issue.issue_date.isoformat()}.html"
    output_path.write_text(template.render(issue=issue, latest_href=latest_href), encoding="utf-8")
    return output_path


def copy_issue_to_legacy_path(issue_html_path: Path, *, dist_dir: Path = DIST_DIR) -> None:
    """Keep dist/index.html and dist/issues/date.html as the public surface only."""
    dist_dir.mkdir(parents=True, exist_ok=True)
    if issue_html_path.name != "index.html":
        shutil.copyfile(issue_html_path, dist_dir / "latest.html")

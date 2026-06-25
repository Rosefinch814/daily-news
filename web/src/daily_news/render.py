from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

from daily_news.models import Issue
from daily_news.paths import DIST_DIR, FRONTEND_DIR, WEB_DIR


def _frontend_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(FRONTEND_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_js_assignment(path: Path, assignment: str, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False)
    path.write_text(f"{assignment} = {data};\n", encoding="utf-8")


def _read_manifest(manifest_path: Path) -> dict:
    if not manifest_path.exists():
        return {"latest_issue_date": None, "issues": [], "public_config": {}}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _public_config() -> dict[str, str]:
    load_dotenv(WEB_DIR / ".env")
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY") or ""
    if not supabase_url or not supabase_anon_key:
        return {}
    return {
        "supabase_url": supabase_url,
        "supabase_anon_key": supabase_anon_key,
    }


def _issue_manifest_entry(issue: Issue) -> dict:
    issue_date = issue.issue_date.isoformat()
    return {
        "date": issue_date,
        "date_cn": issue.date_cn,
        "section_slug": issue.section_slug,
        "title": "Tourbillion News · Technology",
        "path": f"issues/{issue_date}.html",
        "data_path": f"data/issues/{issue_date}.json",
    }


def export_issue_data(issue: Issue, *, dist_dir: Path = DIST_DIR) -> Path:
    issue_date = issue.issue_date.isoformat()
    payload = issue.model_dump(mode="json")
    issue_json_path = dist_dir / "data" / "issues" / f"{issue_date}.json"
    _write_json(issue_json_path, payload)
    issue_js_path = dist_dir / "data" / "issues" / f"{issue_date}.js"
    issue_js_path.write_text(
        "window.DAILY_NEWS_ISSUES = window.DAILY_NEWS_ISSUES || {};\n"
        f'window.DAILY_NEWS_ISSUES["{issue_date}"] = '
        + json.dumps(payload, ensure_ascii=False)
        + ";\n",
        encoding="utf-8",
    )

    manifest_path = dist_dir / "data" / "manifest.json"
    manifest = _read_manifest(manifest_path)
    entry = _issue_manifest_entry(issue)
    entries = [item for item in manifest.get("issues", []) if item.get("date") != issue_date]
    entries.append(entry)
    entries.sort(key=lambda item: item["date"], reverse=True)
    manifest = {
        "latest_issue_date": entries[0]["date"],
        "issues": entries,
        "public_config": _public_config(),
    }
    _write_json(manifest_path, manifest)
    _write_js_assignment(dist_dir / "data" / "manifest.js", "window.DAILY_NEWS_MANIFEST", manifest)
    return issue_json_path


def _render_app_shell(output_path: Path, *, asset_prefix: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    template = _frontend_env().get_template("index.html.j2")
    output_path.write_text(template.render(asset_prefix=asset_prefix), encoding="utf-8")
    return output_path


def build_frontend_app(issue: Issue, *, dist_dir: Path = DIST_DIR) -> dict[str, Path]:
    dist_dir.mkdir(parents=True, exist_ok=True)
    data_path = export_issue_data(issue, dist_dir=dist_dir)
    assets_src = FRONTEND_DIR / "assets"
    assets_dst = dist_dir / "assets"
    if assets_src.exists():
        shutil.copytree(assets_src, assets_dst, dirs_exist_ok=True)
    issue_date = issue.issue_date.isoformat()
    index_path = _render_app_shell(dist_dir / "index.html", asset_prefix="")
    latest_path = _render_app_shell(dist_dir / "latest.html", asset_prefix="")
    issue_path = _render_app_shell(dist_dir / "issues" / f"{issue_date}.html", asset_prefix="../")
    (dist_dir / "_redirects").write_text("/* /index.html 200\n", encoding="utf-8")
    return {
        "index": index_path,
        "latest": latest_path,
        "issue": issue_path,
        "data": data_path,
    }

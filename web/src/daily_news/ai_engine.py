from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Any

from daily_news.models import AIRunRecord, AIIssueOutput, CandidateItem, SectionConfig
from daily_news.text import clamp_text


PROMPT_VERSION = "v1.0"
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class AIEngineError(RuntimeError):
    pass


def _command() -> list[str]:
    command = os.getenv("CLAUDE_COMMAND", "claude -p")
    return shlex.split(command)


def extract_json_object(output: str) -> dict[str, Any]:
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        match = JSON_OBJECT_RE.search(output)
        if not match:
            raise
        return json.loads(match.group(0))


def _candidate_payload(candidates: list[CandidateItem]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for candidate in candidates:
        item = candidate.raw_item
        payload.append(
            {
                "id": item.id,
                "source": item.source_name,
                "source_language": item.source_language,
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "rss_summary": clamp_text(item.summary, 650),
                "content": clamp_text(item.content, 1200),
                "coarse_score": candidate.score,
                "coarse_reason": candidate.reason,
            }
        )
    return payload


def build_issue_prompt(section: SectionConfig, candidates: list[CandidateItem]) -> str:
    interests = section.interests
    payload = {
        "section": section.name,
        "publication_name": section.publication_name,
        "targets": {
            "headlines_min": section.target_headlines.min,
            "headlines_max": section.target_headlines.max,
            "briefs_min": section.target_briefs.min,
            "briefs_max": section.target_briefs.max,
        },
        "interests": {
            "want": {
                "companies": interests.want.companies,
                "themes": interests.want.themes,
                "people": interests.want.people,
            },
            "avoid": interests.avoid,
        },
        "candidates": _candidate_payload(candidates),
    }
    return f"""
你是《我的日报·科技》的新闻编辑。请只基于输入候选新闻生成 v1 科技日报结构化 JSON。

产品铁律：
1. 全程中文可读。英文源必须翻译/改写为中文，原文链接只作备查。
2. 忠于原文。摘要和精读只能写事实，不要脑补。
3. AI 判断只能写在 ai_impact 字段，不能混入 summary_zh 或 read_body_zh。
4. 精准优先，宁缺毋滥。命中“不想看”的内容应丢弃或显著降权。
5. 同一事件多源报道时，请合并为一条，并在 sources 中列出主要来源。

输出要求：
- 只输出一个 JSON 对象，不要 Markdown，不要解释。
- headlines 3-5 条，briefs 10-15 条；如果候选不足，可少于目标，但不要编造。
- 每条都必须给 relevance_score 和 importance_score，整数 0-100。
- headline 字段：source_item_ids, kicker, title_zh, summary_zh, read_body_zh, pullquote, ai_impact, sources, relevance_score, importance_score。
- brief 字段：source_item_ids, title_zh, summary_zh, sources, relevance_score, importance_score。
- discarded 字段记录主要丢弃项和原因。
- merged_sources 字段记录合并了哪些 source_item_ids。
- pullquote 没有可溯源事实或引语时用 null。

JSON schema 形状：
{{
  "headlines": [
    {{
      "source_item_ids": ["..."],
      "kicker": "芯片 · 发布",
      "title_zh": "...",
      "summary_zh": "...",
      "read_body_zh": ["事实段落1", "事实段落2"],
      "pullquote": null,
      "ai_impact": "影响分析，必须是 AI 判断",
      "sources": [{{"name": "The Verge", "url": "https://..."}}],
      "relevance_score": 90,
      "importance_score": 88
    }}
  ],
  "briefs": [
    {{
      "source_item_ids": ["..."],
      "title_zh": "...",
      "summary_zh": "...",
      "sources": [{{"name": "Reuters", "url": "https://..."}}],
      "relevance_score": 75,
      "importance_score": 65
    }}
  ],
  "discarded": [
    {{"source_item_ids": ["..."], "reason": "...", "relevance_score": 10, "importance_score": 20}}
  ],
  "merged_sources": [
    {{"source_item_ids": ["...", "..."], "reason": "同一事件"}}
  ]
}}

输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


def build_repair_prompt(original_prompt: str, raw_output: str, error: str) -> str:
    return f"""
上一次输出无法解析为符合要求的 JSON。

解析错误：
{error}

请基于原始任务重新输出唯一一个合法 JSON 对象。不要 Markdown，不要解释。

原始任务：
{original_prompt}

上一次输出：
{raw_output}
""".strip()


def run_claude(prompt: str, *, timeout_seconds: int | None = None) -> str:
    timeout = timeout_seconds or int(os.getenv("DAILY_NEWS_AI_TIMEOUT_SECONDS", "300"))
    command = _command()
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AIEngineError(f"Claude command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AIEngineError(f"Claude command timed out after {timeout}s") from exc

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise AIEngineError(f"Claude command failed with code {completed.returncode}: {stderr}")
    return completed.stdout.strip()


def generate_issue_output(
    section: SectionConfig,
    candidates: list[CandidateItem],
    *,
    allow_repair: bool = True,
    prompt: str | None = None,
) -> tuple[AIIssueOutput, AIRunRecord]:
    prompt = prompt or build_issue_prompt(section, candidates)
    started_at = datetime.now(timezone.utc)
    raw_output = ""
    try:
        raw_output = run_claude(prompt)
        parsed = extract_json_object(raw_output)
        output = AIIssueOutput.model_validate(parsed)
        finished_at = datetime.now(timezone.utc)
        return output, AIRunRecord(
            task_type="issue_generate",
            prompt_version=PROMPT_VERSION,
            prompt=prompt,
            raw_output=raw_output,
            parsed_output=output.model_dump(mode="json"),
            status="success",
            started_at=started_at,
            finished_at=finished_at,
        )
    except Exception as first_exc:  # noqa: BLE001 - persisted for debug.
        if not allow_repair:
            finished_at = datetime.now(timezone.utc)
            return _raise_with_record(prompt, raw_output, first_exc, started_at, finished_at)

        repair_prompt = build_repair_prompt(prompt, raw_output, str(first_exc))
        repair_started_at = datetime.now(timezone.utc)
        try:
            repair_raw_output = run_claude(repair_prompt)
            parsed = extract_json_object(repair_raw_output)
            output = AIIssueOutput.model_validate(parsed)
            finished_at = datetime.now(timezone.utc)
            return output, AIRunRecord(
                task_type="issue_generate_repair",
                prompt_version=PROMPT_VERSION,
                prompt=repair_prompt,
                raw_output=repair_raw_output,
                parsed_output=output.model_dump(mode="json"),
                status="success",
                started_at=repair_started_at,
                finished_at=finished_at,
            )
        except Exception as second_exc:  # noqa: BLE001
            finished_at = datetime.now(timezone.utc)
            return _raise_with_record(repair_prompt, raw_output, second_exc, repair_started_at, finished_at)


def _raise_with_record(
    prompt: str,
    raw_output: str,
    exc: Exception,
    started_at: datetime,
    finished_at: datetime,
) -> tuple[AIIssueOutput, AIRunRecord]:
    record = AIRunRecord(
        task_type="issue_generate",
        prompt_version=PROMPT_VERSION,
        prompt=prompt,
        raw_output=raw_output,
        parsed_output=None,
        status="failed",
        error=str(exc),
        started_at=started_at,
        finished_at=finished_at,
    )
    raise AIEngineError(str(exc)) from exc

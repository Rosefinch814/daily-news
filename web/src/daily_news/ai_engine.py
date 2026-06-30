from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from daily_news.config import PipelineConfig
from daily_news.models import (
    AIRunRecord,
    AIIssueOutput,
    CandidateItem,
    CodexShortlistOutput,
    SectionConfig,
)
from daily_news.paths import WEB_DIR
from daily_news.text import clamp_text


PROMPT_VERSION = "v1.1"
JSON_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
ProviderName = Literal["claude", "codex"]
AIOutput = TypeVar("AIOutput", bound=BaseModel)


class AIEngineError(RuntimeError):
    def __init__(self, message: str, *, record: AIRunRecord | None = None) -> None:
        super().__init__(message)
        self.record = record


@dataclass
class ProviderRunResult:
    output_text: str
    stdout: str
    stderr: str
    command: list[str]
    return_code: int | None
    duration_ms: int
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    provider_events: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def extract_json_object(output: str) -> dict[str, Any]:
    stripped = output.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("AI output JSON root must be an object")

    for match in JSON_CODE_FENCE_RE.finditer(output):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    candidates = _balanced_json_object_candidates(output)
    if not candidates:
        raise json.JSONDecodeError("No JSON object found", output, 0)
    candidates.sort(key=lambda item: (not _looks_like_ai_output(item[0]), -len(item[1])))
    return candidates[0][0]


def _balanced_json_object_candidates(output: str) -> list[tuple[dict[str, Any], str]]:
    candidates: list[tuple[dict[str, Any], str]] = []
    for start, char in enumerate(output):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(output)):
            current = output[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    candidate = output[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        candidates.append((parsed, candidate))
                    break
    return candidates


def _looks_like_ai_output(value: dict[str, Any]) -> bool:
    expected_keys = {
        "headlines",
        "briefs",
        "keep_item_ids",
        "maybe_item_ids",
        "drop_item_ids",
        "headline_item_ids",
        "brief_item_ids",
    }
    return bool(expected_keys.intersection(value))


def _candidate_payload(config: PipelineConfig, candidates: list[CandidateItem]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for candidate in candidates[: config.prompt.max_candidates]:
        item = candidate.raw_item
        payload.append(
            {
                "id": item.id,
                "source": item.source_name,
                "source_language": item.source_language,
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "rss_summary": clamp_text(item.summary, config.prompt.max_summary_chars),
                "content": clamp_text(item.content, config.prompt.max_content_chars),
                "coarse_score": candidate.score,
                "coarse_reason": candidate.reason,
                "matched_terms": candidate.matched_terms,
                "avoided_terms": candidate.avoided_terms,
            }
        )
    return payload


def _read_profile_text(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text or text.endswith("暂无记录。"):
        return None
    return text


def _section_payload(section: SectionConfig, *, taste_profile_path: Path | None = None) -> dict[str, Any]:
    interests = section.interests
    payload: dict[str, Any] = {
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
    }
    taste_profile = _read_profile_text(taste_profile_path)
    if taste_profile is not None:
        payload["taste_profile"] = {
            "role": "soft_preference_weights",
            "priority": "Use this to boost or lower relevance/importance within the hard boundaries above. If it conflicts with interests.avoid, interests.avoid wins.",
            "content_md": taste_profile,
        }
    return payload


def build_shortlist_prompt(
    section: SectionConfig,
    candidates: list[CandidateItem],
    config: PipelineConfig,
) -> str:
    payload = {
        **_section_payload(section),
        "candidates": _candidate_payload(config, candidates),
    }
    return f"""
你是《我的日报·科技》的第一轮新闻编辑。请只基于输入候选新闻做语义粗筛，输出严格 JSON。

任务目标：
1. 用中文理解英文标题和摘要，不需要先翻译全文。
2. 每个输入 candidate 都必须给出 keep / maybe / drop 三选一。
3. keep 表示值得抓正文并大概率进入最终选题；maybe 表示值得抓正文但不确定；drop 表示不进入正文补全。
4. 命中“不想看”应明显降权，但如果事件重大，可以保留并说明理由。
5. 聚合类新闻需要判断其中是否包含真正命中关注清单的内容。

输出要求：
- 只输出一个 JSON 对象，不要 Markdown，不要解释。
- keep_item_ids、maybe_item_ids、drop_item_ids 三组加起来必须覆盖所有输入 id。
- items 必须包含所有输入 id，且 decision 与顶层列表一致。
- relevance_score 和 importance_score 都是 0-100 整数。

JSON schema 形状：
{{
  "keep_item_ids": ["..."],
  "maybe_item_ids": ["..."],
  "drop_item_ids": ["..."],
  "items": [
    {{
      "source_item_id": "...",
      "decision": "keep",
      "category": "AI 芯片",
      "relevance_score": 90,
      "importance_score": 88,
      "reason": "中文理由",
      "is_aggregate": false,
      "aggregate_highlights": []
    }}
  ]
}}

输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


def build_shortlist_file_prompt(
    section: SectionConfig,
    candidates_path: Path,
    taste_profile_path: Path | None = None,
) -> str:
    payload = _section_payload(section, taste_profile_path=taste_profile_path)
    return f"""
你是《我的日报·科技》的第一轮新闻编辑。请读取本地 JSON 文件，并基于文件中的候选新闻做语义粗筛，输出严格 JSON。

输入文件：
{candidates_path}

板块和关注配置：
{json.dumps(payload, ensure_ascii=False, indent=2)}

任务目标：
1. 用中文理解英文标题和摘要，不需要先翻译全文。
2. 每个输入 candidate 都必须给出 keep / maybe / drop 三选一。
3. keep 表示值得抓正文并大概率进入最终选题；maybe 表示值得抓正文但不确定；drop 表示不进入正文补全。
4. 命中“不想看”应明显降权，但如果事件重大，可以保留并说明理由。
5. 聚合类新闻需要判断其中是否包含真正命中关注清单的内容。
6. 如果板块配置里包含 taste_profile，它是用户反馈沉淀出的软偏好：多看的主题可适度提权，少看的主题可适度降权；但 interests.avoid 仍是硬边界，不能被 taste_profile 翻盘。

输出要求：
- 只输出一个 JSON 对象，不要 Markdown，不要解释。
- keep_item_ids、maybe_item_ids、drop_item_ids 三组加起来必须覆盖输入文件中的所有候选 id。
- items 必须包含输入文件中的所有候选 id，且 decision 与顶层列表一致。
- 不要输出输入文件中不存在的 id。
- relevance_score 和 importance_score 都是 0-100 整数。

JSON schema 形状：
{{
  "keep_item_ids": ["..."],
  "maybe_item_ids": ["..."],
  "drop_item_ids": ["..."],
  "items": [
    {{
      "source_item_id": "...",
      "decision": "keep",
      "category": "AI 芯片",
      "relevance_score": 90,
      "importance_score": 88,
      "reason": "中文理由",
      "is_aggregate": false,
      "aggregate_highlights": []
    }}
  ]
}}
""".strip()


def build_selection_prompt(
    section: SectionConfig,
    candidates: list[CandidateItem],
    config: PipelineConfig,
) -> str:
    payload = {
        **_section_payload(section),
        "candidates": _candidate_payload(config, candidates),
    }
    return f"""
你是《我的日报·科技》的主编。请基于已补全文的候选新闻做最终选题和分层，输出严格 JSON。

任务目标：
1. 选择 {section.target_headlines.min}-{section.target_headlines.max} 条头条候选，{section.target_briefs.min}-{section.target_briefs.max} 条速览候选；候选不足时宁缺毋滥。
2. 同一事件多源报道必须合并为一条 selected item，并在 source_item_ids 中列出全部来源 id。
3. 只做选题和分层，不写中文标题、摘要、精读正文。
4. 丢弃项必须给出中文原因。
5. relevance_score 表示与用户关注点相关度，importance_score 表示事件本身重要度。

输出要求：
- 只输出一个 JSON 对象，不要 Markdown，不要解释。
- headline_item_ids 和 brief_item_ids 是所有入选来源 id 的展开列表。
- headlines 和 briefs 是最终条目列表，每个条目可以包含一个或多个 source_item_ids。

JSON schema 形状：
{{
  "headline_item_ids": ["..."],
  "brief_item_ids": ["..."],
  "headlines": [
    {{
      "source_item_ids": ["..."],
      "relevance_score": 90,
      "importance_score": 88,
      "reason": "中文选题理由"
    }}
  ],
  "briefs": [
    {{
      "source_item_ids": ["..."],
      "relevance_score": 70,
      "importance_score": 65,
      "reason": "中文选题理由"
    }}
  ],
  "discarded": [
    {{
      "source_item_ids": ["..."],
      "reason": "中文丢弃原因",
      "relevance_score": 10,
      "importance_score": 20
    }}
  ],
  "merged_sources": [
    {{"source_item_ids": ["...", "..."], "reason": "同一事件"}}
  ]
}}

输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


def build_selection_file_prompt(
    section: SectionConfig,
    enriched_candidates_path: Path,
    taste_profile_path: Path | None = None,
) -> str:
    payload = _section_payload(section, taste_profile_path=taste_profile_path)
    return f"""
你是《我的日报·科技》的主编。请读取本地 JSON 文件，并基于已补全文的候选新闻做最终选题和分层，输出严格 JSON。

输入文件：
{enriched_candidates_path}

板块和关注配置：
{json.dumps(payload, ensure_ascii=False, indent=2)}

任务目标：
1. 选择 {section.target_headlines.min}-{section.target_headlines.max} 条头条候选，{section.target_briefs.min}-{section.target_briefs.max} 条速览候选；候选不足时宁缺毋滥。
2. 同一事件多源报道必须合并为一条 selected item，并在 source_item_ids 中列出全部来源 id。
3. 只做选题和分层，不写中文标题、摘要、精读正文。
4. 丢弃项必须给出中文原因。
5. relevance_score 表示与用户关注点相关度，importance_score 表示事件本身重要度。
6. 如果板块配置里包含 taste_profile，它是用户反馈沉淀出的软偏好：多看的主题可适度提权，少看的主题可适度降权；但 interests.avoid 仍是硬边界，不能被 taste_profile 翻盘。

输出要求：
- 只输出一个 JSON 对象，不要 Markdown，不要解释。
- headline_item_ids 和 brief_item_ids 是所有入选来源 id 的展开列表。
- headlines 和 briefs 是最终条目列表，每个条目可以包含一个或多个 source_item_ids。
- 所有 source_item_ids 都必须来自输入文件，不要编造 id。

JSON schema 形状：
{{
  "headline_item_ids": ["..."],
  "brief_item_ids": ["..."],
  "headlines": [
    {{
      "source_item_ids": ["..."],
      "relevance_score": 90,
      "importance_score": 88,
      "reason": "中文选题理由"
    }}
  ],
  "briefs": [
    {{
      "source_item_ids": ["..."],
      "relevance_score": 70,
      "importance_score": 65,
      "reason": "中文选题理由"
    }}
  ],
  "discarded": [
    {{
      "source_item_ids": ["..."],
      "reason": "中文丢弃原因",
      "relevance_score": 10,
      "importance_score": 20
    }}
  ],
  "merged_sources": [
    {{"source_item_ids": ["...", "..."], "reason": "同一事件"}}
  ]
}}
""".strip()


def build_issue_file_prompt(
    section: SectionConfig,
    selection_path: Path,
    enriched_candidates_path: Path,
    style_profile_path: Path | None = None,
) -> str:
    payload = _section_payload(section)
    style_profile = _read_profile_text(style_profile_path)
    if style_profile is not None:
        payload["style_profile"] = {
            "role": "writing_style_preferences",
            "priority": "Use this to shape wording, length, explanation depth, and translation style. Product rules about factual accuracy, field boundaries, and Chinese readability override style preferences.",
            "content_md": style_profile,
        }
    return f"""
你是《我的日报·科技》的新闻编辑。请读取本地 JSON 文件，并基于已确定的选题结构和候选原文生成 v1 科技日报结构化 JSON。

选题文件：
{selection_path}

候选原文文件：
{enriched_candidates_path}

板块和关注配置：
{json.dumps(payload, ensure_ascii=False, indent=2)}

产品铁律：
1. 全程中文可读。英文源必须翻译/改写为中文，原文链接只作备查。
2. 忠于原文。摘要和精读只能写事实，不要脑补。
3. AI 判断只能写在 ai_impact 字段，不能混入 summary_zh 或 read_body_zh。
4. 精准优先，宁缺毋滥。命中“不想看”的内容应丢弃或显著降权。
5. 同一事件多源报道时，请合并为一条，并在 sources 中列出主要来源。
6. 如果板块配置里包含 style_profile，它是用户反馈沉淀出的写作偏好：用于调整中文表达、句长、解释深度和翻译口吻；但不能覆盖以上事实红线和字段边界。

输出要求：
- 只输出一个 JSON 对象，不要 Markdown，不要解释。
- 严格按选题文件中的 headlines 和 briefs 生成内容，不要新增未入选条目。
- headlines 需要生成 read_body_zh 和 ai_impact。
- briefs 只生成 title_zh、summary_zh、sources、relevance_score、importance_score，不要生成精读。
- 摘要和精读只写事实；影响分析必须只放在 ai_impact。
- 每条都必须给 relevance_score 和 importance_score，整数 0-100。
- 控制阅读量，目标接近前两天日报而不是长报告：头条 summary_zh 约 180-260 个中文字符；头条 read_body_zh 固定 3 段，每段约 120-180 个中文字符；头条 ai_impact 约 180-260 个中文字符；速览 summary_zh 约 120-200 个中文字符。
- 字数预算是上限倾向，不要机械凑字；信息特别复杂时可略超，但必须删除重复事实和次级细节，避免摘要、精读、影响分析互相复述。
- 速览要像速览：一条讲清核心事实和一层意义即可，不要写成小型精读。
- pullquote 默认输出 null。只有原文中存在明确、可溯源、值得突出展示的短引语时，才输出对象 {{"text": "引语正文", "cite": "来源或说话人"}}。
- pullquote 绝不能输出字符串、数组、Markdown 或带破折号的拼接文本；只能是 null 或 {{"text": "...", "cite": "..."}}。

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
""".strip()


def build_digest_file_prompt(
    section: SectionConfig,
    digest_input_path: Path,
) -> str:
    payload = _section_payload(section)
    return f"""
你是《我的日报·科技》的口味档案维护员。请读取本地 JSON 文件，把用户反馈消化成可长期复用的偏好档案，输出严格 JSON。

输入文件：
{digest_input_path}

板块和关注配置：
{json.dumps(payload, ensure_ascii=False, indent=2)}

任务目标：
1. 只根据输入文件里的反馈、对应日报条目和现有档案做增量更新。
2. 选题偏好写入 taste_md：多看/少看哪些主题、公司、人物、事件类型。
3. 写作偏好写入 style_md：语气、长度、翻译、解释深度等。
4. 对硬性关注清单的修改，只能写入 seed_suggestions_append，不能改 sections.yaml。
5. 保留旧档案中仍然有效的偏好，不要因为一条反馈就大幅重写。
6. 不要把临时情绪过度泛化成长期规则；没有证据就写轻量倾向。
7. 反馈只影响下一期，不改当期日报事实。
8. taste_md 和 style_md 是“当前稳定偏好摘要”，不是反馈流水账；必须合并重复项、删除过时或证据不足的表达。
9. 如果旧档案已经包含同类偏好，请改写合并，不要追加同义句。

档案长度与结构约束：
- taste_md 总长度必须小于 6000 字符；style_md 总长度必须小于 6000 字符。
- taste_md 建议结构：标题、当前倾向（最多 8 条）、降低权重（最多 6 条）、轻量观察（最多 5 条）。
- style_md 建议结构：标题、当前倾向（最多 8 条）、写法偏好（最多 8 条）、轻量观察（最多 5 条）。
- 每条偏好应短而可执行，避免长段落。
- seed_suggestions_append 只写新增建议；已有建议不要重复追加。
- changes 最多 20 条，说明新增、合并、弱化或删除了什么。

输出要求：
- 只输出一个 JSON 对象，不要 Markdown，不要解释。
- taste_md 和 style_md 必须是更新后的完整 Markdown 文本。
- seed_suggestions_append 只放需要用户确认的新增建议；没有则输出空字符串。
- changes 用中文列出本次消化做了哪些变化。

JSON schema 形状：
{{
  "taste_md": "# 选题口味档案 · tech\\n\\n...",
  "style_md": "# 写作口味档案 · tech\\n\\n...",
  "seed_suggestions_append": "- 建议关注清单新增：...",
  "changes": ["提高 AI 芯片供应链新闻权重", "降低发布会通稿权重"]
}}
""".strip()


def _issue_prompt_from_payload(payload: dict[str, Any]) -> str:
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
- pullquote 默认输出 null。只有原文中存在明确、可溯源、值得突出展示的短引语时，才输出对象 {{"text": "引语正文", "cite": "来源或说话人"}}。
- pullquote 绝不能输出字符串、数组、Markdown 或带破折号的拼接文本；只能是 null 或 {{"text": "...", "cite": "..."}}。

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

请基于原始任务和上一次输出，重新输出唯一一个合法 JSON 对象。

强制格式规则：
1. 输出必须从 {{ 开始，到 }} 结束。
2. 不要 Markdown，不要 ```json 代码块，不要解释文字，不要前后缀。
3. 如果错误涉及 pullquote：pullquote 只能是 null 或对象 {{"text": "引语正文", "cite": "来源或说话人"}}；绝不能是字符串。
4. 优先修正字段类型和 JSON 格式，不要新增未入选条目。

原始任务：
{original_prompt}

上一次输出：
{raw_output}
""".strip()


def build_provider_command(
    provider: ProviderName,
    config: PipelineConfig,
    *,
    schema_path: Path | None = None,
    output_path: Path | None = None,
) -> list[str]:
    if provider == "claude":
        runtime = config.ai.claude
        command = shlex.split(os.getenv("CLAUDE_COMMAND", runtime.command))
        if "--output-format" not in command:
            command.extend(["--output-format", "json"])
        if schema_path:
            command.extend(["--json-schema", schema_path.read_text(encoding="utf-8")])
        if runtime.model:
            command.extend(["--model", runtime.model])
        if runtime.max_budget_usd is not None:
            command.extend(["--max-budget-usd", str(runtime.max_budget_usd)])
        return command

    runtime = config.ai.codex
    command = shlex.split(runtime.command)
    command.extend(["--cd", str(WEB_DIR.parent), "--sandbox", "read-only", "--json"])
    if runtime.model:
        command.extend(["--model", runtime.model])
    if schema_path:
        command.extend(["--output-schema", str(schema_path)])
    if output_path:
        command.extend(["--output-last-message", str(output_path)])
    command.append("-")
    return command


def run_provider(
    provider: ProviderName,
    prompt: str,
    output_model: type[AIOutput],
    config: PipelineConfig,
    *,
    use_output_schema: bool = True,
) -> ProviderRunResult:
    timeout = config.ai.timeout_seconds
    with tempfile.TemporaryDirectory(prefix="daily-news-ai-") as temp_dir:
        temp_path = Path(temp_dir)
        schema_path = temp_path / "schema.json"
        output_path = temp_path / "last-message.txt"
        schema_path.write_text(json.dumps(output_model.model_json_schema(), ensure_ascii=False), encoding="utf-8")
        if provider == "codex":
            command = build_provider_command(
                provider,
                config,
                schema_path=schema_path if use_output_schema else None,
                output_path=output_path,
            )
            completed, duration_ms = _run_command(command, prompt, timeout)
            output_text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else completed.stdout.strip()
            result = _provider_result_from_codex(
                output_text=output_text,
                completed=completed,
                command=command,
                duration_ms=duration_ms,
            )
            return result

        command = build_provider_command(provider, config, schema_path=schema_path if use_output_schema else None)
        completed, duration_ms = _run_command(command, prompt, timeout)
        result = _provider_result_from_claude(completed=completed, command=command, duration_ms=duration_ms)
        return result


def _provider_result_from_claude(
    *,
    completed: subprocess.CompletedProcess[str],
    command: list[str],
    duration_ms: int,
) -> ProviderRunResult:
    envelope = _parse_json_object_or_none(completed.stdout)
    if envelope:
        structured_output = envelope.get("structured_output")
        if isinstance(structured_output, dict):
            output_text = json.dumps(structured_output, ensure_ascii=False)
        else:
            output_text = _first_string(envelope, ["result", "message", "content", "output", "text"]) or completed.stdout.strip()
        usage = _extract_usage_metrics(envelope)
        return ProviderRunResult(
            output_text=output_text,
            stdout=completed.stdout,
            stderr=completed.stderr,
            command=command,
            return_code=completed.returncode,
            duration_ms=_first_int(envelope, ["duration_ms", "duration"]) or duration_ms,
            model=_first_string(envelope, ["model"]),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=usage.get("cache_read_tokens"),
            cache_write_tokens=usage.get("cache_write_tokens"),
            total_tokens=usage.get("total_tokens"),
            cost_usd=_first_float(envelope, ["cost_usd", "total_cost_usd"]),
            provider_events=completed.stdout,
            extra={
                "envelope_keys": sorted(envelope.keys()),
                "used_structured_output": isinstance(structured_output, dict),
            },
        )

    return ProviderRunResult(
        output_text=completed.stdout.strip(),
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=command,
        return_code=completed.returncode,
        duration_ms=duration_ms,
        provider_events=completed.stdout,
    )


def _provider_result_from_codex(
    *,
    output_text: str,
    completed: subprocess.CompletedProcess[str],
    command: list[str],
    duration_ms: int,
) -> ProviderRunResult:
    events = _parse_jsonl(completed.stdout)
    usage = _extract_usage_metrics(events)
    return ProviderRunResult(
        output_text=output_text,
        stdout=completed.stdout,
        stderr=completed.stderr,
        command=command,
        return_code=completed.returncode,
        duration_ms=duration_ms,
        model=_first_string(events, ["model"]),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_read_tokens=usage.get("cache_read_tokens"),
        cache_write_tokens=usage.get("cache_write_tokens"),
        total_tokens=usage.get("total_tokens"),
        cost_usd=_first_float(events, ["cost_usd", "total_cost_usd"]),
        provider_events=completed.stdout,
        extra={"event_count": len(events)},
    )


def _parse_json_object_or_none(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_jsonl(value: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _iter_values(value: Any) -> Any:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_values(child)


def _first_string(value: Any, keys: list[str]) -> str | None:
    for node in _iter_values(value):
        if not isinstance(node, dict):
            continue
        for key in keys:
            item = node.get(key)
            if isinstance(item, str):
                return item
    return None


def _first_int(value: Any, keys: list[str]) -> int | None:
    for node in _iter_values(value):
        if not isinstance(node, dict):
            continue
        for key in keys:
            item = node.get(key)
            if isinstance(item, int):
                return item
            if isinstance(item, float):
                return int(item)
    return None


def _first_float(value: Any, keys: list[str]) -> float | None:
    for node in _iter_values(value):
        if not isinstance(node, dict):
            continue
        for key in keys:
            item = node.get(key)
            if isinstance(item, (int, float)):
                return float(item)
    return None


def _extract_usage_metrics(value: Any) -> dict[str, int | None]:
    metrics: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "total_tokens": None,
    }
    aliases = {
        "input_tokens": ["input_tokens", "prompt_tokens"],
        "output_tokens": ["output_tokens", "completion_tokens"],
        "cache_read_tokens": ["cache_read_input_tokens", "cache_read_tokens"],
        "cache_write_tokens": ["cache_creation_input_tokens", "cache_write_tokens"],
        "total_tokens": ["total_tokens"],
    }
    for metric_name, keys in aliases.items():
        metrics[metric_name] = _first_int(value, keys)
    if metrics["total_tokens"] is None:
        parts = [
            metrics["input_tokens"],
            metrics["output_tokens"],
            metrics["cache_read_tokens"],
            metrics["cache_write_tokens"],
        ]
        if any(part is not None for part in parts):
            metrics["total_tokens"] = sum(part or 0 for part in parts)
    return metrics


def _redact_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    sensitive_flags = {"--api-key", "--key", "--token", "--auth-token"}
    for part in command:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        if part in sensitive_flags:
            redacted.append(part)
            redact_next = True
            continue
        if any(marker in part.lower() for marker in ["api_key=", "apikey=", "token=", "secret="]):
            redacted.append("[REDACTED]")
            continue
        redacted.append(part)
    return redacted


def _parsed_output_chars(parsed_output: dict[str, Any] | None) -> int | None:
    if parsed_output is None:
        return None
    return len(json.dumps(parsed_output, ensure_ascii=False))


def _attempt_payload(
    *,
    task_type: str,
    provider: ProviderName,
    config: PipelineConfig,
    prompt: str,
    result: ProviderRunResult | None,
    started_at: datetime,
    finished_at: datetime,
    status: Literal["success", "failed"],
    error: str | None,
) -> dict[str, Any]:
    command = result.command if result else []
    return {
        "task_type": task_type,
        "provider": provider,
        "model": result.model if result else None,
        "status": status,
        "error": error,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": result.duration_ms if result else int((finished_at - started_at).total_seconds() * 1000),
        "command": _redact_command(command) if config.logging.redact_command else command,
        "return_code": result.return_code if result else None,
        "prompt_chars": len(prompt),
        "raw_output_chars": len(result.output_text) if result else 0,
        "input_tokens": result.input_tokens if result else None,
        "output_tokens": result.output_tokens if result else None,
        "cache_read_tokens": result.cache_read_tokens if result else None,
        "cache_write_tokens": result.cache_write_tokens if result else None,
        "total_tokens": result.total_tokens if result else None,
        "cost_usd": result.cost_usd if result else None,
    }


def _run_command(command: list[str], prompt: str, timeout: int) -> tuple[subprocess.CompletedProcess[str], int]:
    started = time.perf_counter()
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
        raise AIEngineError(f"AI command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AIEngineError(f"AI command timed out after {timeout}s") from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    return completed, duration_ms


def run_ai_task(
    *,
    task_type: str,
    prompt: str,
    output_model: type[AIOutput],
    provider: ProviderName,
    config: PipelineConfig,
    use_output_schema: bool = True,
) -> tuple[AIOutput, AIRunRecord]:
    attempts = max(config.ai.repair_attempts, 0) + 1
    current_prompt = prompt
    last_raw_output = ""
    last_error: Exception | None = None
    attempt_records: list[dict[str, Any]] = []
    run_started_at = datetime.now(timezone.utc)

    for attempt in range(attempts):
        started_at = datetime.now(timezone.utc)
        provider_result: ProviderRunResult | None = None
        try:
            provider_result = run_provider(
                provider,
                current_prompt,
                output_model,
                config,
                use_output_schema=use_output_schema,
            )
            if provider_result.return_code != 0:
                raise AIEngineError(
                    f"AI command failed with code {provider_result.return_code}: {provider_result.stderr.strip()}"
                )
            raw_output = provider_result.output_text
            last_raw_output = raw_output
            parsed = extract_json_object(raw_output)
            output = output_model.model_validate(parsed)
            finished_at = datetime.now(timezone.utc)
            parsed_output = output.model_dump(mode="json")
            attempt_records.append(
                _attempt_payload(
                    task_type=task_type if attempt == 0 else f"{task_type}_repair",
                    provider=provider,
                    config=config,
                    prompt=current_prompt,
                    result=provider_result,
                    started_at=started_at,
                    finished_at=finished_at,
                    status="success",
                    error=None,
                )
            )
            run_task_type = task_type if attempt == 0 else f"{task_type}_repair"
            return output, AIRunRecord(
                task_type=run_task_type,
                prompt_version=PROMPT_VERSION,
                prompt=current_prompt,
                raw_output=raw_output,
                parsed_output=parsed_output,
                status="success",
                started_at=run_started_at,
                finished_at=finished_at,
                provider=provider,
                model=provider_result.model,
                attempt_count=len(attempt_records),
                repair_used=attempt > 0,
                duration_ms=int((finished_at - run_started_at).total_seconds() * 1000),
                command=_redact_command(provider_result.command) if config.logging.redact_command else provider_result.command,
                return_code=provider_result.return_code,
                prompt_chars=len(current_prompt),
                raw_output_chars=len(raw_output),
                parsed_output_chars=_parsed_output_chars(parsed_output),
                input_tokens=provider_result.input_tokens,
                output_tokens=provider_result.output_tokens,
                cache_read_tokens=provider_result.cache_read_tokens,
                cache_write_tokens=provider_result.cache_write_tokens,
                total_tokens=provider_result.total_tokens,
                cost_usd=provider_result.cost_usd,
                attempts=attempt_records,
                provider_events=provider_result.provider_events,
            )
        except Exception as exc:  # noqa: BLE001 - persisted for debug.
            last_error = exc
            finished_at = datetime.now(timezone.utc)
            attempt_records.append(
                _attempt_payload(
                    task_type=task_type if attempt == 0 else f"{task_type}_repair",
                    provider=provider,
                    config=config,
                    prompt=current_prompt,
                    result=provider_result,
                    started_at=started_at,
                    finished_at=finished_at,
                    status="failed",
                    error=str(exc),
                )
            )
            if attempt >= attempts - 1:
                final_attempt = attempt_records[-1]
                record = AIRunRecord(
                    task_type=task_type,
                    prompt_version=PROMPT_VERSION,
                    prompt=current_prompt,
                    raw_output=last_raw_output,
                    parsed_output=None,
                    status="failed",
                    error=str(exc),
                    started_at=run_started_at,
                    finished_at=finished_at,
                    provider=provider,
                    model=final_attempt.get("model"),
                    attempt_count=len(attempt_records),
                    repair_used=len(attempt_records) > 1,
                    duration_ms=int((finished_at - run_started_at).total_seconds() * 1000),
                    command=final_attempt.get("command") or [],
                    return_code=final_attempt.get("return_code"),
                    prompt_chars=len(current_prompt),
                    raw_output_chars=len(last_raw_output),
                    parsed_output_chars=None,
                    input_tokens=final_attempt.get("input_tokens"),
                    output_tokens=final_attempt.get("output_tokens"),
                    cache_read_tokens=final_attempt.get("cache_read_tokens"),
                    cache_write_tokens=final_attempt.get("cache_write_tokens"),
                    total_tokens=final_attempt.get("total_tokens"),
                    cost_usd=final_attempt.get("cost_usd"),
                    attempts=attempt_records,
                    provider_events=provider_result.provider_events if provider_result else None,
                )
                raise AIEngineError(str(exc), record=record) from exc
            current_prompt = build_repair_prompt(prompt, last_raw_output, str(exc))

    raise AIEngineError(str(last_error))


def run_claude(prompt: str, *, timeout_seconds: int | None = None) -> str:
    config = PipelineConfig()
    config.ai.timeout_seconds = timeout_seconds or int(os.getenv("DAILY_NEWS_AI_TIMEOUT_SECONDS", "300"))
    return run_provider("claude", prompt, AIIssueOutput, config).output_text

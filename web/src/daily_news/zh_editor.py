from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daily_news.models import AIIssueOutput, BriefArticle, CandidateItem, HeadlineArticle, Issue


NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[$¥€£])?\d+(?:,\d{3})*(?:\.\d+)?(?:%|年|月|日|小时|分钟|秒|亿|万|千|百|倍|个|家|项|名|岁)?",
    re.IGNORECASE,
)
LATIN_ENTITY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[+_.-][A-Za-z0-9]+)*(?![A-Za-z0-9])")
CJK_NUMBER_RE = re.compile(
    r"[一二三四五六七八九十百千万亿两半]+(?:分之一|倍|成|年|月|日|小时|分钟|秒|家|名|座|款)"
)
ENGLISH_MONTH_NUMBERS = {
    "january": "1",
    "february": "2",
    "march": "3",
    "april": "4",
    "may": "5",
    "june": "6",
    "july": "7",
    "august": "8",
    "september": "9",
    "october": "10",
    "november": "11",
    "december": "12",
}

# 无额外中文 NER 依赖时的保守守卫：覆盖常见国家、机构和科技主体。
PROTECTED_CHINESE_ENTITIES = {
    "中国",
    "美国",
    "英国",
    "法国",
    "德国",
    "日本",
    "韩国",
    "欧盟",
    "俄罗斯",
    "印度",
    "白宫",
    "法院",
    "政府",
    "财政部",
    "商务部",
    "国防部",
    "五角大楼",
    "苹果",
    "谷歌",
    "微软",
    "亚马逊",
    "英伟达",
    "英特尔",
    "三星",
    "索尼",
    "特斯拉",
    "华为",
    "字节跳动",
    "阿里巴巴",
    "腾讯",
    "百度",
    "小米",
    "宁德时代",
}
CHINESE_ENTITY_ALIASES = {
    "苹果": {"apple"},
    "谷歌": {"google", "alphabet"},
    "微软": {"microsoft"},
    "亚马逊": {"amazon"},
    "英伟达": {"nvidia"},
    "英特尔": {"intel"},
    "三星": {"samsung"},
    "索尼": {"sony"},
    "特斯拉": {"tesla"},
}
REQUIRED_QUALIFIER_TOKENS = {
    "计划",
    "目标",
    "预计",
    "可能",
    "据称",
    "传闻",
}


@dataclass(frozen=True)
class HumanizeValidationReport:
    valid: bool
    fallback_used: bool
    violations: list[str]
    checks: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "fallback_used": self.fallback_used,
            "violations": self.violations,
            "checks": self.checks,
        }


def issue_to_ai_output(issue: Issue) -> AIIssueOutput:
    return AIIssueOutput(
        headlines=issue.headlines,
        briefs=issue.briefs,
        discarded=issue.discarded,
        merged_sources=issue.merged_sources,
    )


def with_ai_output(issue: Issue, output: AIIssueOutput) -> Issue:
    return issue.model_copy(
        update={
            "headlines": output.headlines,
            "briefs": output.briefs,
            "discarded": output.discarded,
            "merged_sources": output.merged_sources,
        }
    )


def _headline_text(article: HeadlineArticle) -> str:
    return "\n".join([article.title_zh, article.summary_zh, *article.read_body_zh, article.ai_impact])


def _brief_text(article: BriefArticle) -> str:
    return "\n".join([article.title_zh, article.summary_zh])


def _arabic_numbers(text: str) -> set[str]:
    values: set[str] = set()
    for match in NUMBER_RE.finditer(text):
        numeric = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", match.group(0))
        if numeric:
            values.add(numeric.group(0).replace(",", ""))
    return values


def _source_arabic_numbers(text: str) -> set[str]:
    values = _arabic_numbers(text)
    lowered = text.casefold()
    values.update(number for month, number in ENGLISH_MONTH_NUMBERS.items() if month in lowered)
    return values


def _numbers(text: str) -> set[str]:
    arabic = _arabic_numbers(text)
    cjk = {match.group(0) for match in CJK_NUMBER_RE.finditer(text)}
    return arabic | cjk


def _latin_entities(text: str) -> set[str]:
    entities: set[str] = set()
    for match in LATIN_ENTITY_RE.finditer(text):
        value = match.group(0).casefold()
        entities.add(value)
        entities.update(part for part in re.split(r"[+_.-]", value) if part and part[0].isalpha())
    return entities


def _chinese_entities(text: str) -> set[str]:
    return {entity for entity in PROTECTED_CHINESE_ENTITIES if entity in text}


def _required_qualifiers(text: str) -> set[str]:
    qualifiers = {
        token
        for token in REQUIRED_QUALIFIER_TOKENS - {"目标"}
        if token in text
    }
    if re.search(r"目标(?:是|为)|以[^`。！？]{0,20}为目标", text):
        qualifiers.add("目标")
    return qualifiers


def _locked_headline(article: HeadlineArticle) -> dict[str, Any]:
    return article.model_dump(
        mode="json",
        exclude={"title_zh", "summary_zh", "read_body_zh", "ai_impact"},
    )


def _locked_brief(article: BriefArticle) -> dict[str, Any]:
    return article.model_dump(mode="json", exclude={"title_zh", "summary_zh"})


def _validate_article_facts(
    original_text: str,
    edited_text: str,
    *,
    label: str,
    violations: list[str],
) -> None:
    new_numbers = _numbers(edited_text) - _numbers(original_text)
    if new_numbers:
        violations.append(f"{label}: 新增数字 {sorted(new_numbers)}")

    new_latin_entities = _latin_entities(edited_text) - _latin_entities(original_text)
    if new_latin_entities:
        violations.append(f"{label}: 新增英文/混合主体 {sorted(new_latin_entities)}")

    new_chinese_entities = _chinese_entities(edited_text) - _chinese_entities(original_text)
    if new_chinese_entities:
        violations.append(f"{label}: 新增关键中文主体 {sorted(new_chinese_entities)}")

    missing_qualifiers = _required_qualifiers(original_text) - _required_qualifiers(edited_text)
    if missing_qualifiers:
        violations.append(
            f"{label}: 丢失事实限定词 {sorted(missing_qualifiers)}"
        )
    if "必然" in edited_text and "必然" not in original_text:
        violations.append(f"{label}: 新增确定性表达 ['必然']")


def validate_humanized_output(
    draft: AIIssueOutput,
    edited: AIIssueOutput,
) -> HumanizeValidationReport:
    violations: list[str] = []
    if len(draft.headlines) != len(edited.headlines):
        violations.append(f"头条数量改变: {len(draft.headlines)} -> {len(edited.headlines)}")
    if len(draft.briefs) != len(edited.briefs):
        violations.append(f"速览数量改变: {len(draft.briefs)} -> {len(edited.briefs)}")
    if draft.discarded != edited.discarded:
        violations.append("discarded 被修改")
    if draft.merged_sources != edited.merged_sources:
        violations.append("merged_sources 被修改")

    for index, (original, revised) in enumerate(zip(draft.headlines, edited.headlines, strict=False), start=1):
        label = f"头条 {index}"
        if _locked_headline(original) != _locked_headline(revised):
            violations.append(f"{label}: 可编辑字段以外发生变化")
        _validate_article_facts(
            _headline_text(original),
            _headline_text(revised),
            label=label,
            violations=violations,
        )

    for index, (original, revised) in enumerate(zip(draft.briefs, edited.briefs, strict=False), start=1):
        label = f"速览 {index}"
        if _locked_brief(original) != _locked_brief(revised):
            violations.append(f"{label}: 可编辑字段以外发生变化")
        _validate_article_facts(
            _brief_text(original),
            _brief_text(revised),
            label=label,
            violations=violations,
        )

    return HumanizeValidationReport(
        valid=not violations,
        fallback_used=bool(violations),
        violations=violations,
        checks={
            "structure_and_locked_fields": "passed" if not any("数量" in v or "以外" in v or "discarded" in v or "merged_sources" in v for v in violations) else "failed",
            "no_new_numbers": "passed" if not any("新增数字" in v for v in violations) else "failed",
            "no_new_entities": "passed" if not any("新增英文" in v or "新增关键中文" in v for v in violations) else "failed",
            "modality_preserved": "passed" if not any("限定词" in v or "确定性" in v for v in violations) else "failed",
        },
    )


def guarded_humanized_output(
    draft: AIIssueOutput,
    edited: AIIssueOutput,
) -> tuple[AIIssueOutput, HumanizeValidationReport]:
    report = validate_humanized_output(draft, edited)
    return (edited if report.valid else draft), report


def guarded_hybrid_output(
    draft: AIIssueOutput,
    edited: AIIssueOutput,
) -> tuple[AIIssueOutput, HumanizeValidationReport]:
    """Apply a moderately free rewrite, falling back only articles that break factual anchors."""
    base_report = validate_humanized_output(draft, edited)
    violations = list(base_report.violations)

    for index, (original, revised) in enumerate(zip(draft.headlines, edited.headlines, strict=False), start=1):
        label = f"头条 {index}"
        missing_numbers = _numbers(_headline_text(original)) - _numbers(_headline_text(revised)) - {"一款"}
        if missing_numbers:
            violations.append(f"{label}: 删除事实稿数字 {sorted(missing_numbers)}")
        title_entities = (
            _latin_entities(original.title_zh) - {"ai", "the", "a", "an"}
        ) | _chinese_entities(original.title_zh)
        revised_entities = _latin_entities(_headline_text(revised)) | _chinese_entities(_headline_text(revised))
        missing_title_entities = title_entities - revised_entities
        if missing_title_entities:
            violations.append(f"{label}: 删除标题主要主体 {sorted(missing_title_entities)}")

    for index, (original, revised) in enumerate(zip(draft.briefs, edited.briefs, strict=False), start=1):
        label = f"速览 {index}"
        missing_numbers = _numbers(_brief_text(original)) - _numbers(_brief_text(revised)) - {"一款"}
        if missing_numbers:
            violations.append(f"{label}: 删除事实稿数字 {sorted(missing_numbers)}")
        title_entities = (
            _latin_entities(original.title_zh) - {"ai", "the", "a", "an"}
        ) | _chinese_entities(original.title_zh)
        revised_entities = _latin_entities(_brief_text(revised)) | _chinese_entities(_brief_text(revised))
        missing_title_entities = title_entities - revised_entities
        if missing_title_entities:
            violations.append(f"{label}: 删除标题主要主体 {sorted(missing_title_entities)}")

    global_failure = any(
        violation.startswith("头条数量")
        or violation.startswith("速览数量")
        or violation.startswith("discarded")
        or violation.startswith("merged_sources")
        for violation in violations
    )
    fallback_articles = sorted(
        {
            violation.split(":", 1)[0]
            for violation in violations
            if violation.startswith("头条 ") or violation.startswith("速览 ")
        }
    )
    if global_failure:
        final_output = draft
        fallback_articles = ["整期"]
    else:
        invalid_headlines = {
            int(label.split()[1])
            for label in fallback_articles
            if label.startswith("头条 ")
        }
        invalid_briefs = {
            int(label.split()[1])
            for label in fallback_articles
            if label.startswith("速览 ")
        }
        final_output = edited.model_copy(
            update={
                "headlines": [
                    original if index in invalid_headlines else revised
                    for index, (original, revised) in enumerate(
                        zip(draft.headlines, edited.headlines, strict=False), start=1
                    )
                ],
                "briefs": [
                    original if index in invalid_briefs else revised
                    for index, (original, revised) in enumerate(
                        zip(draft.briefs, edited.briefs, strict=False), start=1
                    )
                ],
                "discarded": draft.discarded,
                "merged_sources": draft.merged_sources,
            }
        )

    final_report = validate_humanized_output(draft, final_output)
    final_valid = final_report.valid and len(final_output.headlines) == len(draft.headlines) and len(final_output.briefs) == len(draft.briefs)
    return final_output, HumanizeValidationReport(
        valid=final_valid,
        fallback_used=bool(fallback_articles),
        violations=violations,
        checks={
            "final_output_valid": final_valid,
            "fallback_articles": fallback_articles,
            "candidate_violation_count": len(violations),
            "per_article_fallback": not global_failure,
        },
    )


def validate_variant_selection(reference: Issue, candidate: Issue) -> list[str]:
    """Ensure an offline rewrite still represents the exact same selected articles."""
    violations: list[str] = []
    reference_headlines = [article.source_item_ids for article in reference.headlines]
    candidate_headlines = [article.source_item_ids for article in candidate.headlines]
    reference_briefs = [article.source_item_ids for article in reference.briefs]
    candidate_briefs = [article.source_item_ids for article in candidate.briefs]
    if reference_headlines != candidate_headlines:
        violations.append("头条来源 ID、数量或顺序改变")
    if reference_briefs != candidate_briefs:
        violations.append("速览来源 ID、数量或顺序改变")
    return violations


def validate_variant_against_sources(
    reference: Issue,
    candidate: Issue,
    source_candidates: list[CandidateItem],
) -> list[str]:
    """Check selection, source attribution, numerals and lexical entities for a fresh compose variant."""
    violations = validate_variant_selection(reference, candidate)
    source_by_id = {item.raw_item.id: item.raw_item for item in source_candidates}

    def check_article(reference_article: Any, candidate_article: Any, label: str, text: str) -> None:
        selected_sources = [source_by_id[item_id] for item_id in candidate_article.source_item_ids if item_id in source_by_id]
        if len(selected_sources) != len(candidate_article.source_item_ids):
            violations.append(f"{label}: 引用了候选文件中不存在的来源 ID")
            return
        allowed_urls = {item.url for item in selected_sources} | {source.url for source in reference_article.sources}
        allowed_names = {item.source_name for item in selected_sources} | {
            source.name for source in reference_article.sources
        }
        if any(source.url not in allowed_urls or source.name not in allowed_names for source in candidate_article.sources):
            violations.append(f"{label}: 新增或替换了来源署名")

        source_text = "\n".join(
            part
            for item in selected_sources
            for part in (item.title, item.summary or "", item.content or "")
            if part
        )
        reference_text = (
            _headline_text(reference_article)
            if isinstance(reference_article, HeadlineArticle)
            else _brief_text(reference_article)
        )
        allowed_text = source_text + "\n" + reference_text
        new_numbers = _arabic_numbers(text) - _source_arabic_numbers(allowed_text)
        if new_numbers:
            violations.append(f"{label}: 相对候选原文和基线新增数字 {sorted(new_numbers)}")
        new_latin_entities = _latin_entities(text) - _latin_entities(allowed_text)
        if new_latin_entities:
            violations.append(
                f"{label}: 相对候选原文和基线新增英文/混合主体 {sorted(new_latin_entities)}"
            )
        allowed_chinese_entities = _chinese_entities(allowed_text)
        allowed_latin_entities = _latin_entities(allowed_text)
        for entity, aliases in CHINESE_ENTITY_ALIASES.items():
            if aliases & allowed_latin_entities:
                allowed_chinese_entities.add(entity)
        new_chinese_entities = _chinese_entities(text) - allowed_chinese_entities
        if new_chinese_entities:
            violations.append(
                f"{label}: 相对候选原文和基线新增关键中文主体 {sorted(new_chinese_entities)}"
            )

    for index, (reference_article, candidate_article) in enumerate(
        zip(reference.headlines, candidate.headlines, strict=False), start=1
    ):
        check_article(reference_article, candidate_article, f"头条 {index}", _headline_text(candidate_article))
    for index, (reference_article, candidate_article) in enumerate(
        zip(reference.briefs, candidate.briefs, strict=False), start=1
    ):
        check_article(reference_article, candidate_article, f"速览 {index}", _brief_text(candidate_article))
    return violations


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_blind_mapping(run_id: str) -> dict[str, str]:
    variants = ["A", "B", "C"]
    seed = int(hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16], 16)
    random.Random(seed).shuffle(variants)
    return dict(zip(["方案甲", "方案乙", "方案丙"], variants, strict=True))


def build_blind_review(
    variants: dict[str, Issue],
    mapping: dict[str, str],
    *,
    headline_limit: int = 4,
    brief_limit: int = 6,
) -> str:
    lines = [
        "# Codex 中文编辑盲评稿",
        "",
        "说明：三个方案使用同一批候选与选题。请先不看方案映射，按 1–5 分评价；AI 腔分数越高表示问题越重。",
        "",
        "| 方案 | 自然度 | 易懂度 | 信息完整度 | AI 腔 |",
        "|---|---:|---:|---:|---:|",
    ]
    lines.extend([f"| {label} |  |  |  |  |" for label in mapping])
    lines.append("")

    label_variants = [(label, variants[variant]) for label, variant in mapping.items()]
    baseline = variants.get("A") or next(iter(variants.values()))
    for index in range(min(headline_limit, len(baseline.headlines))):
        lines.extend([f"## 头条 {index + 1}", ""])
        for label, issue in label_variants:
            article = issue.headlines[index]
            lines.extend(
                [
                    f"### {label}",
                    "",
                    f"**标题：**{article.title_zh}",
                    "",
                    f"**摘要：**{article.summary_zh}",
                    "",
                    f"**影响分析：**{article.ai_impact}",
                    "",
                ]
            )

    for index in range(min(brief_limit, len(baseline.briefs))):
        lines.extend([f"## 速览 {index + 1}", ""])
        for label, issue in label_variants:
            article = issue.briefs[index]
            lines.extend(
                [
                    f"### {label}",
                    "",
                    f"**标题：**{article.title_zh}",
                    "",
                    f"**摘要：**{article.summary_zh}",
                    "",
                ]
            )

    lines.extend(
        [
            "## 补充记录",
            "",
            "- 最喜欢的方案：",
            "- 最别扭的句子：",
            "- 有遗漏或改变原意的地方：",
            "",
        ]
    )
    return "\n".join(lines)

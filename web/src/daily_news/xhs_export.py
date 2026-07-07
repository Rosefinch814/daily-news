from __future__ import annotations

import html
import logging
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from daily_news.ai_engine import (
    AIEngineError,
    ProviderName,
    XHSCondenseOutput,
    build_xhs_condense_prompt,
    run_ai_task,
)
from daily_news.config import PipelineConfig
from daily_news.models import BriefArticle, HeadlineArticle, Issue
from daily_news.paths import DIST_DIR, RUNS_DIR
from daily_news.storage.local import save_ai_task_run


CARD_WIDTH = 1080
CARD_HEIGHT = 1440
XHS_PUBLICATION_NAME = "AI科技日报"
MAX_HEADLINE_CARDS = 3
BRIEF_PAGE_MAX_ITEMS = 5
BRIEF_LIST_HEIGHT_LIMIT = 1080
BRIEF_ITEM_SOFT_LIMIT = 260
HASHTAGS = f"#{XHS_PUBLICATION_NAME} #科技日报 #AI日报 #人工智能 #科技资讯"
WEEKDAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
LOGGER = logging.getLogger(__name__)

SlotType = Literal["headline_summary", "headline_impact", "brief_summary"]
SLOT_RANGES: dict[SlotType, tuple[int, int]] = {
    "headline_summary": (90, 155),
    "headline_impact": (85, 145),
    "brief_summary": (22, 52),
}


@dataclass(frozen=True)
class XHSExportResult:
    output_dir: Path
    image_paths: list[Path]
    caption_path: Path
    html_path: Path


@dataclass(frozen=True)
class Card:
    kind: str
    html_body: str


@dataclass(frozen=True)
class CondenseRequest:
    slot_type: SlotType
    title: str
    original_text: str
    min_chars: int
    max_chars: int


class XHSCondenser:
    def __init__(self, issue: Issue, *, config: PipelineConfig, provider: ProviderName | None = None) -> None:
        self.config = config
        self.provider: ProviderName = provider or config.ai.stage_providers.get("xhs_condense") or config.ai.default_provider
        self.run_id = f"xhs-{issue.issue_date.isoformat()}"
        self.cache: dict[CondenseRequest, str] = {}
        self.sequence = 0

    def condense(self, request: CondenseRequest, fallback: str) -> str:
        if request in self.cache:
            return self.cache[request]
        if original_text_is_in_range(request.original_text, request.min_chars, request.max_chars):
            self.cache[request] = finish_complete_text(compact_text(request.original_text))
            return self.cache[request]

        payload = {
            "slot_type": request.slot_type,
            "title": request.title,
            "original_text": compact_text(request.original_text),
            "target_min": request.min_chars,
            "target_max": request.max_chars,
        }
        self.sequence += 1
        stage = f"xhs_condense_{self.sequence:02d}"
        try:
            output, ai_run = run_ai_task(
                task_type="xhs_condense",
                prompt=build_xhs_condense_prompt(payload),
                output_model=XHSCondenseOutput,
                provider=self.provider,
                config=self.config,
            )
            save_ai_task_run(
                self.run_id,
                stage,
                ai_run,
                save_attempts=self.config.logging.save_attempts,
                save_provider_events=self.config.logging.save_provider_events,
                append_metrics_jsonl=self.config.logging.append_metrics_jsonl,
            )
            candidate = finish_complete_text(compact_text(output.text))
            if is_valid_condensed_text(candidate, request):
                self.cache[request] = candidate
                return candidate
            LOGGER.warning("xhs_condense output rejected for %s: %s", request.slot_type, candidate)
        except AIEngineError as exc:
            if exc.record is not None:
                failed_record = exc.record
                save_ai_task_run(
                    self.run_id,
                    stage,
                    failed_record,
                    save_attempts=self.config.logging.save_attempts,
                    save_provider_events=self.config.logging.save_provider_events,
                    append_metrics_jsonl=self.config.logging.append_metrics_jsonl,
                )
            LOGGER.warning("xhs_condense failed for %s: %s", request.slot_type, exc)

        self.cache[request] = fallback
        return fallback


def load_issue_for_xhs(issue_date: str, *, dist_dir: Path = DIST_DIR) -> Issue:
    issue_path = dist_dir / "data" / "issues" / f"{issue_date}.json"
    if not issue_path.exists():
        raise FileNotFoundError(f"Issue JSON not found: {issue_path}")
    return Issue.model_validate_json(issue_path.read_text(encoding="utf-8"))


def export_xhs_issue(
    issue: Issue,
    *,
    output_dir: Path | None = None,
    config: PipelineConfig | None = None,
    ai_condense: bool = False,
    provider: ProviderName | None = None,
) -> XHSExportResult:
    out_dir = output_dir or RUNS_DIR / "xhs" / issue.issue_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()

    condenser = XHSCondenser(issue, config=config, provider=provider) if ai_condense and config else None
    cards = build_cards(issue, condenser=condenser)
    html_path = out_dir / "cards.html"
    html_path.write_text(render_cards_html(issue, cards), encoding="utf-8")
    caption_path = out_dir / "caption.txt"
    caption_path.write_text(build_caption(issue), encoding="utf-8")
    image_paths = render_card_images(html_path, out_dir, len(cards))
    return XHSExportResult(
        output_dir=out_dir,
        image_paths=image_paths,
        caption_path=caption_path,
        html_path=html_path,
    )


def build_cards(issue: Issue, *, condenser: XHSCondenser | None = None) -> list[Card]:
    cards = [cover_card(issue)]
    for index, article in enumerate(issue.headlines[:MAX_HEADLINE_CARDS], start=1):
        cards.append(headline_card(issue, article, index, condenser=condenser))
    cards.extend(brief_cards(issue, condenser=condenser))
    return cards


def cover_card(issue: Issue) -> Card:
    headline_count = min(len(issue.headlines), MAX_HEADLINE_CARDS)
    headline_items = "\n".join(
        f"""
        <div class="hl">
          <div class="no">{idx:02d}</div>
          <div class="t">{escape(article.title_zh)}</div>
        </div>
        """
        for idx, article in enumerate(issue.headlines[:MAX_HEADLINE_CARDS], start=1)
    )
    body = f"""
      <div class="cv-top">
        <span class="pill">{headline_count} 条头条</span>
        <span class="pill">{len(issue.briefs)} 条速览</span>
        <span class="pill">约 {estimate_reading_minutes(issue)} 分钟</span>
      </div>
      <div class="datewrap"><span class="date">{escape(date_dot(issue))}</span><span class="dow">{escape(weekday_cn(issue))}</span></div>
      <h1 class="title">{escape(XHS_PUBLICATION_NAME)}</h1>
      <div class="rule"></div>
      <div class="lead-label">今日头条</div>
      {headline_items}
      <div class="swipe">
        <span>左滑翻阅</span>
        <svg width="66" height="22" viewBox="0 0 66 22" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M6 4l7 7-7 7"/><path d="M25 4l7 7-7 7"/><path d="M44 4l7 7-7 7"/>
        </svg>
      </div>
    """
    return Card(kind="cover", html_body=body)


def headline_card(
    issue: Issue,
    article: HeadlineArticle,
    index: int,
    *,
    condenser: XHSCondenser | None = None,
) -> Card:
    total = min(len(issue.headlines), MAX_HEADLINE_CARDS)
    summary_min, summary_max = SLOT_RANGES["headline_summary"]
    impact_min, impact_max = SLOT_RANGES["headline_impact"]
    kicker = f'<div class="kicker">{escape(article.kicker)}</div>' if article.kicker else ""
    body = f"""
      <div class="topbar"><span class="l">HEADLINE {index}</span><span>{escape(date_dot(issue))}</span></div>
      <div class="hl-body">
        {kicker}
        <h2>{escape(article.title_zh)}</h2>
        <div class="fact">
          <div class="label">发生了什么</div>
          <p>{escape(condense_slot(article.summary_zh, slot_type="headline_summary", min_chars=summary_min, max_chars=summary_max, title=article.title_zh, condenser=condenser))}</p>
        </div>
        <div class="impact">
          <div class="label"><span class="chip">AI</span>为什么重要 · AI 分析</div>
          <p>{escape(condense_slot(article.ai_impact, slot_type="headline_impact", min_chars=impact_min, max_chars=impact_max, title=article.title_zh, condenser=condenser))}</p>
        </div>
        <div class="src">来源 · {escape(source_names(article.sources))}</div>
      </div>
      <div class="foot"><span>{escape(XHS_PUBLICATION_NAME)} · {escape(date_dot(issue))}</span><span class="pg">头条 {index} / {total}</span></div>
    """
    return Card(kind="headline", html_body=body)


def brief_cards(issue: Issue, *, condenser: XHSCondenser | None = None) -> list[Card]:
    pages = paginate_briefs(issue.briefs, condenser=condenser)
    cards: list[Card] = []
    start_no = 1
    for page_index, page_items in enumerate(pages, start=1):
        items_html = "\n".join(
            brief_item_html(article, start_no + offset, condenser=condenser)
            for offset, article in enumerate(page_items)
        )
        body = f"""
          <div class="topbar"><span class="l">BRIEFS</span><span>{escape(date_dot(issue))}</span></div>
          <h2>今日速览</h2>
          <div class="brief-list">
            {items_html}
          </div>
          <div class="foot"><span>{escape(XHS_PUBLICATION_NAME)} · {escape(date_dot(issue))}</span><span class="pg">速览 {page_index} / {len(pages)}</span></div>
        """
        cards.append(Card(kind="briefs", html_body=body))
        start_no += len(page_items)
    return cards


def brief_item_html(article: BriefArticle, number: int, *, condenser: XHSCondenser | None = None) -> str:
    min_chars, max_chars = SLOT_RANGES["brief_summary"]
    summary = condense_slot(
        article.summary_zh,
        slot_type="brief_summary",
        min_chars=min_chars,
        max_chars=max_chars,
        title=article.title_zh,
        condenser=condenser,
    )
    return f"""
      <div class="brief-item">
        <div class="no">{number:02d}</div>
        <div>
          <h3>{escape(article.title_zh)}</h3>
          <p>{escape(summary)}</p>
          <div class="src">来源 · {escape(source_names(article.sources))}</div>
        </div>
      </div>
    """


def build_caption(issue: Issue) -> str:
    headline_lines = "\n".join(
        f"{idx}. {article.title_zh}"
        for idx, article in enumerate(issue.headlines[:MAX_HEADLINE_CARDS], start=1)
    )
    brief_topics = " / ".join(article.title_zh for article in issue.briefs[:5])
    parts = [
        f"{XHS_PUBLICATION_NAME}｜{date_dot(issue)}",
        f"今日 {len(issue.headlines) + len(issue.briefs)} 条，约 {estimate_reading_minutes(issue)} 分钟读完。",
        "",
        "今日头条：",
        headline_lines,
    ]
    if brief_topics:
        parts.extend(["", f"速览还包括：{brief_topics}"])
    parts.extend(["", HASHTAGS, ""])
    return "\n".join(parts)


def render_cards_html(issue: Issue, cards: Sequence[Card]) -> str:
    card_html = "\n".join(
        f"""
        <section id="card-{index}" class="card {card.kind}">
          {card.html_body}
        </section>
        """
        for index, card in enumerate(cards, start=1)
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(XHS_PUBLICATION_NAME)} · 小红书导出</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,900&family=JetBrains+Mono:wght@500;700&family=Noto+Serif+SC:wght@400;500;600;700;900&display=swap" rel="stylesheet">
  <style>{CSS}</style>
</head>
<body>
  <div class="stage">{card_html}</div>
</body>
</html>
"""


def render_card_images(html_path: Path, output_dir: Path, count: int) -> list[Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depends on local tooling
        raise RuntimeError("Playwright is required for export-xhs. Install it and run `playwright install chromium`.") from exc

    image_paths: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": CARD_WIDTH + 120, "height": CARD_HEIGHT + 120},
            device_scale_factor=1,
        )
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        for index in range(1, count + 1):
            output_path = output_dir / f"{index:02d}.png"
            page.locator(f"#card-{index}").screenshot(path=str(output_path))
            image_paths.append(output_path)
        browser.close()
    return image_paths


def paginate_briefs(items: Sequence[BriefArticle], *, condenser: XHSCondenser | None = None) -> list[list[BriefArticle]]:
    pages: list[list[BriefArticle]] = []
    current: list[BriefArticle] = []
    current_height = 0
    for item in items:
        height = estimate_brief_item_height(item, condenser=condenser)
        should_break = (
            current
            and (
                len(current) >= BRIEF_PAGE_MAX_ITEMS
                or current_height + height > BRIEF_LIST_HEIGHT_LIMIT
                or (height > BRIEF_ITEM_SOFT_LIMIT and current_height > BRIEF_LIST_HEIGHT_LIMIT - BRIEF_ITEM_SOFT_LIMIT)
            )
        )
        if should_break:
            pages.append(current)
            current = []
            current_height = 0
        current.append(item)
        current_height += height
    if current:
        pages.append(current)
    return pages


def estimate_brief_item_height(article: BriefArticle, *, condenser: XHSCondenser | None = None) -> int:
    min_chars, max_chars = SLOT_RANGES["brief_summary"]
    summary = condense_slot(
        article.summary_zh,
        slot_type="brief_summary",
        min_chars=min_chars,
        max_chars=max_chars,
        title=article.title_zh,
        condenser=condenser,
    )
    title_lines = max(1, math.ceil(len(article.title_zh) / 22))
    summary_lines = max(1, math.ceil(len(summary) / 27))
    title_height = title_lines * 47
    summary_height = summary_lines * 44
    source_height = 26
    vertical_padding_and_margins = 62
    return title_height + summary_height + source_height + vertical_padding_and_margins


def condense_slot(
    text: str,
    *,
    slot_type: SlotType,
    min_chars: int,
    max_chars: int,
    title: str = "",
    condenser: XHSCondenser | None = None,
) -> str:
    fallback = fallback_condense_slot(text, slot_type=slot_type, min_chars=min_chars, max_chars=max_chars)
    if condenser is None:
        return fallback
    request = CondenseRequest(
        slot_type=slot_type,
        title=title,
        original_text=text,
        min_chars=min_chars,
        max_chars=max_chars,
    )
    return condenser.condense(request, fallback)


def fallback_condense_slot(text: str, *, slot_type: SlotType, min_chars: int, max_chars: int) -> str:
    value = compact_text(text)
    if not value:
        return ""
    if min_chars <= len(value) <= max_chars and ends_complete(value):
        return value
    if len(value) <= max_chars:
        return finish_complete_text(value)

    selected: list[str] = []
    total = 0
    for unit in complete_text_units(value):
        next_total = total + len(unit)
        if selected and next_total > max_chars:
            break
        if not selected and next_total > max_chars:
            selected.append(fit_long_unit(unit, max_chars=max_chars))
            break
        selected.append(unit)
        total = next_total
    return finish_complete_text("".join(selected))


def original_text_is_in_range(text: str, min_chars: int, max_chars: int) -> bool:
    value = compact_text(text)
    return min_chars <= len(value) <= max_chars and ends_complete(value)


def is_valid_condensed_text(text: str, request: CondenseRequest) -> bool:
    if not text or "…" in text or "..." in text:
        return False
    if not ends_complete(text):
        return False
    if len(text) > request.max_chars:
        return False
    if len(compact_text(request.original_text)) >= request.min_chars and len(text) < request.min_chars:
        return False
    return numbers_in_text(text).issubset(numbers_in_text(request.original_text + request.title))


def numbers_in_text(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?%?", text))


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def complete_text_units(text: str) -> list[str]:
    sentences = re.findall(r"[^。！？!?]+[。！？!?]?", text)
    units: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= 72:
            units.append(sentence)
            continue
        semicolon_parts = re.findall(r"[^；;]+[；;]?", sentence)
        for part in semicolon_parts:
            part = part.strip()
            if not part:
                continue
            if len(part) <= 58:
                units.append(part)
            else:
                units.extend(fragment.strip() for fragment in re.findall(r"[^，,]+[，,]?", part) if fragment.strip())
    return units


def fit_long_unit(unit: str, *, max_chars: int) -> str:
    fragments = [fragment.strip() for fragment in re.findall(r"[^，,、：:；;]+[，,、：:；;]?", unit) if fragment.strip()]
    selected: list[str] = []
    total = 0
    for fragment in fragments:
        next_total = total + len(fragment)
        if selected and next_total > max_chars:
            break
        selected.append(fragment)
        total = next_total
    if selected:
        return "".join(selected)
    return unit[:max_chars]


def finish_complete_text(text: str) -> str:
    value = text.strip().rstrip("，,、；;：: ")
    if value and not ends_complete(value):
        value += "。"
    return value


def ends_complete(text: str) -> bool:
    return bool(text) and text[-1] in "。！？!?"


def estimate_reading_minutes(issue: Issue) -> int:
    text = " ".join(
        [article.title_zh + article.summary_zh for article in issue.headlines]
        + [article.title_zh + article.summary_zh for article in issue.briefs]
    )
    return max(2, min(6, math.ceil(len(text) / 520)))


def source_names(sources: Sequence[object]) -> str:
    names: list[str] = []
    for source in sources:
        name = getattr(source, "name", "")
        if name:
            names.append(name)
    return "、".join(names) or "原文来源"


def date_dot(issue: Issue) -> str:
    return issue.issue_date.strftime("%Y.%m.%d")


def weekday_cn(issue: Issue) -> str:
    return WEEKDAYS_CN[issue.issue_date.weekday()]


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def remove_output_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


CSS = """
:root{
  --paper:#F4EFE3; --ink:#1A1714; --ink-2:#3D372F; --ink-3:#5A5247;
  --muted:#8E836F; --rule:#D2C8B2; --rule-2:#B7AB90;
  --seal:#A6342B; --seal-soft:#F0DCD5; --ai-bg:#ECE7DA; --field:#FBF8EF;
  --sans:"Noto Serif SC","Songti SC",serif;
  --disp:"Fraunces","Georgia",serif;
  --mono:"JetBrains Mono",ui-monospace,monospace;
}
*{box-sizing:border-box;}
body{
  margin:0;
  background:#cfc6b2;
  color:var(--ink);
  font-family:var(--sans);
}
.stage{
  display:grid;
  gap:48px;
  padding:60px;
}
.card{
  width:1080px;
  height:1440px;
  background:var(--paper);
  color:var(--ink);
  position:relative;
  overflow:hidden;
}
.card .topbar{
  display:flex;
  justify-content:space-between;
  align-items:center;
  font-family:var(--mono);
  font-size:22px;
  letter-spacing:.14em;
  color:var(--muted);
}
.card .topbar .l{
  color:var(--seal);
  font-weight:700;
}
.card .foot{
  position:absolute;
  left:0;
  right:0;
  bottom:0;
  padding:0 72px 40px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  font-family:var(--mono);
  font-size:20px;
  letter-spacing:.12em;
  color:var(--muted);
}
.card .foot .pg{
  color:var(--seal);
  font-weight:700;
}
.cover{
  padding:88px 84px;
}
.cover .cv-top{
  display:flex;
  justify-content:flex-end;
  flex-wrap:wrap;
  gap:14px;
  margin-bottom:48px;
}
.pill{
  font-family:var(--mono);
  font-weight:700;
  font-size:22px;
  letter-spacing:.06em;
  color:var(--seal);
  background:var(--seal-soft);
  border:1.5px solid var(--seal);
  border-radius:999px;
  padding:11px 22px;
  white-space:nowrap;
}
.cover .datewrap{
  display:flex;
  align-items:center;
  gap:24px;
  margin-bottom:28px;
}
.cover .date{
  font-family:var(--mono);
  font-weight:700;
  font-size:82px;
  letter-spacing:.01em;
  color:var(--seal);
  line-height:1;
}
.cover .dow{
  font-family:var(--sans);
  font-weight:900;
  font-size:46px;
  line-height:1;
  color:var(--paper);
  background:var(--seal);
  border-radius:14px;
  padding:11px 24px;
}
.cover .title{
  font-family:var(--sans);
  font-weight:900;
  font-size:146px;
  line-height:1.02;
  letter-spacing:.03em;
  margin:0 0 6px;
  color:var(--ink);
}
.cover .rule{
  height:8px;
  background:var(--seal);
  width:180px;
  margin:40px 0 56px;
}
.cover .lead-label{
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.2em;
  color:var(--seal);
  margin-bottom:30px;
  text-transform:uppercase;
}
.cover .hl{
  display:flex;
  gap:26px;
  align-items:flex-start;
  padding:26px 0;
  border-top:1.5px solid var(--rule);
}
.cover .hl:last-of-type{
  border-bottom:1.5px solid var(--rule);
}
.cover .hl .no{
  font-family:var(--disp);
  font-weight:900;
  font-size:52px;
  line-height:1;
  color:var(--seal);
  flex:none;
  width:74px;
}
.cover .hl .t{
  font-family:var(--sans);
  font-weight:700;
  font-size:40px;
  line-height:1.28;
  color:var(--ink);
}
.cover .swipe{
  position:absolute;
  left:0;
  right:0;
  bottom:54px;
  display:flex;
  align-items:center;
  justify-content:center;
  gap:16px;
  font-family:var(--mono);
  font-weight:700;
  font-size:24px;
  letter-spacing:.2em;
  color:var(--seal);
  text-transform:uppercase;
}
.cover .swipe svg{
  color:var(--seal);
}
.headline{
  padding:58px 72px 86px;
  display:flex;
  flex-direction:column;
}
.headline .topbar{
  flex:none;
}
.hl-body{
  flex:1 1 auto;
  display:flex;
  flex-direction:column;
  justify-content:center;
  min-height:0;
}
.headline .kicker{
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.16em;
  color:var(--seal);
  margin:0 0 18px;
  text-transform:uppercase;
}
.headline h2{
  font-family:var(--sans);
  font-weight:900;
  font-size:56px;
  line-height:1.18;
  letter-spacing:.01em;
  margin:0 0 14px;
  color:var(--ink);
}
.fact{
  margin-top:32px;
}
.fact .label{
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.13em;
  color:var(--ink-3);
  margin-bottom:16px;
}
.fact p{
  margin:0;
  font-family:var(--sans);
  font-weight:500;
  font-size:36px;
  line-height:1.5;
  color:var(--ink-2);
}
.impact{
  position:relative;
  margin-top:40px;
  padding:30px 36px 34px 40px;
  background:var(--ai-bg);
  border-left:8px solid var(--seal);
}
.impact .label{
  display:flex;
  align-items:center;
  gap:12px;
  font-family:var(--mono);
  font-weight:700;
  font-size:26px;
  letter-spacing:.1em;
  color:var(--seal);
  margin-bottom:16px;
}
.impact .label .chip{
  font-size:19px;
  letter-spacing:.08em;
  color:var(--paper);
  background:var(--seal);
  border-radius:5px;
  padding:3px 10px;
}
.impact p{
  margin:0;
  font-family:var(--sans);
  font-weight:500;
  font-size:34px;
  line-height:1.52;
  color:var(--ink-3);
}
.headline .src{
  margin-top:34px;
  font-family:var(--mono);
  font-size:26px;
  letter-spacing:.06em;
  color:var(--muted);
}
.briefs{
  padding:60px 72px 86px;
}
.briefs h2{
  font-family:var(--sans);
  font-weight:900;
  font-size:66px;
  line-height:1;
  margin:26px 0 24px;
  color:var(--ink);
}
.brief-list{
  border-top:3px solid var(--ink);
  border-bottom:3px solid var(--ink);
}
.brief-item{
  display:grid;
  grid-template-columns:64px 1fr;
  gap:22px;
  padding:22px 0;
  border-bottom:1px solid var(--rule);
  break-inside:avoid;
}
.brief-item:last-child{
  border-bottom:0;
}
.brief-item .no{
  font-family:var(--disp);
  font-weight:900;
  font-size:42px;
  line-height:1;
  color:var(--seal);
}
.brief-item h3{
  margin:0;
  font-family:var(--sans);
  font-weight:700;
  font-size:38px;
  line-height:1.22;
  color:var(--ink);
}
.brief-item p{
  margin:10px 0 8px;
  font-family:var(--sans);
  font-weight:500;
  font-size:31px;
  line-height:1.4;
  color:var(--ink-2);
}
.brief-item .src{
  font-family:var(--mono);
  font-size:20px;
  letter-spacing:.05em;
  color:var(--muted);
}
"""

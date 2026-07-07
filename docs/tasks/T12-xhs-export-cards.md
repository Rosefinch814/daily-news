---
id: T12
title: 小红书导出·卡片模板落地（定稿视觉 + 高度驱动分页 + 文案槽位接缝）
status: done
依赖: []
里程碑: M4
---

## 目标

把 `export-xhs` 的输出改造到**设计定稿**：封面/头条/速览三种竖版卡片按 [design §12](../../design/设计规范.md) + 原型 [design/prototype/xhs-图组-科技日报.html](../../design/prototype/xhs-图组-科技日报.html) 渲染，实现高度驱动分页，并把每个文本槽位收进**一个可替换的收敛接缝**（本工单先给确定性兜底实现；AI 收敛由 T13 接上）。行为权威见 [spec/小红书图组导出.md](../spec/小红书图组导出.md)。

## 范围

1. **重做卡片 HTML/CSS**：以原型为**像素级基准**替换现有 `xhs_export.py` 里那套自造 CSS 与 DOM——封面、头条（方案 A：事实裸通栏主角 + AI 朱红批注卡从属）、速览三种卡，类名/字号/间距对齐 §12 与原型。
2. **CSS 用 §1 token**：颜色/字体全部走 design 规范 §1 基础 token（原型 `:root` 那套 `--paper/--ink/--seal/...`），**零新裸值**（§12.1 和解结论）。
3. **高度驱动分页**：速览按实测高度分页（每页 ≤5，约 4 长/5 短，单条总高 > 页面可用高 ~1/4 即拆下一页），**宁可多一页留白，不硬塞不裁切**（spec §2、§7 附加约束）。
4. **文案接缝**：所有正文槽位经过单一函数 `condense_slot(text, *, slot_type, min_chars, max_chars)` 取值；本工单实现**确定性兜底**版本（收完整句、控在 max 内、不出省略号），保证能出图、字数不溢出。**这是 T13 AI 收敛的注入点**。
5. **字数区间落成可配置常量**：spec §7 的区间冻成 `xhs_export.py` 顶部常量（或小 config），不散写魔法数；真图核定后允许 ±3~5 微调。
6. **caption 同步**：保持 `caption.txt` 输出（§8），刊名/日期文案与定稿一致。

## 涉及文件

- `web/src/daily_news/xhs_export.py`：改 `build_cards`/`cover_card`/`headline_card`/`brief_cards`/`brief_item_html`/`render_cards_html`/`CSS`；**删除** `headline_budgets`/`brief_budget`/`fit_complete_text` 那套规则式预算（由 `condense_slot` 兜底替代）；分页 `paginate_briefs` 改为高度驱动。
- `web/tests/test_xhs_export.py`：更新/新增用例（分页、槽位接缝、无省略号、字数落区间、封面无「今日」）。
- 只动 `web/`；**不改** `docs/`、`design/`。

## 实现要点

- **DOM/类名照原型**：封面 `.cover`（`.cv-top`>`.pill`×3、`.datewrap`>`.date`+`.dow`、`.title`「AI科技日报」单行、`.rule`、`.lead-label`、`.hl`×3、`.swipe`+静态 SVG chevron）；头条 `.headline`（`.topbar`、`.hl-body` flex 竖向居中、`.kicker`、`h2`、`.fact`、`.impact`>`.label`>`.chip"AI"`、`.src`、`.foot` 页码）；速览 `.briefs`（`.topbar`、`h2`、`.brief-list` 粗线夹栏、`.brief-item` grid、`.foot`）。
- **刊名收成单一常量**：`XHS_PUBLICATION_NAME = "AI科技日报"`（`xhs_export.py` 顶部）——封面 `.title`、页脚、caption、hashtag **全引用它**，改名只动一处；**不复用** `issue.publication_name`（那是「我的日报·科技」，与小红书刊名不同）。
- **封面去「今日」**：刊名只出「AI科技日报」，日期承载新鲜度（spec §4）。日期/刊名落上部方形安全区。
- **页脚**：`AI科技日报 · 日期` + 页码（`头条 1 / 3`、`速览 1 / 3`），封面不用 topbar、用 `.swipe`。
- **kicker/星期/分钟等派生字段**：kicker（如「半导体 · 存储」）若日报 JSON 无对应字段，**不要 AI 现编**——缺则留空或用板块名，避免引入 spec §3.3 之外的杜撰。星期由 `issue_date` 推算，分钟沿用现有 `estimate_reading_minutes`。
- **高度驱动分页的落地**：优先用 Playwright 渲染后量真实高度分页（已在用 Playwright 截图，可复用同一 page 量 `boundingBox`）；若一次渲染难以边量边分，可先按字数估高的保守启发式，但**必须留足安全边界、不得溢出/裁切**，并在完成说明里写清用了哪种。
- **兜底 `condense_slot`**：确定性——按句/分句切、累加到 `max_chars` 为止、`finish` 成完整句、绝不 `…`。签名要稳定，T13 只需替换其内部实现或加一层 AI 优先、兜底回退。
- 复用现有 `load_issue_for_xhs`/`render_card_images`/`build_caption` 骨架，别推倒重写整文件。
- 旧 `web/marketing/` 是无关历史项目，**不碰**。

## 验收标准

- `daily-news export-xhs --date <某已发布日期>` 出图：封面/头条/速览三型与原型一致（人眼比对原型），封面**无「今日」二字**、日期 82px 朱红 + 星期 pill、三统计 pill、列 3 头条、静态左滑 SVG。
- 头条一条一图、事实块无边框当主角、AI 块朱红左竖条 + `AI` chip 显式标注；页脚页码正确。
- 速览每页 ≤5、无「N 条快扫」footer、**无任何条目被裁切/溢出**；内容多时自动多页。
- 全卡**无省略号**；各槽位字数落 spec §7 区间（或按 §3.3 降级，不溢出）。
- 跑 `export-xhs` 不改动 `dist/`/pipeline 数据、不触发部署；输出只在 `runs/xhs/<date>/`。
- `pytest -q` 全过。

## 非目标

- **不接 AI 收敛**（→ T13）：本工单文案用确定性兜底，可能偶尔偏短或收得略硬，可接受。
- 不做自动发布小红书。
- 不改上游日报 JSON 结构。

## 开放问题

- 高度驱动分页是否用「Playwright 边量边分」两趟渲染实现——由开发按稳定性选，只要不溢出/不裁切即可，完成说明写明方案。
- kicker（头条栏目标签）数据来源：日报 JSON 是否已有可映射字段？若无，留空还是用板块名——实现时定，**不得 AI 现编**。

## 完成说明（开发填）

2026-07-07：已按 design §12/原型重做 `export-xhs` 三类卡片、接入 `condense_slot` 确定性兜底、改为保守高度估算分页（每页 ≤5，不裁切）；`2026-07-07` 真数据导出 7 张，DOM 高度检查无溢出，`pytest -q` 67 passed。

---
id: T19
title: 小红书封面头条可选，图组内部一致重排
status: done
依赖: [T18]
里程碑: M4
---

## 目标

解耦“日报编辑排名”与“小红书封面点击潜力”：允许从当日最多 3 条头条中显式指定小红书封面新闻，并在不修改网页日报顺序的前提下，让小红书封面、发布标题、内容卡和 caption 保持同一顺序。

## 范围

1. `export-xhs` 新增 `--cover-headline N`，N 为 1 基编号，默认 1，可选范围为当日可导出头条。
2. 选中 N 后，小红书内部顺序为 `N → 其余头条按日报原相对顺序`；例如 N=2 时为 `2 → 1 → 3`。
3. 重排后的第 1 条同时作为封面钩子/支撑句、v2 `xhs_magnetize` 输入、`xhs_note_title` 唯一事实来源、第一张头条卡和 caption 第 1 条。
4. 上游 issue JSON、网页日报和历史去重数据一律不改。
5. AI 成功凭证记录封面原始编号与小红书实际头条顺序。
6. 日常脚本支持 `XHS_COVER_HEADLINE`，封面模板默认使用当前 v2。

## 涉及文件

- `docs/spec/小红书图组导出.md`
- `docs/milestones/01-科技日报.md`
- `docs/tasks/README.md`
- `web/src/daily_news/xhs_export.py`
- `web/src/daily_news/ai_engine.py`
- `web/src/daily_news/main.py`
- `web/tests/test_xhs_export.py`
- `scripts/generate_today.sh`
- `scripts/export_xhs_today.sh`

## 实现要点

- 用 `Issue.model_copy` 生成仅供 XHS 导出使用的重排视图，不原地修改 `issue.headlines`。
- 封面选择在收集 `xhs_condense` 槽位之前完成，因此头条卡槽位 ID 、AI 输入和渲染编号天然一致。
- 发布标题 AI 只接收重排后的第 1 条头条，不再从其他头条/速览另选角度。
- 继续遵守 T18 严格 AI：任一必需 AI 阶段失败则无可发布产物。

## 验收标准

- 默认 N=1 时与 T18 成功路径一致。
- N=2 时，封面、第一张头条卡、caption 第 1 条和标题 AI 输入均来自原头条 2；后两条为原头条 1、3。
- 原 `Issue` 对象与 `dist/data/issues/<date>.json` 不变。
- N 超出当日可导出头条范围时明确失败，不开始 AI。
- `ai_provenance.json` 记录 `cover_headline=2` 和 `headline_order=[2,1,3]`。
- `pytest -q` 全过；7 月 22 日以 `--cover-headline 2 --cover-template v2` 真数据导出通过。

## 非目标

- 不让 AI 自动决定封面新闻；当前先显式指定，便于人工判断和 A/B 复盘。
- 不回改日报头条排名。
- 不改图组视觉设计。

## 开放问题

无。

## 完成说明（开发填）

- `export-xhs` 已支持 `--cover-headline`，脚本已支持 `XHS_COVER_HEADLINE`，并将 v2 设为默认封面模板。
- 选择原头条 2 后，小红书专用视图按 `2 → 1 → 3` 排列；上游 issue 与网页日报顺序不变。
- `xhs_note_title` 只接收所选封面新闻，避免发布标题跳到其他头条。
- `ai_provenance.json` 已记录封面原始编号、实际顺序及 3 个严格 AI 阶段的成功状态。
- `pytest -q`：101 项通过。
- 2026-07-22 真数据以 `--cover-template v2 --cover-headline 2 --provider codex` 导出成功；封面、第一张头条卡、caption 第 1 条和发布标题均对应 Gemini 头条。

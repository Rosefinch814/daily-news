---
id: T09
title: 选题级软去重（AI 读近几天已发索引判重）+ 聚合稿降权
status: done
依赖: [T08]
里程碑: M3
---

> **追认工单**：本功能由 Codex 先行实现（提交 `46a75c2` / `9ecbae9`），用户已人工验证效果并认可，故补此工单落成正式行为。权威行为见 [spec §7.5 第二层](../spec/01-科技日报.md)。

## 目标

在 T08「本地硬去重」之上，补第二层**软去重**：把最近几天已发布的头条/速览做成极简索引喂给 AI 选题，让 AI 自己判"换源转述 / 聚合合集 / 无新增事实"的重复该不该再上头条——**只影响选题分层，绝不本地硬删**。同时在本地预筛加重聚合稿（早报/晚报/热点导览等）降权，压低其进入 AI 的概率。

## 范围

1. 选题前生成「近 M 天已发索引」（默认 3 天、最多 40 条），作制品存档并注入选题 prompt。
2. 选题 prompt 增加「历史索引用法」段：换源转述/聚合/无新增事实不得上头条、有新增事实须说明并按跟进处理、聚合稿默认不上头条。
3. 本地预筛 `scoring.py` 补聚合噪声词 + 加重降权 + 分数封顶。
4. 全部可配（`selection_history.*`）、可关、向后兼容。

## 涉及文件（已实现）

- `web/src/daily_news/main.py`：`build_selection_history_index` / 注入 `run_ai_select_stage`。
- `web/src/daily_news/storage/local.py`：`save_selection_history_index` + 近 M 天已发索引收集。
- `web/src/daily_news/ai_engine.py`：`build_selection_file_prompt` 收 `history_index_path`，拼「历史索引用法」段。
- `web/src/daily_news/config.py` + `web/config/pipeline.yaml`：`selection_history{enabled,lookback_days=3,max_items=40}`。
- `web/src/daily_news/scoring.py`：聚合噪声词（今日热点导览/热点导览/TOP 3/快讯…）、惩罚 12/20→18/30、重噪声封顶 35 / 聚合封顶 50。

## 实现要点

- **索引极简**：只含日期/层级/标题/来源，**不含历史正文**（省 token，够 AI 判重）。
- **软去重、绝不硬删（安全铁律）**：第二层只让 AI 做"不上头条 / 降速览 / 丢弃 / 标跟进"的选题决策；硬删仅限 T08 第一层的 URL / 标题 hash 完全命中。**误删一条真新闻 ≫ 漏去一条重复。**
- **跟进语义**：有新增事实的候选，要求在 `reason` 说明"新增事实是什么"、按跟进处理，不把昨天讲过的主体重新包装成新头条。
- **聚合稿封顶**：预筛对聚合合集类降权并封顶分数——不硬删，凭高相关度仍可幸存。
- **向后兼容**：无历史期 / `selection_history.enabled=false` 时，选题行为同今天（不注入索引）。

## 验收标准

- 连续跑几期后，昨天已上头条的事件若今天只是换源转述/聚合合集 → 不再重复上头条（进速览或被丢），有新增事实的按跟进呈现。
- 聚合稿（早报/晚报/热点导览/TOP 3/快讯）默认不占头条名额。
- 关掉 `selection_history` 或无历史期时 pipeline 行为与之前一致。
- `pytest -q` 全过。（用户已人工验证 2026-07-01 期效果认可。）

## 非目标

- 不做 stdlib 字符相似度（difflib / n-gram Jaccard）、不做规则化事件指纹 `near_duplicate`/`possible_followup` 分级——判重靠"已发索引 + AI 判断"。
- 第二层不做任何本地硬删。
- 不改反馈/口味档案链路、不改阅读前端。

## 开放问题

- 若出现 AI 判不准的"换源换稿"漏网（URL 与标题都不同、AI 也没识别成重复），再评估补 spec §7.5「仍未做」那档的字符相似度/事件指纹，届时另开工单。

## 完成说明（开发填）

Codex 先行实现（`46a75c2` T08 内夹带 + `9ecbae9` selection history guard）；本工单为产品侧追认，用户已验证 2026-07-01 期效果并认可。

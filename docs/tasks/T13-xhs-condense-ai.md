---
id: T13
title: 小红书导出·xhs_condense AI 收敛阶段（版面定区间 + 忠实收敛，不漂移）
status: done
依赖: [T12]
里程碑: M4
---

## 目标

把 T12 的确定性兜底文案，升级成**受控 AI 收敛**：按 spec §7 每槽位的 `[min, max]` 字数区间，把日报原文**忠实收敛**进区间——只压缩、不新增/不改写事实，收完整句、不出省略号。行为权威见 [spec/小红书图组导出 §3](../spec/小红书图组导出.md)。

## 范围

1. 新增 AI 任务 `xhs_condense`，走**现有可替换 `ai_engine` 单一入口**（`CLAUDE_COMMAND` 间接层 / provider 可配），**绝不把某种调用方式焊死进导出逻辑**。
2. 把 T12 的 `condense_slot` 接缝实现改为：**AI 收敛优先，确定性兜底回退**（AI 不可用/超时/输出越界时回落 T12 兜底，保证导出永不崩）。
3. 收敛遵守 spec §3.3 **反漂移铁律**（见实现要点），并落测试。
4. 可选批量：一次调用收敛整期所有槽位、返回映射，省 AI 调用；语义仍按单槽位定义。

## 涉及文件

- `web/src/daily_news/ai_engine.py`（或现有 AI 任务所在处）：加 `xhs_condense` 任务 + 文件版 prompt 构造器，复用现有 `run_ai_task`/`save_ai_task_run` 风格。
- `web/src/daily_news/xhs_export.py`：`condense_slot` 内部改为「AI 优先 + 兜底回退」；把 §7 区间作为 payload 传入。
- `web/config/pipeline.yaml`（或对应 stage_providers 配置）：给 `xhs_condense` 配 provider（默认与其它 AI 阶段一致）。
- `web/tests/`：覆盖 忠实收敛/越界回退/不漂移/原文已在区间则原样。
- 只动 `web/`。

## 实现要点

- **入口契约（每槽位）**：payload `{slot_type, title(上下文), original_text, target_min, target_max}`，`slot_type ∈ {headline_summary, headline_impact, brief_summary}`；输出结构化 JSON `{text}`。
- **反漂移铁律（写进 prompt 且落测试）**：
  - 只压缩、只删次要，**不得新增/改写/推断**任何事实、数字、主体、时间、来源。
  - **来源署名、关键数字、主体公司/人物不可动。**
  - 必须**完整句**收尾，**禁止 `…`/`...`**。
  - AI 分析块内容仍是「分析」不是「事实」，标注由模板负责（T12 的 `.chip"AI"`），收敛不得把分析写成断言事实。
  - 原文已落在 `[min,max]` → **原样返回**。
- **越界与安全回退**：AI 输出为空 / 超出 `max` 过多 / 明显跑偏（长度异常、疑似加了原文没有的实体）→ **回落 T12 确定性兜底**，导出不中断；记一行日志。
- **就近提示**：prompt 明确「目标 `[min,max]` 字，尽量贴近日报原文信息量，落不进宁可略短，不许编」。
- 标题槽位默认**不收敛**（spec §7）；超长按 T12 的版面自适应，不在此强行压缩。
- 复用现有 AI 阶段的 provider 选择/日志/产物落盘机制，别另造。

## 验收标准

- 配好 provider 后跑 `export-xhs`：头条事实/AI 分析、速览摘要均由 AI 收敛，字数落 spec §7 区间；**逐条核对与日报原文一致、无新增事实、无半句、无省略号**。
- 断网/provider 不可用时，`export-xhs` **仍能出图**（回落兜底），不崩、不留半成品。
- 原文本就在区间内的槽位，输出与原文一致（未被无谓改写）。
- 切换 provider 不改导出业务逻辑（`ai_engine` 入口可替换）。
- `pytest -q` 全过（含不漂移/回退用例）。

## 非目标

- 不改卡片视觉/分页（T12 已定）。
- 不做 caption 的 AI 组织（spec §10，先纯模板）。
- 不接自动发布。

## 开放问题

- 批量 vs 逐槽位调用的性价比/稳定性——实现时定，契约不变；若批量，注意单条失败不拖垮整期（可逐槽位回退兜底）。
- 「疑似跑偏」的检测阈值（长度/新实体）——先用保守规则，误判就回退兜底，不误伤真新闻。

## 完成说明（开发填）

2026-07-07：已新增 `xhs_condense` AI prompt/schema/provider 配置，`export-xhs` 默认走 AI 收敛并在 provider 失败或输出越界时回落 T12 兜底；新增 `--no-ai-condense` 便于稳定导出。已用坏 provider 验证失败不崩并仍出 7 张图，`pytest -q` 69 passed。

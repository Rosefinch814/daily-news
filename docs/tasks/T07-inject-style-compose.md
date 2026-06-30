---
id: T07
title: AI 写作注入写作偏好档案（style.md）
status: done
依赖: [T05]
里程碑: M2
---

## 目标

让 AI 写作（compose）在生成中文摘要/精读/翻译时，**读最新的 `style.md`**，按你的语气/长度/翻译偏好下笔——这是调教"怎么写"生效的地方（[spec §9.5](../spec/01-科技日报.md)）。

## 范围

1. 给 `build_issue_file_prompt`（compose 的 prompt 构造器）加可选 `style_profile_path` 参数。
2. 有 style 时，把"语气/文风、长度、翻译要求"注入写作 prompt。
3. `run_ai_compose_stage` 解析 `web/profiles/<板块>/style.md` 路径并传入。
4. style 不存在时，行为与今天完全一致（向后兼容）。

## 涉及文件

- `web/src/daily_news/ai_engine.py`（`build_issue_file_prompt`）
- `web/src/daily_news/main.py`（`run_ai_compose_stage` 加载 style 路径并传参）

## 实现要点

- **开工门槛**：必须先由用户人工确认 `digest-feedback` 生成的 `style.md` 质量可接受，再开始本工单。
- style 是 markdown 文本，作为一段"写作偏好（用户调教得来）"附在写作 prompt 里，**不覆盖** spec §8 的红线（忠于原文、AI 判断标注、全程中文）——风格偏好在红线之内调，冲突时红线优先。
- 路径约定：`web/profiles/<section_slug>/style.md`；**不存在 → 传 None → 走原逻辑**。
- 不改 issue JSON 的结构与字段（title_zh/read_body_zh/ai_impact 等不变），只影响行文风格。
- 与 T05 解耦联调：可先手写 `style.md`（如"翻译更口语、精读每段更短、少用术语堆砌"）验证效果。

## 验收标准

- 不放 style.md：compose 输出与改前一致（diff 仅限新增可选参数）。
- 放一份写明"翻译要更口语、精读更短"的 style.md 后重跑：生成文本风格可见变化（更口语、段落更短），但仍忠于原文、影响分析仍有标注。
- `pytest -q` 全过。

## 非目标

- 不碰选题注入（taste=T06）。
- 不改写作的事实/标注红线、不改 issue 数据结构。

## 开放问题

- 等待用户人工校验 T05 产物质量后再开工。

## 完成说明（开发填）

2026-06-30：已将 style.md 作为写作偏好接入 AI 写作，缺少档案时保持原流程不变。

---
id: T06
title: 粗筛/选题注入口味档案（taste.md）
status: todo
依赖: [T05]
里程碑: M2
---

## 目标

让 AI 粗筛和选题在打分时，除了硬边界「关注清单」，还**读最新的 `taste.md`** 作为软偏好权重——这是调教"选题"生效的地方（[spec §9.5](../spec/01-科技日报.md)）。

## 范围

1. 给 `build_shortlist_file_prompt` / `build_selection_file_prompt` 加可选 `taste_profile_path` 参数。
2. 有 taste 时，把其内容注入 `_section_payload` 输出（标明是"软偏好/权重"，关注清单仍是硬边界）。
3. `run_ai_shortlist_stage` / `run_ai_select_stage` 解析 `web/profiles/<板块>/taste.md` 路径并传入。
4. taste 不存在时，行为与今天完全一致（向后兼容）。

## 涉及文件

- `web/src/daily_news/ai_engine.py`（`build_shortlist_file_prompt`、`build_selection_file_prompt`、`_section_payload` 约 93–112 行）
- `web/src/daily_news/main.py`（`run_ai_shortlist_stage`、`run_ai_select_stage` 加载 taste 路径并传参）

## 实现要点

- **开工门槛**：必须先由用户人工确认 `digest-feedback` 生成的 `taste.md` 质量可接受，再开始本工单。
- 注入位置：在 `_section_payload(section)` 产出的 JSON 里加一个 `taste_profile`（或在 prompt 里单列一段"口味档案"），**明确告诉 AI**：关注清单是硬过滤、口味档案是偏好加权（多看的提权、少看的降权），二者冲突时关注清单的"不想看"优先。
- taste 是 markdown 文本，直接作为字符串喂进 prompt 即可（不必结构化解析）。
- 路径约定：`web/profiles/<section_slug>/taste.md`；用 `paths.py` 风格拼接，**文件不存在 → 传 None → 走原逻辑**。
- 不动选题的名额/分层规则（头条 3-5、速览等），只增加偏好信号。
- 与 T05 解耦联调：可先手写一份 `taste.md`（如"多看 AI 模型进展，少看发布会通稿"）验证注入效果，不必等 T05 真产出。

## 验收标准

- 不放 taste.md：粗筛/选题输出与改前一致（diff 仅限新增可选参数）。
- 放一份写明"多看 Y、少看 Z"的 taste.md 后重跑同一批候选：对 Y 的保留/提级倾向可见上升、对 Z 可见下降（人工核对粗筛 keep/maybe/drop 与选题头条/速览分布）。
- 关注清单「不想看」仍然硬生效（不被 taste 的"多看"翻盘）。
- `pytest -q` 全过。

## 非目标

- 不碰写作注入（style→compose=T07）。
- 不改关注清单本身、不改打分算法结构。

## 开放问题

- 等待用户人工校验 T05 产物质量后再开工。

## 完成说明（开发填）

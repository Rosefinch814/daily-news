---
id: T02
title: 收口生成路径——删除带病的 legacy `generate`，统一到 `run-pipeline`
status: done
依赖: []
里程碑: M1
---

## 背景

目前生成一期日报有三套并存路径：手动 checkpoint 命令、旧 `generate`、新 `run-pipeline`。
`run-pipeline`（`PipelineRunner`）是已对齐 2026-06-24 工程结论的正确实现（AI 读本地文件、补全后不重排、阶段可切 provider）。
而旧 `generate`（`src/daily_news/main.py` 的 `async def generate`）已过时且带病：

- 正文补全后又 `rank_candidates` 重排（`main.py:1236`），正是 `run-pipeline` 已修、它没修的「丢掉 AI 选中候选」bug。
- 用老的单发 prompt `build_issue_prompt`（把全部候选塞进一个 prompt），与「AI 读文件、保留 60 条候选」的新结论相悖。
- 日志写死「调用 Claude / 准备 Claude 输入」，但默认 provider 已是 codex，可切换，文案误导。

留着它＝一个随时会被误用的坑。本工单把它删掉，让 `run-pipeline` 成为唯一的端到端生成入口。

## 目标

移除 legacy `generate` 命令及其连带的死代码，全程只保留 `run-pipeline` 一条端到端路径；删完测试全过、`run-pipeline` 行为不变。

## 范围

只动 `web/`。具体：

1. **删命令**：移除 `generate` 子命令注册（`build_parser` 里 `generate_parser` 那段，约 `main.py:1419`）和 `async def generate`（约 `main.py:1209`）。
2. **删连带死代码**（删 `generate` 后用 grep 确认已无引用，再删）：
   - `ai_engine.py`：`generate_issue_output`、`build_issue_prompt`（确认仅被 `generate` 与 `generate_issue_output` 用）。
   - `main.py`：`load_ai_output`，以及删后变成无引用的 import（`build_issue_prompt`、`generate_issue_output`、`build_issue_from_selection_prompt`——后者目前疑似只 import 未调用，一并核实）。
   - `storage/local.py`：`save_prompt`、`save_ai_run` 若删后无其他引用则一并移除（`run-pipeline` 用的是 `save_ai_task_run`，注意别误删）。
3. **务必保留**（共享，含测试在用）：`make_issue`、`next_issue_number`、`_issue_prompt_from_payload`、文件版 prompt 构造器（`build_shortlist_file_prompt` / `build_selection_file_prompt` / `build_issue_file_prompt`）、`PipelineRunner` 全部、`sync` 命令。
4. **DEVELOPMENT.md**：如有指向 `generate` 的用法，改成 `run-pipeline`。

## 实现要点

- 顺序：先删 `generate` 命令与函数 → `grep -rn "<符号>" src tests` 逐个确认无引用 → 再删该符号。不要凭印象批量删。
- `make_issue` 被 `tests/test_render.py` 和 `PipelineRunner` 共用，**绝不能删**。
- 删除是纯瘦身，不改 `run-pipeline` / checkpoint 任何行为，不动产物结构。
- Supabase 内联同步原本在 `generate` 里；删了不影响独立的 `sync` 命令（去留另议，本工单不碰）。

## 验收标准

- `grep -rn "def generate\b\|generate_issue_output\|build_issue_prompt\|load_ai_output\|\"generate\"" web/src web/tests` 无残留（除非是被删对象自身的定义已不存在）。
- `daily-news --help` 不再出现 `generate`；`run-pipeline`、`sync`、各 checkpoint、`render-mvp` 仍在。
- `pytest -q` 全过（当前 36 个）。
- `daily-news run-pipeline --section tech --date <某日> ...` 仍端到端产出 `dist/`（与改前一致）。

## 非目标

- 不动 T01（旧 SSR 死代码）——那张工单单独清。
- 不决定 Supabase 去留、不改 provider 选择逻辑。
- 不“为了减行数”重构 `run-pipeline` 或 `ai_engine` 的现有正确逻辑。

## 开放问题

- 是否保留一个 `generate` 作为 `run-pipeline` 的**薄别名**（照顾肌肉记忆）？产品默认意见：不保留，直接用 `run-pipeline`。若你要保留别名，请在此注明，否则按删除执行。

## 完成说明（开发填）

2026-06-24：已删除 legacy `generate` 命令、旧单发 issue prompt 入口和旧 AI 保存函数，端到端入口统一为 `run-pipeline`。

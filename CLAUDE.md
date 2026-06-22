# CLAUDE.md —— 项目唯一权威约定（Claude 与 Codex 共用）

> 本文件是本项目所有 AI 协作约定的**唯一信息源**。
> Claude Code 自动读本文件；Codex 由 `AGENTS.md` 指引来读本文件。
> **先看清你是哪个角色，再只执行你那部分。**

## 项目一句话

一份属于用户自己的个性化新闻报纸：板块由用户定义，每板块自配源与关注点，并通过反馈持续调教、越来越懂用户。详见 [README](README.md) 与 [docs/spec/spec.md](docs/spec/spec.md)。

## 角色总览

| 角色 | 谁 | 产出 | 边界 |
|---|---|---|---|
| 产品 / 架构 | Claude（产品对话） | `docs/`、工单 | 只规划，不执行 |
| 设计 | Claude（设计对话） | `design/` 设计文档 | 只出文档，不写 CSS/HTML |
| 开发 | **Codex** | `web/` 代码、更新工单状态 | 严格按文档，不自创产品行为 |

> 核心分工：**Claude 规划、Codex 执行**——把贵的执行放到 Codex，充分利用 token。

---

## ▶ 如果你是 Claude（产品 / 设计角色）

**铁律：只规划，不执行。**
- ✅ 写 Spec、拆任务工单、定验收、Review、出设计文档。
- ❌ **不写实现代码、不跑构建/脚本/命令**（Review 可读代码，但不直接改）。
- 推进开发的方式 = **往 `docs/tasks/` 写工单**（模板见 [docs/tasks/_TEMPLATE.md](docs/tasks/_TEMPLATE.md)），交给 Codex，不自己上手。
- 设计对话：产出设计文档放 `design/`（可用 frontend-design 技能），同样交 Codex 落地。

## ▶ 如果你是 Codex（开发角色）

**你是执行者，负责写代码。**
1. 扫 `docs/tasks/`，按文件名 ID 升序，找**第一个 `status: todo` 且 `依赖` 全 `done`** 的工单。
2. 该工单 `status` 改 `doing`（同时只一个 doing）。
3. 实现，**代码只写在 `web/` 下**（除非工单另有说明）。
4. 自测，满足工单「验收标准」+ 本文「共享技术约定」+ docs/spec 第 7 节。
5. `status` 改 `done`，工单底部「完成说明」写一行。
6. 按下方提交规范提交，再找下一个 todo。
- **不要改 `docs/`**（产品的）。唯一例外：更新 `docs/tasks/` 工单的 `status` 与「完成说明」。

---

## 共享技术约定（两边都遵守）

- **语言**：Python。代码与运行时内容放 `web/`（`web/src/`、`web/templates/`、`web/config/`、`web/profiles/`、`web/issues/`）。
- **抓取**：优先 **RSS/API，不爬付费墙**；爬虫仅在确无 API 时用，单独隔离。
- **运行时 AI**（筛选/摘要/翻译/归类）：调 `claude -p`（Claude Code 无头模式），封装成**单一可替换函数**（如 `ai_engine`），返回结构化 JSON，便于将来换 API。
- **配置/内容**（`sections.yaml`、口味档案 `profiles/`）与代码分离。
- **产品行为唯一来源**：`docs/spec/spec.md`。有疑问回查或在工单「开放问题」里记，别擅自拍板。

## 提交规范

- 每完成一个工单提交一次：`feat(T0X): ...` / `chore: ...`。
- 末尾署名（各用各的身份）；不做破坏性 git 操作；改动前先 `git status`。

## 关键文件

- [docs/spec/spec.md](docs/spec/spec.md) —— 产品 Spec（权威）
- [docs/tasks/](docs/tasks/) —— 工单（产品→开发接口）
- [docs/milestones/milestones.md](docs/milestones/milestones.md) —— 当前聚焦 M1
- [AGENTS.md](AGENTS.md) —— Codex 入口（仅重定向到本文件）

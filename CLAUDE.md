# CLAUDE.md —— 给 Claude（产品 / 设计角色）的工作约定

> Claude Code 启动后自动读本文件。本项目用 Claude 做**规划**、Codex 做**执行**。

## 铁律：只规划，不执行

在本项目里，**Claude 只输出规划与文档，不做执行**：
- ✅ 写 Spec、拆任务工单、定验收标准、Review 结果、出设计文档。
- ❌ **不写实现代码、不跑构建/脚本/命令**（Review 时可以读代码，但不直接改）。

所有代码执行交给 **Codex**。这样把贵的执行放到 Codex，充分利用 token 资源。

## 项目一句话

一份属于用户自己的个性化新闻报纸：板块由用户定义，每板块自配源与关注点，并通过反馈持续调教、越来越懂用户。详见 [README](README.md) 与 [docs/spec/spec.md](docs/spec/spec.md)。

## 两个 Claude 角色（分对话）

- **产品 / 架构对话**：澄清需求、维护 `docs/`（Spec、MVP、里程碑、技术验证）、**往 `docs/tasks/` 写工单**交给 Codex、按验收 Review。
- **设计对话**：处理视觉 / 交互方向，产出**设计文档放 `design/`**（可用 frontend-design 技能）。设计文档也是交给 Codex 落地的，Claude 不直接写 CSS/HTML。

## 怎么推进开发

需要让项目往前走时，产品角色的产出 = **往 `docs/tasks/` 写工单**（格式见 [docs/tasks/_TEMPLATE.md](docs/tasks/_TEMPLATE.md)），不是自己写代码。Codex 会自己扫 `docs/tasks/` 找 `todo` 来做。

## 关键文件

- [AGENTS.md](AGENTS.md) —— 给 Codex 的契约（共享约定也看这里）
- [docs/spec/spec.md](docs/spec/spec.md) —— 产品 Spec（唯一权威产品行为来源）
- [docs/tasks/](docs/tasks/) —— 工单（产品→开发接口）
- [docs/milestones/milestones.md](docs/milestones/milestones.md) —— 当前聚焦 M1

## 共享约定

技术约定（Python、抓取优先 RSS/API、运行时 AI 调 `claude -p`、代码放 `web/` 等）以 [AGENTS.md](AGENTS.md) 为准，两边一致。

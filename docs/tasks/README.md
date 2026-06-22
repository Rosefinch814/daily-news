# 任务工单 —— 产品 → 开发 的接口

产品角色（Claude）在此放工单；开发角色（Codex）扫这里找活干。
工作流见根目录 [CLAUDE.md](../../CLAUDE.md)（唯一权威约定）。

## 状态约定（写在工单 frontmatter 的 `status`）

- `todo` —— 待做
- `doing` —— 进行中（同一时间应只有一个）
- `done` —— 已完成、自测通过（等产品 Review）

## 命名与顺序

- 文件名：`T<两位序号>-<短名>.md`，例如 `T01-scaffold.md`。
- 开发按序号**升序**执行；有 `依赖` 的，依赖未 `done` 不能开工。

## 怎么新增工单

复制 [_TEMPLATE.md](_TEMPLATE.md)，填好各字段。工单要具体到开发能直接照做、无需发明产品行为。

## 当前里程碑

见 [../milestones/milestones.md](../milestones/milestones.md)。当前聚焦 **M1：科技单板块，验证筛得准 + 能调教**。

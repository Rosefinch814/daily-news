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

见 [../milestones/01-科技日报.md](../milestones/01-科技日报.md)。

当前进度（2026-06-30）：
- M1 主链路已完成：科技单板块日报可抓取、筛选、写作、导出并通过 Cloudflare Pages 发布。
- M2 反馈闭环已完成：前端反馈写入 Supabase，`digest-feedback` 可生成 taste/style/seed-suggestions，T06/T07 已接入生成链路。
- M3 先行项 T08 已完成：本地预筛支持近 7 天历史 URL 去重和可选标题 hash 去重。
- 当前无新的 `todo` 工单；下一步以连续运行观察、重复新闻验证和后续工单拆分为主。

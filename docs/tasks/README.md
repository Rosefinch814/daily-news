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

当前进度（2026-07-07）：
- M1 主链路已完成：科技单板块日报可抓取、筛选、写作、导出并通过 Cloudflare Pages 发布。
- M2 反馈闭环已完成（含 T10/T11 反馈两通道，代码 done，待用户手动 apply Supabase schema + 设 `OWNER_FEEDBACK_TOKEN` 才生效）。
- M3 先行项：T08 本地硬去重 + T09 选题级软去重均已完成。
- **M4 分享·小红书图组导出**：设计已定稿（design §12 + 原型），spec/字数契约已回填。T12（卡片模板落地）、T13（xhs_condense AI 收敛）已 **done**。**新 todo：T14 发布文案（AI 钩子标题 + 精简正文，依赖 T12/T13）**。旧 `web/marketing/` 是无关历史项目，勿动。

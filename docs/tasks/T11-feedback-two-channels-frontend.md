---
id: T11
title: 反馈两通道·前端与渲染（公开=读者留言箱只读、主人版=完整口味 UI）
status: done
依赖: [T10]
里程碑: M2
---

## 目标

前端落地 [spec §9.6](../spec/01-科技日报.md) 的两条通道 + 双渲染模式：**公开发布版**只渲染阅读视图 + 读者留言框（写 `product_feedback`）；**主人版**（本地、不部署）额外渲染完整口味反馈 UI（写 `feedback`，带 `owner_token`）。后端底座由 T10 提供。

## 范围

1. `render.py` 双渲染模式：由 env 决定注入 `feedback_mode`（`reader`｜`owner`）及对应配置。
2. `app.js` 按模式渲染：非 owner 模式**不渲染**每条 👍👎 / 口味整期留言（按钮根本不出现），改渲染**读者留言框**；owner 模式渲染完整口味 UI 并带令牌。
3. 主人版输出到 **gitignore 目录、绝不提交/部署**；公开发布版进 `dist/`。
4. 文案分流：读者框去掉调教语义。

## 涉及文件

- `web/src/daily_news/render.py`：`_public_config()` 按 `FEEDBACK_MODE`（env，缺省 `reader`）产出：
  - `reader`：`{supabase_url, supabase_anon_key, feedback_mode:"reader"}`（供读者框写 `product_feedback`）。
  - `owner`：额外 `{feedback_mode:"owner", owner_token:"<从 env>"}`。
  - 主人版渲染输出到独立目录（**建议** `web/dist-owner/`，加进 `.gitignore`）。
- `web/frontend/assets/app.js`：按 `config.feedback_mode` 分支渲染（见实现要点）。
- `web/frontend/assets/app.css`：读者留言框样式（**引用设计规范 token，不散写裸值**）。
- `.gitignore`：加 `web/dist-owner/`（或所选主人版输出目录）。
- `web/README` 或 `docs` 附近的运行说明（**建议**）：记一句"主人版怎么本地跑+给反馈"。

## 实现要点

- **渲染模式**：`FEEDBACK_MODE=owner` 出主人版（本地），缺省/公开出 `reader` 版。公开发布流程（进 `dist/` → 推 Cloudflare）**必须是 reader 版**；主人版只本地 `python -m http.server` 打开，永不提交。
- **app.js 分支（关键，修掉"按钮显示了点了报错"）**：
  - `feedback_mode !== "owner"`：**不创建**每条 👍👎 与口味整期留言 DOM；**创建读者留言框**（issue 末尾），POST 到 `{supabase_url}/rest/v1/product_feedback`，payload `{issue_id, issue_date, section_slug, note}`，`Prefer: return=minimal`。
  - `feedback_mode === "owner"`：渲染现有完整口味 UI（每条 👍👎 + 补充 + 整期留言），POST 到 `feedback` 时**带上 `config.owner_token`** 字段。
- **读者框文案（去调教语义）**：标题"读者反馈"、占位"对这份日报有什么想说的？"、按钮"记下"、提示"你的建议我会看到"。**不出现**"影响下一期""本期已标记 X 好 Y 不好"（那是口味语义，只在主人版）。
- **owner_token 只随主人版**：令牌从主人版 env 注入、只进 `dist-owner/`；公开 `dist/` 里**不得出现** owner_token（构建后可 grep 校验）。
- **安全自检**：公开 `dist/` grep 搜不到 `owner_token`、搜不到 `SERVICE_ROLE`；读者框只写 `product_feedback`、不触碰 `feedback`。
- **无障碍/响应式/离线队列**：沿用 T04 既有做法（`aria-*`、断点、localStorage 重试）。

## 验收标准

- **公开版**（`FEEDBACK_MODE` 缺省渲染的 `dist/`）：打开某期——**没有**任何每条 👍👎、**没有**口味整期留言；底部只有"读者反馈"留言框；提交 → Supabase `product_feedback` 出现一行；`dist/` grep 搜不到 owner_token / service key。
- **主人版**（`FEEDBACK_MODE=owner` 渲染、本地打开）：每条 👍👎 + 补充 + 整期留言齐全；提交口味反馈 → `feedback` 出现一行且带 `owner_token`；跑 `digest-feedback`（配了同一令牌，T10）→ 该行被消化、档案更新。
- 读者在公开版怎么点都**动不了** `taste.md`/`style.md`（因读者写的是 `product_feedback`，且 digest 只认令牌）。
- `pytest -q` 全过（若前端有可测部分）；`prefers-reduced-motion` 下动效关、键盘可操作。

## 非目标

- 不做读者反馈的展示/管理面板（作者手动翻表）。
- 不改口味消化逻辑与档案结构（T05/T10 已定）。
- 不引入登录 / Supabase Auth。
- 不做定时/自动部署主人版（主人版就是本地手动跑）。

## 开放问题

- 主人版输出目录命名与本地打开方式（`dist-owner/` + `http.server`？）——实现时定，务必确保它进 `.gitignore`、绝不被公开部署。
- 读者留言框的视觉细节（是否复用现有整期留言框样式去掉计数）——按设计规范落，必要时回产品对话确认。

## 完成说明（开发填）

已完成 reader/owner 双渲染模式：公开版只写 `product_feedback`，主人版输出到 `web/dist-owner/` 并携带 `owner_token` 写入口味反馈。

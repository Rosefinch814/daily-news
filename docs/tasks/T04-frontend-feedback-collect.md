---
id: T04
title: 前端反馈采集（A 常驻图标 + 头条补充一句 + 整期留言）→ 直写 Supabase
status: done
依赖: [T03]
里程碑: M2
---

## 目标

在客户端渲染的每期页面上，按设计稿渲染反馈入口，让读者**即点即写**地把 👍/👎、补充一句、整期留言匿名写进 Supabase `feedback` 表（T03 已建）。这是 M2 调教闭环（[spec §9](../spec/01-科技日报.md)）的采集前端。

## 范围

1. 前端渲染**反馈入口**（入口形态已定＝**A 常驻微缩图标**，见 [spec §14.1](../spec/01-科技日报.md)、[设计规范 §5「反馈批注」](../../design/设计规范.md)、参考原型 `design/prototype/m2-反馈交互-科技日报.html`）：
   - **每条**（头条 hero/次条、速览）来源·原文行右侧常驻 👍/👎；速览只 👍/👎。
   - **头条**：表态后才显形「补充一句 + 记下」；速览不给输入。
   - **整期末尾**：多行 `textarea` +「记下本期」+ 上方「本期回顾」（纯客户端汇总）。
2. 点击/提交**直接 POST** 到 Supabase REST `feedback`，用 **anon key**；payload 对齐 T03 表结构与 [spec §9.2](../spec/01-科技日报.md)。
3. 前端配置注入 `SUPABASE_URL` + **anon key**（build/render 阶段从 env 注入到前端可读处）；**service key 绝不进前端**。
4. 反馈入口**只在 M2 渲染**；不改 v1 阅读版面与阅读链路（除按需补文章定位字段，见开放问题）。

## 涉及文件

- `web/frontend/assets/app.js`（渲染反馈 DOM + 交互 + POST + 本地重试）
- `web/frontend/assets/app.css`（反馈组件样式，**引用设计规范 token，不散写裸值**）
- 前端配置注入点（**建议** `web/frontend/` 下的运行时配置，或 render 导出的 `data/manifest.json` 带 `supabase_url` + `supabase_anon_key`）——具体位置随前端目录/build 收敛（T01/T02）定
- render 侧（**建议**，若每期 data JSON 未含文章定位字段则需补：`article_level`/`article_index`/`source_item_ids`）

## 实现要点

- **定位字段**（POST 时带上）：整期级取 `issue_id` / `issue_date` / `section_slug`；文章级再带 `scope='article'` + `article_level('headline'|'brief')` + `article_index` + `source_item_ids`。整期留言 `scope='issue'`，文章定位字段留空、靠 `note`。
- **POST**：`POST {SUPABASE_URL}/rest/v1/feedback`，headers `apikey: <anon>`、`Authorization: Bearer <anon>`、`Content-Type: application/json`、`Prefer: return=minimal`。RLS 已仅放开匿名 INSERT（T03），前端**只写不读**。
- **快照语义**：每次提交写**当前完整快照** `{signal, note, …定位}` 一行；改主意=再写一行（👍↔👎 切换、取消＝`signal:null`）。digest（T05）取最新，前端不做去重/更新（RLS 不允许 UPDATE/DELETE）。
- **交互**（照原型）：👍/👎 即点即写、UI 乐观即时反馈 + 约 1s 去抖收敛连点；头条 note 单行**显式「记下」**提交（回车等同）、空不写；整期 `textarea` +「记下本期」；写后淡出「已记下 · 调整下一期」；**本期回顾纯客户端**（读页面 `.is-on` 态，不发请求）。
- **诚实**：明示反馈不改当期、只影响下一期（末尾提示文案）。
- **离线/失败**：乐观更新 + 本地暂存队列（localStorage）失败后台重试；不阻塞阅读。
- **无障碍/响应式**：👍/👎 用 `aria-pressed`、图标按钮有 `aria-label`、触摸目标够大；断点 820/520 沿用 v1；`prefers-reduced-motion` 关动效。
- **安全自检**：前端打包产物里**不得出现 service key**（构建后可 grep 校验）。

## 验收标准

- 打开某期，点头条 hero 的 👍 → UI 乐观显示「已记下」，Supabase `feedback` 出现一行 `scope=article`、`article_level=headline`、`article_index` 正确、`signal=up`、定位字段齐；用 anon key `select` 仍被 RLS 拒（沿 T03）。
- 头条点「记下」补充一句 → 写入一行带 `note` 的快照；速览只有 👍/👎、贴在来源·原文同一行、无输入框。
- 整期「记下本期」→ 写一行 `scope=issue` + `note`；「本期回顾」正确汇总本期标记且**不发网络请求**。
- 断网点击：UI 仍乐观、恢复网络后补写成功。
- 构建后的前端产物 grep **搜不到** service key；`prefers-reduced-motion` 下动效关闭、键盘可操作。

## 非目标

- 不做反馈**消化**（T05）、不做注入（T06/T07）、不建 `feedback` 表/RLS（T03）。
- 不给 anon 读权限、不做反馈分析/可视化面板。
- 不做"点完即生效"的实时调教（反馈只影响下一期）。
- 不改 v1 阅读版面与既有阅读数据结构（除按需补文章定位字段）。

## 开放问题

- **anon key 注入位置**：取决于前端目录与 build 形态，待 T01/T02 收敛后定（务必只注 anon、不注 service）。
- **每期 data JSON 是否已含 `article_level`/`article_index`/`source_item_ids`**：若无，需在 render 侧补（可能牵出一个小前置改动）。
- 去抖时长（~1s 起）与本地重试上限——实现时定。

## 完成说明（开发填）

2026-06-25：已实现前端常驻反馈入口、头条补充一句、整期留言和 anon key 直写 Supabase，public 配置由 manifest 注入且 dist 通过 service key 检查。

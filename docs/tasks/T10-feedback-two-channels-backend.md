---
id: T10
title: 反馈两通道·后端（product_feedback 留言箱表 + feedback.owner_token + digest 只认我）
status: done
依赖: [T03, T05]
里程碑: M2
---

## 目标

报纸公开后，把反馈拆成两条**互不相通**的通道的**后端底座**：①读者对日报产品的公开留言（新 `product_feedback` 表，**digest 永不读**）；②我的口味反馈（`feedback` 表加 `owner_token`，**digest 只消化令牌匹配的行**）。权威行为见 [spec §9.6](../spec/01-科技日报.md)。前端/渲染归 T11。

> 核心保证：**"只有我的口味能改我的报纸"这条，焊在 digest（我控制的消化侧），不靠前端边界。** 别人扒到公开 key 直接灌 `feedback`，令牌不对 → 永不被消化。

## 范围

1. `web/supabase/schema.sql`：新增 `product_feedback` 表 + RLS（**仅匿名 INSERT**）；给 `feedback` 表加 `owner_token text` 列。
2. `SupabaseStore`：`fetch_undigested_feedback(...)` 增加**按 `owner_token` 过滤**（只取匹配配置令牌的行）；补一个 `insert_product_feedback(...)`（服务端/测试用；前端走 REST 不经此）。
3. digest 侧读令牌：从 env/config 读 `OWNER_FEEDBACK_TOKEN`，传入 fetch 做过滤。
4. 不动前端、不动渲染（T11）。

## 涉及文件

- `web/supabase/schema.sql`：`product_feedback` 建表 + RLS；`alter table feedback add column owner_token`。
- `web/src/daily_news/storage/supabase.py`：`fetch_undigested_feedback` 加 `owner_token` 过滤；`insert_product_feedback`。
- `web/src/daily_news/main.py`（digest handler）/ `config.py`：读 `OWNER_FEEDBACK_TOKEN`（env 优先），传给 fetch。
- `web/.env.example`：加 `OWNER_FEEDBACK_TOKEN=`（注释说明：仅本地主人版用，绝不提交真值）。
- `web/tests/`：覆盖"令牌匹配才被消化""令牌不匹配的行被跳过""product_feedback 不进 digest"。

## 实现要点

- **`product_feedback` 表**（沿用 schema.sql 现有风格）：
  ```sql
  create table if not exists product_feedback (
    id uuid primary key default gen_random_uuid(),
    issue_id text, issue_date date, section_slug text,
    note text not null,
    created_at timestamptz not null default now()
  );
  alter table product_feedback enable row level security;
  create policy product_feedback_anon_insert on product_feedback
    for insert to anon with check (true);
  -- 不给 anon 任何 select/update/delete；digest 永不读此表
  ```
- **`feedback.owner_token`**：`alter table feedback add column if not exists owner_token text;`（可空，向后兼容旧行）。
- **digest 过滤（关键）**：`fetch_undigested_feedback` 加参数 `owner_token: str | None`；**配置了令牌就 `where owner_token = <token>`**。令牌来源 `OWNER_FEEDBACK_TOKEN`（env）。
  - **安全默认**：令牌**未配置**时，不要退回"吃全部"（那等于没锁）——应**跳过口味消化并给清晰提示**（"未配置 OWNER_FEEDBACK_TOKEN，跳过口味反馈消化"），避免悄悄把陌生人反馈吃进档案。
- **RLS 不变原则**：两张表都只匿名 INSERT、禁读改删；`SERVICE_ROLE_KEY` 仍只服务端。anon key 公开无妨——真正的锁是 digest 的令牌过滤。
- **复用** T03 的 `mark_feedback_digested` / fake client 测试风格，别另造。

## 验收标准

- 应用新 schema 后：anon key 能 `insert` 一条 `product_feedback`；anon `select` 被拒。
- `feedback` 插两条：一条 `owner_token=<A>`、一条 `owner_token=<B>`；配置 `OWNER_FEEDBACK_TOKEN=<A>` 跑 `digest-feedback` → **只有 A 那条被消化**，B 那条 `digested_at` 仍空。
- 未配置 `OWNER_FEEDBACK_TOKEN` 时，digest **跳过口味消化并提示**，不吃任何行。
- `product_feedback` 里的行**永不**被 `digest-feedback` 读取/消化。
- `pytest -q` 全过；现有 digest 对已配置令牌的行为正常。

## 非目标

- 不做前端留言框、不做双渲染模式、不改 app.js（→ T11）。
- 不做读者反馈的展示/分析面板（作者手动翻表即可）。
- 不引入登录/Supabase Auth（本期用 owner_token 足够）。

## 开放问题

- 需用户在 Supabase 手动：应用 schema（建 `product_feedback` + 加列）、生成一个随机 `OWNER_FEEDBACK_TOKEN` 放进本地 `web/.env`（绝不提交）。这些在 T11 落地前先备好即可联调。

## 完成说明（开发填）

已完成 `product_feedback` 表、`feedback.owner_token`、digest owner token 过滤与相关存储测试；未配置 `OWNER_FEEDBACK_TOKEN` 时默认跳过口味消化。

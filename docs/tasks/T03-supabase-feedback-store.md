---
id: T03
title: Supabase feedback 表 + RLS + SupabaseStore 读写方法
status: done
依赖: []
里程碑: M2
---

## 目标

让前端能匿名安全地把反馈写进 Supabase，让本地 pipeline 能读回未消化的反馈做消化。这是 M2 调教闭环（[spec §9](../spec/01-科技日报.md)）的存储底座。

## 范围

1. 在 `web/supabase/schema.sql` 新增 `feedback` 表 + 启用 RLS + 一条**仅允许匿名 INSERT** 的策略。
2. 给 `web/src/daily_news/storage/supabase.py` 的 `SupabaseStore` 补**读写**方法（现在只写不读）：
   - `insert_feedback(...)`（服务端写，主要给联调/测试用；前端是直接走 REST，不经此函数）。
   - `fetch_undigested_feedback(section_slug, from_date=None, to_date=None, include_digested=False) -> list[...]`：默认取 `digested_at is null` 的反馈，含定位字段；`from_date/to_date` 按 `issue_date` 收窄；`include_digested=True` 时无视 `digested_at`（供 T05 的 `--redigest` 重炒用）。
   - `mark_feedback_digested(ids: list[...])`：批量写 `digested_at = now()`。
3. 不改动阅读链路、不改现有 `sync` 行为。

## 涉及文件

- `web/supabase/schema.sql`（加表 + RLS 策略）
- `web/src/daily_news/storage/supabase.py`（加 3 个方法）
- （建议）`web/tests/` 加针对新方法的最小测试（可对 client 打桩/mock，不必真连云）

## 实现要点

- **表结构**（对齐 [spec §9.2](../spec/01-科技日报.md)，沿用 schema.sql 现有风格：`timestamptz default now()` 等）：
  ```sql
  create table if not exists feedback (
    id uuid primary key default gen_random_uuid(),
    issue_id text not null,
    issue_date date not null,
    section_slug text not null,
    scope text not null check (scope in ('article','issue')),
    article_level text check (article_level in ('headline','brief')),
    article_index integer,
    source_item_ids text[] not null default '{}',
    signal text check (signal in ('up','down')),
    note text,
    created_at timestamptz not null default now(),
    digested_at timestamptz
  );
  create index if not exists feedback_undigested_idx
    on feedback (section_slug) where digested_at is null;
  ```
- **RLS（安全铁律，务必照做）**：
  ```sql
  alter table feedback enable row level security;
  -- 只放开匿名 INSERT；不给 anon SELECT/UPDATE/DELETE
  create policy feedback_anon_insert on feedback
    for insert to anon with check (true);
  ```
  - service_role 天然绕过 RLS，所以 pipeline 的读/标记不受影响。
  - **不要**给 anon 任何 select 策略——前端只写不读。
- `SupabaseStore.from_env` 已读 `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`，新方法复用 `self.client`；`enabled` 为 false 时（无 env）方法应安全降级（抛清晰错误或返回空，照现有 `sync_run` 的处理风格）。
- 反馈定位语义：`scope='article'` 时 `article_level`+`article_index` 必填、`source_item_ids` 冗余存；`scope='issue'`（整期留言）时三者可空、`signal` 可空、靠 `note`。
- ⚠️ **anon key 不在本工单范围**（它属于前端 T04）；本工单只确保 RLS 策略让"匿名只能插"成立。

## 验收标准

- 在 Supabase 上应用新 schema 后：用 **anon key** 能 `insert` 一条 feedback；用 anon key `select` 被 RLS 拒。
- 用 service key（`SupabaseStore`）能 `fetch_undigested_feedback('tech')` 取到刚插的那条；`mark_feedback_digested([id])` 后再取不到。
- `pytest -q` 全过；现有 `sync` 行为不变。

## 非目标

- 不写前端采集（T04）、不写消化阶段（T05）。
- 不给 anon 读权限、不做反馈分析面板。
- 不动 issues/issue_articles 等既有表。

## 开放问题

（无；anon key 的获取与前端用法在 T04 处理）

## 完成说明（开发填）

2026-06-25：已新增 feedback 表/RLS/匿名仅 INSERT 策略，补齐 SupabaseStore 反馈插入、读取未消化与标记消化方法，并加 fake client 测试。

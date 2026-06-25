---
id: T05
title: digest_feedback 消化阶段（分类路由 + 批量 + 可独立运行）
status: todo
依赖: [T03]
里程碑: M2
---

## 目标

把 Supabase 里未消化的反馈，按类型分流增量更新三档偏好档案（`taste.md` / `style.md` / `seed-suggestions.md`），消化后标记。这是调教闭环把"反馈"变成"记忆"的那一步（[spec §9.3 / §9.4](../spec/01-科技日报.md)）。

## 范围

1. 新增 AI `task_type: digest_feedback` 及其文件版 prompt 构造器与输出模型。
2. 实现消化逻辑：读未消化反馈（按 `issue_date` 分组）+ 对应那期日报 + 现有 taste/style → **分类路由**增量更新三档档案 → 标记反馈 `digested_at`。
3. 两种触发：
   - **独立命令** `digest-feedback`（攒几天批量、不出报只更新档案）。
   - **run-pipeline 起手阶段**：在 `PIPELINE_STAGES` 的 `ai_shortlist` 之前插入，自动先消化。
4. **消化进度 = 逐行 `digested_at`**（不另存水位/游标）；默认只吃 `digested_at is null`，再跑幂等、只吃新的，不重复消化。
5. **范围控制（独立命令的可选开关）**：
   - `--from YYYY-MM-DD` / `--to YYYY-MM-DD`：在未消化反馈里**再按 `issue_date` 圈一段**只消化这段（缺省＝全部未消化）。
   - `--redigest`：**逃生口**，无视 `digested_at` 把指定范围（建议配合 `--from/--to` 使用）**重吃一遍**；默认关闭。
6. 配置：`pipeline.yaml` 的 `stage_providers` 增加该阶段（默认 `codex`）。

## 涉及文件

- `web/src/daily_news/main.py`（`PIPELINE_STAGES` / `AI_STAGE_TASKS` 约 78–91 行加阶段；`_execute_stage` 加分支；新增 `digest-feedback` 子命令 + handler；新 `--ai-digest-provider`）
- `web/src/daily_news/ai_engine.py`（新增 `build_digest_file_prompt(...)`，复用 `_section_payload` + `run_ai_task` 模式）
- `web/src/daily_news/models.py`（新增 digest 输出模型，见下）
- `web/config/pipeline.yaml`（`stage_providers` 增 `digest_feedback`）
- `web/config/config.py`（若 `stage_providers` 是 `Literal` 约束，扩入 `digest_feedback`）
- `web/profiles/<板块>/`（运行时产物目录，需新建；taste.md/style.md/seed-suggestions.md）
- `web/src/daily_news/storage/local.py`（如需：读/写 `web/profiles/<板块>/*.md` 的小工具函数）

## 实现要点

- **读反馈**：用 T03 的 `SupabaseStore.fetch_undigested_feedback(section_slug, from_date=None, to_date=None, include_digested=False)`（T03 已把这几个可选参数留好）——默认只取 `digested_at is null`；`from_date/to_date` 按 `issue_date` 收窄；`include_digested=True`（对应 `--redigest`）则无视 `digested_at` 取该范围全部。按 `issue_date` 分组，每组用本地那期日报 `web/runs/issues/<issue_id>.json`（`load_issue`）把反馈对回具体条目（`article_level`+`article_index` / `source_item_ids`）。
- **进度即 `digested_at`**：不另存水位/游标；默认路径靠"只吃未消化 + 吃完盖戳"做到**重跑幂等**（连跑两次第二次无活）。CLI 的 `--from/--to/--redigest` 只透传给上面的 fetch 参数。
- **⚠️ replay 会叠加（务必写进实现，别让用户踩）**：`--redigest` 把已消化反馈重吃时，因消化是"在旧档案上增量改"，**同一条偏好可能被叠加两次**（如 taste.md 出现重复"少看通稿"）。所以：① `--redigest` 默认关、文案标"高级/重来用"；② 这也是默认靠 `digested_at` 挡重吃的原因；③ 实现时可在 prompt 里提示"档案已含历史、勿重复堆叠"，但**不保证去重**——`--redigest` 的语义就是"我知道我在干嘛"。
- **归并/取最新（关键，喂 AI 前先做）**：`feedback` 表是**只追加事件日志**——同一条文章会有多行（👍→👎 改主意、取消＝`signal:null`、先表态后「记下」补 note 各写一行，见 [spec §14.1](../spec/01-科技日报.md#L208)）。消化前**先按文章定位键聚合**（`scope=article` 用 `issue_id`+`article_level`+`article_index`，整期用 `scope=issue`），同一条按 `created_at` **取最新**：`signal` 以最新非空值为最终态、最新为 `null` 视为"已撤回/无意见"；`note` 取该条最新一条非空备注（或保留时间线，按效果定）。**喂给 AI 的是归并后的最终态，不是原始多行**，避免自相矛盾/重复计数。注意：**这批被归并掉的旧行也要一起标记 `digested_at`**（它们同属本次已消化）。
- **分类路由（核心）**：让 AI 把每条反馈判成三类并分别产出**增量更新后的整份档案**：
  - 选题（多看/少看 主题·公司·人物）→ `taste.md`
  - 写作/语气/翻译/长度（"没说人话""翻译生硬""精读太长"）→ `style.md`
  - 硬性想看/不看某种子 → **追加到 `seed-suggestions.md`（只建议，绝不改 `sections.yaml`）**
- **增量与安全（务必照 [spec §9.4](../spec/01-科技日报.md)）**：prompt 要求"在旧档案基础上增量修改、保留既有偏好、不要重写掉"；每档附一行变更说明；**严禁让 AI 改 prompt 模板或 sections.yaml**。
- **档案落地**：真源是本地文件 `web/profiles/<板块>/{taste,style,seed-suggestions}.md`；首次不存在则以空骨架创建（结构见 spec §9.3）。
- **输出模型**：建议 `DigestFeedbackOutput { taste_md: str, style_md: str, seed_suggestions_append: str, changes: list[str] }`，沿用现有 Codex*Output 的 pydantic 校验风格。
- **复用**：`run_ai_task` / `build_provider_command` / `save_ai_task_run`（日志落 `web/logs/<run_id>/`）—— 不要另造一套。
- **标记消化**：成功写完档案后调用 `mark_feedback_digested([...])`（T03）。失败则不标记，保证可重试。
- **provider 优先级**沿用现状：CLI 阶段参数 > pipeline.yaml stage_providers > default_provider。
- **向后兼容**：无反馈 / Supabase 未配置时，阶段应安全跳过（不报错、不改档案），run-pipeline 照常往下走。

## 验收标准

- 准备几条不同类型的反馈（选题 / 写作 / 硬性关注）写入 feedback 表。
- `daily-news digest-feedback --section tech` 单跑后：
  - `taste.md`、`style.md` 各自**增量**出现对应偏好，旧内容保留；
  - `seed-suggestions.md` 只追加**建议**（`sections.yaml` 未被改动）；
  - 那几条反馈 `digested_at` 被写；再次跑不重复消化。
- **范围控制**：`--from/--to` 只消化该 `issue_date` 段、段外未消化反馈仍保持 `digested_at=null`；不带范围＝全部未消化。
- **重炒**：`--redigest --from X --to Y` 能把该段已消化反馈重吃一遍（验证逃生口可用）；默认不带 `--redigest` 时已消化的不会被重吃。
- `run-pipeline` 起手会自动消化（同上效果），其余阶段行为不变。
- `pytest -q` 全过；无反馈时 digest 阶段安全跳过。

## 非目标

- 不做注入（taste→粗筛选题=T06、style→写作=T07）。
- 不自动改 `sections.yaml`、不重写 prompt 模板。
- 不做定时（cron 归 M4）、不做反馈分析面板。

## 开放问题

- 消化时给 AI 看"那期日报全文"还是"被反馈命中的条目"？建议只喂命中条目 + 必要上下文，控 token；实现时按效果微调，记此处。

## 完成说明（开发填）

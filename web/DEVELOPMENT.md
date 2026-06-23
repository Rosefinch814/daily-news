# 开发文档

## v1 抓取策略

日报第一步以“新鲜度优先，部分热度增强”为原则。RSS 只负责发现候选新闻，不直接支撑头条精读；精读内容必须等候选入围后再抓正文。

### 数据源入口

- 一个媒体可以配置多个 RSS 入口，例如 TechCrunch 同时抓 `all` 和 `AI` 分类，Ars Technica 同时抓 `technology-lab` 和 `all`。
- Hacker News 使用 HNRSS 的热度参数，例如 `best?points=100` 和 `frontpage?points=50`，降低纯时间流噪音。
- Reuters 当前 RSS 地址不可用，先保留配置但禁用，不阻塞 MVP。

### 新鲜度窗口

- 默认保留最近 36 小时的内容，适配日报的跨时区和夜间发布。
- 更新频率较低的源可以放宽到 48 小时，例如 Ars Technica。
- 如果 RSS 条目没有发布时间，暂时保留该条，交给后续粗筛处理。

### 抓取与收敛

抓取阶段可以广一点，但不把全部内容交给 AI：

```text
多 feed 抓取
  -> 按发布时间过滤
  -> 按 URL / 标题去重
  -> 本地轻预筛压缩到 40-60 条
  -> Codex/AI 用标题 + RSS 摘要做语义粗筛
  -> 只给 Codex/AI 保留的候选补正文
  -> 再进入 Codex/Claude 精选和内容生成
```

当前配置大致会抓：

- The Verge：最多 10 条，36 小时
- Ars Technica：两个入口，各最多 20 条，48 小时
- TechCrunch：AI + all，各最多 20 条，36 小时
- Hacker News：best/frontpage 热度入口，36 小时
- 36氪：最多 30 条，36 小时
- 爱范儿：最多 20 条，36 小时

### 热度处理

- Hacker News 原生支持 `points=N` / `comments=N` 参数，因此可在抓取入口层做热度过滤。
- 其他媒体 RSS 通常只提供发布时间和分类标签，不提供浏览量/评论数等热度指标。
- 对普通媒体源，事件重要度由后续 AI 精选阶段判断，不在抓取阶段伪造热度。

## v1 Checkpoint 粗筛策略

粗筛拆成两层，避免纯规则漏掉英文、聚合快讯和语义相关内容：

1. `shortlist-mvp`：本地轻预筛，只做去重、基础加权、明显噪音降权，默认输出 `02_candidates.json`，数量控制在 60 条左右。
2. `shortlist-codex`：Codex/AI 读取 `02_candidates.json`，用标题和 RSS 摘要判断“保留 / 备选 / 丢弃”，输出并校验 `02_codex_shortlist.json`。
3. `enrich-mvp`：优先读取 `02_codex_shortlist.json`，只对 `keep_item_ids + maybe_item_ids` 补正文；如果该文件不存在，才临时回退到本地预筛结果。

Codex/AI 粗筛负责处理：

- 英文标题和摘要的中文理解，不要求第一步先翻译。
- 聚合类新闻的拆解判断，例如只保留其中真正命中关注清单的部分。
- 同主题但不同表达的识别，例如 AI 芯片、GPU、数据中心算力、半导体供应链。
- 对“想看 / 不想看”的语义判断，规则命中只作为参考，不作为最终决定。

`02_codex_shortlist.json` 必须包含：

- `keep_item_ids`
- `maybe_item_ids`
- `drop_item_ids`
- `items[].source_item_id`
- `items[].decision`
- `items[].category`
- `items[].relevance_score`
- `items[].importance_score`
- `items[].reason`
- `items[].is_aggregate`
- `items[].aggregate_highlights`

## 本轮 Checkpoint MVP 实测工作流

本轮验证 run id：

```text
tech-2026-06-23-165511
```

本轮目标是验证完整静态日报链路，不写 Supabase：

```text
抓取 RSS
  -> 本地轻预筛
  -> Codex/AI 粗筛
  -> 正文补全
  -> Codex/AI 最终选题
  -> Codex/AI 中文日报结构
  -> 渲染 HTML
  -> 人工确认 HTML
```

### Step 1：抓取 RSS

命令：

```bash
./.venv/bin/daily-news fetch-mvp --section tech --date 2026-06-23
```

输出：

```text
web/runs/<run_id>/01_raw_items.json
```

本轮结果：

- 有效新闻：119 条
- 失败项：0
- 来源覆盖：36氪、爱范儿、TechCrunch、The Verge、Ars Technica、Hacker News

设计要点：

- 第一阶段只发现新闻，不抓全文。
- 通过 RSS 多入口扩大覆盖面。
- Hacker News 入口使用 points 参数做热度增强。
- 其他媒体主要按新鲜度抓取，不伪造热度。

### Step 2a：本地轻预筛

命令：

```bash
./.venv/bin/daily-news shortlist-mvp --run-id <run_id>
```

输出：

```text
web/runs/<run_id>/02_candidates.json
```

本轮结果：

- 输入：119 条 raw items
- 输出：60 条 candidates

设计要点：

- 这一步不是最终粗筛，只是 AI 输入池。
- 规则负责去重、基础加权、明显噪音降权。
- 默认保留 60 条，并允许未命中关注词但可能有价值的新闻进入 AI 判断。

### Step 2b：Codex/AI 语义粗筛

当前 MVP 由 Codex 手工读取 `02_candidates.json` 并生成：

```text
web/runs/<run_id>/02_codex_shortlist.json
```

校验命令：

```bash
./.venv/bin/daily-news shortlist-codex --run-id <run_id>
```

本轮结果：

- 保留：17 条
- 备选：10 条
- 丢弃：33 条
- 合计：60 条

设计要点：

- `keep + maybe` 进入正文补全。
- `drop` 不进入后续正文抓取。
- AI 粗筛负责英文理解、聚合快讯拆解、同主题识别、规则误判纠偏。
- 校验器要求顶层 `keep/maybe/drop` 列表与 `items[]` 完全一致，避免漏项。

后续自动化替换点：

- 用 `claude -p` 或其他 AI API 生成 `02_codex_shortlist.json`。
- prompt 输入应包含：标题、RSS 摘要、来源、发布时间、规则分数、规则命中原因、关注清单、不想看清单。
- prompt 输出必须严格符合 `CodexShortlistOutput` schema。

### Step 3：正文补全

命令：

```bash
./.venv/bin/daily-news enrich-mvp --run-id <run_id>
```

输出：

```text
web/runs/<run_id>/03_enriched_candidates.json
```

本轮结果：

- 输入：27 条 Codex/AI 选中候选
- 有正文：27 条
- 正文失败：0 条

设计要点：

- 当前流程已经经过 AI 粗筛，因此默认抓取全部 `keep + maybe` 正文。
- `--body-candidates N` 只作为手动限制参数，默认不限制。
- 正文补全只更新 `raw_item.content` 和抓取状态，不再二次排序、不再丢弃 AI 选中的候选。

曾修正的问题：

- 早期默认只抓前 8 条正文，这是旧流程下的保守策略。
- 已改为默认抓全部 Codex/AI 选中候选。
- 早期正文补全后又调用规则排序，导致 27 条被压回 17 条；已改为保留 AI 选中集合。

### Step 4：Codex/AI 最终选题

当前 MVP 由 Codex 读取 `03_enriched_candidates.json` 并生成：

```text
web/runs/<run_id>/04_selection.json
```

校验命令：

```bash
./.venv/bin/daily-news select-codex --run-id <run_id>
```

本轮结果：

- 头条候选：5 条，合并 10 个来源
- 速览候选：12 条，合并 13 个来源
- 丢弃：4 条

本轮头条主线：

1. Reflection AI 向 SpaceX 购买 Nvidia GB300 算力。
2. 凌川科技与 Groq 的 AI 芯片商业化动态。
3. Nvidia 液冷、Microsoft 燃气数据中心与 AI 基建资源约束。
4. Tesla Autopilot 致命事故与 NHTSA 调查。
5. OpenAI 将模型能力用于漏洞修复和网络防御。

设计要点：

- 同一事件或同一主题可以合并多个来源。
- `headline_item_ids` / `brief_item_ids` 是来源 ID 展开列表。
- `headlines[]` / `briefs[]` 是最终条目列表。
- 摘要打印应区分“条目数”和“来源数”，避免误读。

后续自动化替换点：

- 用 AI 根据全文候选生成 `04_selection.json`。
- prompt 必须明确：头条 3-5 条，速览 10-15 条，允许合并同事件，必须给出丢弃原因。

### Step 5：Codex/AI 中文日报结构

当前 MVP 由 Codex 读取 `04_selection.json` 和 `03_enriched_candidates.json` 并生成：

```text
web/runs/<run_id>/05_issue.json
```

校验命令：

```bash
./.venv/bin/daily-news compose-codex --run-id <run_id>
```

本轮结果：

- 头条精读：5 条
- 速览：12 条
- 校验通过

写作规则：

- `summary_zh` 只写事实。
- `read_body_zh` 只写事实，不写判断。
- `ai_impact` 只放 AI 判断和影响分析。
- 速览不生成 `read_body_zh`，只保留中文标题和事实摘要。

后续自动化替换点：

- 用 AI 根据 `04_selection.json` 和候选正文生成 `05_issue.json`。
- prompt 必须强制事实字段和判断字段分离。
- Pydantic 校验失败时，应保存失败现场，并允许一次 repair 调用。

### Step 6：渲染静态 HTML

命令：

```bash
./.venv/bin/daily-news render-mvp --run-id <run_id>
```

输出：

```text
web/dist/issues/2026-06-23.html
web/dist/index.html
web/dist/latest.html
```

本轮检查：

- `viewport`: ok
- `mobile_520`: ok
- `headlines`: ok
- `briefs`: ok

文件含义：

- `issues/YYYY-MM-DD.html`：按日期归档的正式日报页。
- `index.html`：网站首页，访问静态站根路径时打开。
- `latest.html`：最新一期快捷入口。

设计要点：

- 公开发布只需要 `web/dist/`。
- 当前 v1 没有浏览器后端接口，公网访问不依赖数据库。
- 手机端需要保留 `820px` 和 `520px` 断点。

### Step 7：数据库同步

当前决策：MVP 暂不执行。

原因：

- 当前产品形态是静态 HTML，公网访问不需要后端接口。
- 数据库主要用于生成侧留档、调试、反馈和口味档案，不是 v1 静态浏览的必要条件。
- 先把静态日报流程、内容质量和视觉体验跑顺，再接 Supabase。

如果后续要同步，命令是：

```bash
./.venv/bin/daily-news sync --run-id <run_id>
```

后续启用数据库时，建议只同步人工确认过的 run，避免调试数据污染云端。

## 自动化方向

短期自动化目标：

```text
fetch-mvp
  -> shortlist-mvp
  -> AI 生成 02_codex_shortlist.json
  -> shortlist-codex
  -> enrich-mvp
  -> AI 生成 04_selection.json
  -> select-codex
  -> AI 生成 05_issue.json
  -> compose-codex
  -> render-mvp
```

需要重点微调的地方：

- AI 粗筛 prompt：决定召回率，避免漏掉英文、聚合类和规则没命中的重要新闻。
- 正文抓取质量：不同站点正文提取效果不同，需要持续观察失败源。
- 最终选题偏好：头条数量、速览数量、是否偏 AI 基建、芯片、自动驾驶或产品。
- 中文写作风格：事实密度、标题力度、精读长度、`ai_impact` 的判断尺度。
- 静态页面体验：手机端行距、展开区域、速览密度、首页是否只跳转最新一期。

当前推荐策略：

- v1 继续使用本地 JSON + 静态 HTML。
- 暂不强依赖 Supabase。
- 流程稳定后，再把 Codex 的粗筛、选题、写作标准固化为 Claude prompt。
- 再下一步才考虑自动定时生成、GitHub Actions、历史数据库、反馈和口味档案。

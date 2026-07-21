---
id: T16
title: 小红书封面 v2 独立模板（标题即主视觉）
status: done
依赖: [T15]
里程碑: M4
---

## 目标

为 `export-xhs` **新增第三个封面模板 `v2`**，在 T15 的设计基础上实现「标题即主视觉」：在真图上层层收敛后，只留 **5 处**实打实的视觉增量。**v2 是独立模板，不是对 `single-hook` 的原地升级**；已有 `classic` 和 `single-hook` 的视觉、文案逻辑、默认行为必须完全不变。

**除新增模板选项、独立渲染分支和输出目录外，本工单只做 CSS / 版式改动；不含新 AI 任务、不做文案磁化**（归 T17）。视觉权威见 [design §12.2](../../design/设计规范.md) + 原型 [design/prototype/xhs-封面-v2-数字主体.html](../../design/prototype/xhs-封面-v2-数字主体.html)（**像素级照此**，含 class 名）；行为见 [spec §4](../spec/小红书图组导出.md)。

## 范围（净差别 5 处，别多改）

新建 v2 专用封面渲染分支（如 `v2_cover_card`），以 T15 为设计参照做且仅做这 5 处差异。**不直接修改 `single_hook_cover_card` 或它的文案取值。**

1. **眉题**：`.cv2-kicker` 实心朱红 chip → `.eyebrow` **平文红字**（mono 700 26px/.16em `--seal`，无底色/无圆角，`margin-bottom 28`）。
2. **关键词高亮**：`.cv2-big em` 仅换 `--seal` 色 → `.mark` **朱红粗底线**（`color:--seal;text-decoration:underline;text-decoration-color:--seal;text-decoration-thickness:11px;text-underline-offset:10px;text-decoration-skip-ink:none`）。
3. **标题字号**：`94px`（及 L/M 双档）→ **单一 100px**（`.title3`，Noto Serif SC 900/1.16）。**去掉 L/M 双档**，改为"默认 100px + 溢出降档"（见实现要点）。
4. **支撑句**：短一句 → **放足两三行**（`.sub3` 40px/1.44 `--ink-3`，`margin-top 34`）；文案长度由 `cover_sub` 收敛区间承载（spec §7），版式允许两三行。
5. **页脚**：去掉 `.bar`（120×6 红小节棒）；`.foot3` 一行 `.row`（mono 700 22px `--muted`）+ `.swipe`（`--seal` + chevron SVG）。padding 收成 `26 84 56`。

> class 名对齐原型：`.coverv2 / .cv2-head / .hook3 / .eyebrow / .title3 / .mark / .sub3 / .foot3`；品牌骨架条 padding 收成 `56 84 28`、内容区 `.hook3` padding `30 84`。

## 涉及文件

- `web/src/daily_news/main.py`：`--cover-template` 选项新增 `v2`，默认值仍为 `classic`。
- `web/src/daily_news/xhs_export.py`：新增 v2 独立 HTML/CSS 渲染分支；`CoverTemplate` 新增 `v2`；v2 默认输出目录为 `web/runs/xhs/<date>-v2/`。字号/内边距落**可配置常量**（延续 T12/T15）。
- `web/tests/test_xhs_export.py`：覆盖下方验收（尤其溢出降档）。
- 只动 `web/`。

## 实现要点

- **CSS 零裸值**：照 design §1 token，与原型一致，不自造新色/新裸值。
- **版本隔离**：`classic` 继续输出到 `<date>/`，`single-hook` 继续输出到 `<date>-single-hook/`，`v2` 输出到 `<date>-v2/`；显式 `--output-dir` 的覆盖语义不变。
- **旧模板冻结**：不改 `classic` / `single-hook` 的 DOM、CSS、字数收敛、重音、字号档位或验证逻辑。共享工具函数只能在有回归测试证明旧行为不变时复用。
- **字号溢出保护（替代 L/M 双档）**：`.title3` 默认 100px；为 v2 新增独立安全区校验（可复用底层测量工具，但不改 T15 的降档规则）。若标题包围盒溢出方形安全区（y 180–1260），**程序侧确定性下调字号**（100→90→82…）直到整句落内，非 AI。本工单不依赖 T17，v2 先用现有 `cover_hook` 收敛文案也要能跑、能降档。
- 关键词 `.mark` 的**来源不变**（仍是现有 emphasis 机制：收敛输出标注 + 正则兜底），本工单只改**呈现**（换色→粗底线）、**一条别滥标**。
- 支撑句放足只是版式与收敛区间的事，不新增 AI。
- 不动图组结构/顺序、不动头条页/速览页、不动 caption。

## 验收标准

- `export-xhs --cover-template classic`、`single-hook`、`v2` 三种均可成功导出；不传参时仍为 `classic`。
- `--cover-template v2` 的封面为「标题即主视觉」——平文红字眉题、100px 大标题、关键词朱红粗底线、支撑句两三行、页脚无红小节棒；与原型 [xhs-封面-v2-数字主体.html](../../design/prototype/xhs-封面-v2-数字主体.html) 一致。
- 三种默认目录分别为 `<date>/`、`<date>-single-hook/`、`<date>-v2/`，同日导出不互相覆盖。
- 回归测试锁定 `classic` 和 `single-hook` 的封面 DOM/关键文案/字号档位/输出目录，证明两者与 T16 前行为一致。
- **溢出降档**：构造一条超长标题 → 字号自动下调、整句仍落安全区、无溢出/无裁切/无省略号。
- 常规标题走 100px、不触发降档。
- 真数据导出一期，DOM 高度检查封面无溢出。
- `--no-ai-condense` 下仍成功导出、可复现。
- `pytest -q` 全过。

## 非目标

- 不做文案磁化（T17）、不改支撑句/速览收敛区间语义（T13）、不改 caption（T14）。
- 不把 v2 设为默认，不删除、重命名或改造 `classic` / `single-hook`。
- 不恢复被收敛掉的 数字 hero / 情绪前缀 / 引号锚 / 关联签（v2 已明确去掉）。
- 不改头条页/速览页视觉。

## 完成说明（开发填）

- 2026-07-21：已新增独立 `v2` 封面模板、`<date>-v2/` 输出目录和 100→90→82px 安全区降档；`classic` / `single-hook` 回归锁定，三种真数据导出成功，`pytest -q` 86 passed。

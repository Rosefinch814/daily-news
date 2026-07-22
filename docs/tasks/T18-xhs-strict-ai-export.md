---
id: T18
title: 小红书发布导出严格 AI 模式（不允许静默回退）
status: done
依赖: [T13, T14, T17]
里程碑: M4
---

## 目标

恢复并固化小红书生产链路的原始产品边界：所有需要缩写或标题改写的文案都必须由 AI 生成。正常发布导出中，任一必需 AI 阶段失败或输出未通过校验，整次导出必须失败，不能静默产出规则截断、原标题回退或固定日期标题。

## 范围

1. `xhs_condense` 失败、缺槽位或槽位输出越界：中止导出。
2. v2 的 `xhs_magnetize` 失败，或默认使用的克制版未通过反漂移校验：中止导出。
3. `xhs_note_title` 失败或标题未通过字数/事实校验：中止导出。
4. 导出失败时清理当次目录中可能被误发的 PNG、`caption.txt`、`cards.html` 和标题变体文件；只有全部成功后才写 AI 成功凭证。
5. 从公开 CLI 和日常脚本移除跳过 AI 的开关；非 AI 渲染只保留为代码内部测试/视觉预览能力，不得冒充可发布产物。

## 涉及文件

- `docs/spec/小红书图组导出.md`
- `docs/milestones/01-科技日报.md`
- `docs/tasks/T13-xhs-condense-ai.md`
- `docs/tasks/T14-xhs-note-title-body.md`
- `docs/tasks/T17-xhs-cover-title-magnetize.md`
- `web/src/daily_news/xhs_export.py`
- `web/src/daily_news/main.py`
- `web/tests/test_xhs_export.py`
- `scripts/generate_today.sh`
- `scripts/export_xhs_today.sh`

## 实现要点

- 严格模式是 `daily-news export-xhs` 的唯一对外行为，三个 AI 任务仍统一经 `ai_engine` 调用。
- 程序侧反漂移、字数和完整句校验保留；校验失败的处理从“回退”改为“阻断”。
- caption 的 hashtag、三条头条原题和 slogan 仍然确定性拼装，因为它们不做缩写/改写；三条头条必须逐字复制 issue，继续零漂移。
- v2 的冲版备选可被校验拒绝，但默认上封面的克制版必须是 AI 产出且校验通过。

## 验收标准

- provider 不可用时，`export-xhs` 非零退出，不产出 PNG / caption / cards HTML。
- `xhs_condense` 缺槽位或返回越界文本时，导出被阻断而非规则截断。
- v2 克制版磁化标题越界，导出被阻断而非改用 `cover_hook`。
- 发布标题超 20 字或事实漂移，导出被阻断而非改用固定日期标题。
- 成功导出写入 AI 凭证，列出本次必需阶段并全部标记成功。
- 公开 CLI 不再提供 `--no-ai-condense`，日常脚本不再读 `XHS_NO_AI_CONDENSE`。
- `pytest -q` 全过；7 月 22 日 v2 真数据重跑后，三个 AI 阶段均有成功记录。

## 非目标

- 不改小红书视觉模板、字数区间和反漂移规则。
- 不把 caption 中逐字复制的三条头条交给 AI 改写。
- 不做小红书自动发布。

## 开放问题

无。

## 完成说明（开发填）

2026-07-22：已将对外 `export-xhs` 改为严格 AI 模式，移除 CLI/日常脚本的跳过 AI 开关；收敛、v2 磁化克制版和发布标题任一失败即阻断并清理可发布产物，成功后写 `ai_provenance.json`。`pytest -q` 99 passed；7 月 22 日 v2 真数据导出已验证 `xhs_condense` / `xhs_magnetize` / `xhs_note_title` 均为 Codex success，7 张图 + caption 生成。

---
id: T01
title: 清理迁移到客户端渲染后遗留的旧 SSR 死代码
status: todo
依赖: []
里程碑: M1
---

## 目标

架构已切到「前后端分离 · 客户端渲染」（`build_frontend_app` 产出壳 HTML + `data/` JSON，`frontend/app.js` 客户端渲染）。旧的 Jinja 整页服务端渲染路径已无人调用，但代码和模板还留着。删掉它，避免「两套渲染来源」让人分不清哪套权威。

## 范围

删除已确认无引用的旧 SSR 渲染路径，删完测试仍全过、`generate` / `render` / `render-mvp` 仍能产出和现在一致的 `dist/`。只动 `web/`。

## 涉及文件

- `web/src/daily_news/render.py`：删除 `render_issue()`、`render_index()`、`copy_issue_to_legacy_path()`、`_env()` 四个函数，及对应的 `TEMPLATES_DIR` import。保留 `build_frontend_app()`、`export_issue_data()`、`_render_app_shell()`、`_frontend_env()` 等仍在用的。
- `web/src/daily_news/paths.py`：删除 `TEMPLATES_DIR` 常量（删后确认无其他引用）。
- `web/templates/issue.html.j2`、`web/templates/index.html.j2`：删除；`web/templates/` 若清空则一并删目录。
- `web/tests/test_render.py`：测试体已在调 `build_frontend_app`，函数名 `test_render_issue_and_index` 可顺手改成贴合新路径的名字（如 `test_build_frontend_app`），非必须。

## 实现要点

- 删之前再 grep 一遍确认无引用：`render_issue|render_index|copy_issue_to_legacy|TEMPLATES_DIR|issue.html.j2`（产品侧已核对，main.py 仅 import `build_frontend_app`，`render_existing` 也走 `build_frontend_app`）。
- 不要动客户端渲染路径的任何行为；本工单是纯删除，不改产物结构。
- 视觉/组件唯一来源仍是 [design/设计规范.md](../../design/设计规范.md) 与高保真原型，删模板不影响它们。

## 验收标准

- `grep -rn "render_issue\|render_index\|copy_issue_to_legacy\|TEMPLATES_DIR\|issue.html.j2" web/src web/tests` 无结果（除非是被删函数自己）。
- `pytest -q` 全过。
- `daily-news render-mvp --run-id tech-2026-06-23-165511` 重新生成的 `dist/` 与当前一致（壳 HTML + `data/` JSON）。

## 非目标

- 不碰 Supabase 相关代码（其去留另议）。
- 不改 `generate` 自动路径里「正文补全后又重排」的问题（那是另一张工单）。
- 不新增单文件导出能力（见 spec §15，M4 再议）。

## 开放问题

（无）

## 完成说明（开发填）

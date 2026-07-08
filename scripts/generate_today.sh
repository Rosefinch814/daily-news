#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"
DAILY_NEWS="$WEB_DIR/.venv/bin/daily-news"

ISSUE_DATE="${1:-$(TZ=Asia/Shanghai date +%F)}"
SECTION="${SECTION:-tech}"
EXPORT_XHS="${EXPORT_XHS:-1}"
RENDER_OWNER="${RENDER_OWNER:-0}"

if [[ ! -x "$DAILY_NEWS" ]]; then
  echo "daily-news command not found: $DAILY_NEWS" >&2
  echo "Create the web virtualenv and install the project first." >&2
  exit 1
fi

cd "$WEB_DIR"

pipeline_args=(
  run-pipeline
  --section "$SECTION"
  --date "$ISSUE_DATE"
)

if [[ "$RENDER_OWNER" == "1" ]]; then
  pipeline_args+=(--render-owner)
fi

echo "==> Generating daily issue: section=$SECTION date=$ISSUE_DATE"
"$DAILY_NEWS" "${pipeline_args[@]}"

issue_json="$WEB_DIR/dist/data/issues/$ISSUE_DATE.json"
if [[ ! -f "$issue_json" ]]; then
  echo "Issue JSON was not generated: $issue_json" >&2
  exit 1
fi

if [[ "$EXPORT_XHS" == "1" ]]; then
  xhs_args=(export-xhs --date "$ISSUE_DATE")
  if [[ "${XHS_NO_AI_CONDENSE:-0}" == "1" ]]; then
    xhs_args+=(--no-ai-condense)
  fi

  echo "==> Exporting Xiaohongshu cards: date=$ISSUE_DATE"
  "$DAILY_NEWS" "${xhs_args[@]}"
fi

echo "==> Done"
echo "Issue JSON: $issue_json"
echo "Public app: $WEB_DIR/dist/issues/$ISSUE_DATE.html"
if [[ "$EXPORT_XHS" == "1" ]]; then
  echo "XHS output: $WEB_DIR/runs/xhs/$ISSUE_DATE"
fi

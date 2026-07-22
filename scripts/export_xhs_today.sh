#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"
DAILY_NEWS="$WEB_DIR/.venv/bin/daily-news"

ISSUE_DATE="${1:-$(TZ=Asia/Shanghai date +%F)}"
XHS_PROVIDER="${XHS_PROVIDER:-codex}"

if [[ ! -x "$DAILY_NEWS" ]]; then
  echo "daily-news command not found: $DAILY_NEWS" >&2
  echo "Create the web virtualenv and install the project first." >&2
  exit 1
fi

issue_json="$WEB_DIR/dist/data/issues/$ISSUE_DATE.json"
if [[ ! -f "$issue_json" ]]; then
  echo "Issue JSON not found: $issue_json" >&2
  echo "Generate the daily issue first:" >&2
  echo "  $ROOT_DIR/scripts/generate_daily.sh $ISSUE_DATE" >&2
  exit 1
fi

cd "$WEB_DIR"

args=(export-xhs --date "$ISSUE_DATE")
args+=(--provider "$XHS_PROVIDER")

echo "==> Exporting Xiaohongshu cards: date=$ISSUE_DATE provider=$XHS_PROVIDER"
"$DAILY_NEWS" "${args[@]}"

echo "==> Done"
echo "XHS output: $WEB_DIR/runs/xhs/$ISSUE_DATE"
echo "Caption: $WEB_DIR/runs/xhs/$ISSUE_DATE/caption.txt"

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"
DAILY_NEWS="$WEB_DIR/.venv/bin/daily-news"

ISSUE_DATE="${1:-$(TZ=Asia/Shanghai date +%F)}"
SECTION="${SECTION:-tech}"
RENDER_OWNER="${RENDER_OWNER:-0}"

if [[ ! -x "$DAILY_NEWS" ]]; then
  echo "daily-news command not found: $DAILY_NEWS" >&2
  echo "Create the web virtualenv and install the project first." >&2
  exit 1
fi

cd "$WEB_DIR"

args=(
  run-pipeline
  --section "$SECTION"
  --date "$ISSUE_DATE"
)

if [[ "$RENDER_OWNER" == "1" ]]; then
  args+=(--render-owner)
fi

echo "==> Generating daily issue: section=$SECTION date=$ISSUE_DATE"
"$DAILY_NEWS" "${args[@]}"

echo "==> Done"
echo "Issue JSON: $WEB_DIR/dist/data/issues/$ISSUE_DATE.json"
echo "Public app: $WEB_DIR/dist/issues/$ISSUE_DATE.html"

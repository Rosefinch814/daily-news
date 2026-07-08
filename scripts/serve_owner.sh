#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNER_DIR="$ROOT_DIR/web/dist-owner"
PORT="${PORT:-8001}"
HOST="${HOST:-127.0.0.1}"

if [[ ! -f "$OWNER_DIR/index.html" ]]; then
  echo "Owner build not found: $OWNER_DIR/index.html" >&2
  echo "Generate it first with:" >&2
  echo "  cd $ROOT_DIR/web" >&2
  echo "  ./.venv/bin/daily-news run-pipeline --section tech --date YYYY-MM-DD --run-id <run-id> --resume --render-owner" >&2
  exit 1
fi

cd "$OWNER_DIR"

echo "Serving owner app from: $OWNER_DIR"
echo "Open: http://$HOST:$PORT/"
echo "Latest: http://$HOST:$PORT/latest.html"
python3 -m http.server "$PORT" --bind "$HOST"

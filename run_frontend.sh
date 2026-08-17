#!/usr/bin/env bash
# Idempotent launcher used by Windows Task Scheduler through wsl.exe.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${GUDETNIGHT_FRONTEND_LOG:-$APP_DIR/frontend_wsl.log}"
PYTHON="${GUDETNIGHT_PYTHON:-python3}"

cd "$APP_DIR"

# Both boot and logon triggers may fire. Do not start a duplicate server.
if curl -fsS --max-time 3 http://127.0.0.1:8898/ >/dev/null 2>&1; then
  printf '[%s] Frontend already healthy; skipping duplicate start.\n' "$(date -Iseconds)" >> "$LOG"
  exit 0
fi

printf '[%s] Starting GudetNight frontend.\n' "$(date -Iseconds)" >> "$LOG"
exec "$PYTHON" -c 'import uvicorn; from app import app; uvicorn.run(app, host="0.0.0.0", port=8898)' >> "$LOG" 2>&1

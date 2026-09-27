#!/usr/bin/env bash
set -Eeuo pipefail
RUNTIME_DIR="${COBOT_DATA_UI_RUNTIME_DIR:-/home/agilex/cobot_magic/task3/jiaan/runtime/cobot-data-console-v1}"
PORT="${COBOT_DATA_UI_PORT:-8015}"
PID_FILE="$RUNTIME_DIR/service.pid"
APP="cobot_console.api:app"

[[ -r "$PID_FILE" ]] || { echo "Cobot data console PID file is absent." >&2; exit 1; }
pid="$(tr -d '[:space:]' < "$PID_FILE")"
[[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || { echo "Cobot data console process is not running." >&2; exit 1; }
tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$APP" || { echo "PID $pid is not the registered console." >&2; exit 1; }
identity="$(curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/api/console/identity")"
status="$(curl --noproxy '*' -fsS "http://127.0.0.1:$PORT/api/console/status")"
printf '%s\n%s\nPID=%s\n' "$identity" "$status" "$pid"
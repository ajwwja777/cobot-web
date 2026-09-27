#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
V1_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUNTIME_DIR="${TASK5_RUNTIME_DIR:-$V1_DIR/runtime}"
PID_FILE="$RUNTIME_DIR/task5.pid"
PYTHON="${TASK5_PYTHON:-/home/agilex/miniconda3/envs/cobot-station/bin/python}"
PORT="${TASK5_PORT:-8015}"
APP="capture_core.api:app"

[[ -r "$PID_FILE" ]] || { echo "Task5 v1 is not running"; exit 1; }
pid="$(tr -d '[:space:]' < "$PID_FILE")"
[[ "$pid" =~ ^[0-9]+$ ]] || { echo "Invalid Task5 pidfile" >&2; exit 1; }
[[ -r "/proc/$pid/cmdline" ]] || { echo "Task5 v1 PID is stale: $pid" >&2; exit 1; }
tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$APP" || {
  echo "PID $pid is not the Task5 API; refusing to treat it as managed." >&2
  exit 1
}

"$PYTHON" - "$PORT" <<'PY'
import json
import sys
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=1.0) as response:
    payload = json.load(response)
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY
echo "Task5 v1 PID: $pid"

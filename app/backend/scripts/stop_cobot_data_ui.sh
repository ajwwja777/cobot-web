#!/usr/bin/env bash
set -Eeuo pipefail
RUNTIME_DIR="${COBOT_DATA_UI_RUNTIME_DIR:-/home/agilex/cobot_magic/task3/jiaan/runtime/cobot-data-console-v1}"
PORT="${COBOT_DATA_UI_PORT:-8015}"
PID_FILE="$RUNTIME_DIR/service.pid"
APP="cobot_console.api:app"
PYTHON="${TASK5_PYTHON:-/home/agilex/miniconda3/envs/cobot-station/bin/python}"

[[ -r "$PID_FILE" ]] || { echo "Cobot data console is not registered as running."; exit 0; }
pid="$(tr -d '[:space:]' < "$PID_FILE")"
if [[ ! "$pid" =~ ^[0-9]+$ ]] || [[ ! -r "/proc/$pid/cmdline" ]]; then
  echo "Stale PID file; refusing broad process cleanup." >&2
  exit 1
fi
if ! tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$APP"; then
  echo "PID $pid is not the registered console; refusing to stop it." >&2
  exit 1
fi

"$PYTHON" - "$PORT" <<'PY'
import json, sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open("http://127.0.0.1:%s/api/console/status" % sys.argv[1], timeout=1.0) as response:
        payload = json.load(response)
except Exception as error:
    print("Cannot verify console idle state: %s" % type(error).__name__, file=sys.stderr)
    raise SystemExit(1)
if payload.get("active_mode") is not None:
    print("Console has active_mode=%s; finish or abort the active episode before stopping." % payload.get("active_mode"), file=sys.stderr)
    raise SystemExit(1)
PY

kill -TERM "$pid"
for _attempt in $(seq 1 100); do
  kill -0 "$pid" 2>/dev/null || break
  [[ "$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)" == "Z" ]] && break
  sleep 0.1
done
if kill -0 "$pid" 2>/dev/null && [[ "$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)" != "Z" ]]; then
  echo "PID $pid did not stop after SIGTERM; no stronger signal was sent." >&2
  exit 1
fi
mv -- "$PID_FILE" "$RUNTIME_DIR/service.pid.stopped.$(date -u +%Y%m%dT%H%M%SZ).$$"
echo "Cobot data console stopped: PID=$pid"
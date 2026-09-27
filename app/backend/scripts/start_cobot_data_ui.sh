#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CODE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUNTIME_DIR="${COBOT_DATA_UI_RUNTIME_DIR:-/home/agilex/cobot_magic/task3/jiaan/runtime/cobot-data-console-v1}"
PYTHON="${TASK5_PYTHON:-/home/agilex/miniconda3/envs/cobot-station/bin/python}"
ROS_SETUP="${TASK5_ROS_SETUP:-/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash}"
RLT_SCRIPTS="${COBOT_RLT_SCRIPTS_DIR:-/media/agilex/Getea1/jiaan/projects/cobot-realworld-vla/deployments/openpi-rlt/plug-insertion-stage1-v2/runtime-overlay/methods/openpi_rlt/scripts}"
HOST="${COBOT_DATA_UI_HOST:-0.0.0.0}"
PORT="${COBOT_DATA_UI_PORT:-8015}"
PUBLIC_HOST="${TASK5_PUBLIC_HOST:-10.7.165.64}"
APP="cobot_console.api:app"
PID_FILE="$RUNTIME_DIR/service.pid"
LOG_DIR="$RUNTIME_DIR/logs"
PUBLIC_URL="http://$PUBLIC_HOST:$PORT/"

mkdir -p -- "$RUNTIME_DIR" "$LOG_DIR" "$RUNTIME_DIR/ros"

is_managed_pid() {
  local pid="$1"
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$APP"
}

if [[ -r "$PID_FILE" ]]; then
  pid="$(tr -d '[:space:]' < "$PID_FILE")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null && is_managed_pid "$pid"; then
    echo "网页已运行：$PUBLIC_URL"
    exit 0
  fi
  mv -- "$PID_FILE" "$RUNTIME_DIR/service.pid.stale.$(date -u +%Y%m%dT%H%M%SZ).$$"
fi

[[ -x "$PYTHON" ]] || { echo "Python is not executable: $PYTHON" >&2; exit 1; }


set +u
if [[ -n "$ROS_SETUP" && -f "$ROS_SETUP" && "${COBOT_READ_ONLY:-0}" != 1 ]]; then source "$ROS_SETUP"; fi
set -u

# ROS XML-RPC resolves the local master through the machine hostname.  The
# workstation exports an HTTP proxy globally, so explicitly bypass it for all
# local ROS and console traffic inherited by this service and its child jobs.
local_no_proxy="localhost,127.0.0.1,agilex,192.168.1.50,10.7.165.64"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}$local_no_proxy"
export no_proxy="${no_proxy:+$no_proxy,}$local_no_proxy"

if ! "$PYTHON" - "$PORT" <<'PY'
import socket, sys
try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.2):
        raise SystemExit(1)
except OSError:
    raise SystemExit(0)
PY
then
  echo "Port $PORT is already in use; no process was stopped." >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="$LOG_DIR/service-$stamp.log"
cd -- "$CODE_DIR"
# Camera/status polling must not synchronously append every GET to the external
# disk while model weights are being restored. Application/errors stay logged.
nohup env   PYTHONPATH="$CODE_DIR:$RLT_SCRIPTS${PYTHONPATH:+:$PYTHONPATH}"   COBOT_RLT_LIFECYCLE_STATE="${COBOT_RLT_LIFECYCLE_STATE:-/home/agilex/cobot_magic/task3/jiaan/runtime/cobot-rlt-backend-v1/state.json}"   ROS_HOME="$RUNTIME_DIR/ros"   ROS_LOG_DIR="$RUNTIME_DIR/ros"   "$PYTHON" -m uvicorn "$APP" --host "$HOST" --port "$PORT" --timeout-graceful-shutdown 3 --no-access-log   >>"$log_file" 2>&1 &
pid=$!
printf '%s\n' "$pid" > "$PID_FILE.pending"
mv -- "$PID_FILE.pending" "$PID_FILE"

readiness_timeout="${COBOT_DATA_UI_READINESS_TIMEOUT_SEC:-90}"
[[ "$readiness_timeout" =~ ^[0-9]+$ ]] && (( readiness_timeout > 0 )) || {
  echo "COBOT_DATA_UI_READINESS_TIMEOUT_SEC must be a positive integer." >&2
  exit 1
}
deadline=$((SECONDS + readiness_timeout))
while (( SECONDS < deadline )); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Cobot data console exited before readiness. Log: $log_file" >&2
    tail -n 60 -- "$log_file" >&2 || true
    exit 1
  fi
  if "$PYTHON" - "$PORT" <<'PY'
import json, sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open("http://127.0.0.1:%s/api/console/identity" % sys.argv[1], timeout=0.5) as response:
        payload = json.load(response)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if payload.get("service") == "cobot-data-console-v1" else 1)
PY
  then
    echo "网页已就绪：$PUBLIC_URL"
    exit 0
  fi
  sleep 0.2
done

kill -TERM "$pid" 2>/dev/null || true
echo "Cobot data console readiness timeout; the owned process was stopped. Log: $log_file" >&2
exit 1

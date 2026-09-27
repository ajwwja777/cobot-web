#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
V1_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(cd -- "$V1_DIR/.." && pwd)"
RUNTIME_DIR="${TASK5_RUNTIME_DIR:-$V1_DIR/runtime}"
LOG_DIR="${TASK5_LOG_DIR:-$V1_DIR/logs}"
PID_FILE="$RUNTIME_DIR/task5.pid"
PYTHON="${TASK5_PYTHON:-/home/agilex/miniconda3/envs/cobot-station/bin/python}"
ROS_SETUP="${TASK5_ROS_SETUP:-/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash}"
HOST="${TASK5_HOST:-0.0.0.0}"
PORT="${TASK5_PORT:-8015}"
PUBLIC_HOST="${TASK5_PUBLIC_HOST:-10.7.165.64}"
APP="capture_core.api:app"
START_TIMEOUT="${TASK5_START_TIMEOUT_SECONDS:-30}"
GRACEFUL_SHUTDOWN_TIMEOUT="${TASK5_GRACEFUL_SHUTDOWN_SECONDS:-5}"

mkdir -p -- "$RUNTIME_DIR/archive" "$RUNTIME_DIR/ros" "$LOG_DIR/ros"

archive_pid_file() {
  local stamp destination
  [[ -e "$PID_FILE" ]] || return 0
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  destination="$RUNTIME_DIR/archive/task5.pid.$stamp.$$"
  mv -- "$PID_FILE" "$destination"
}

is_managed_pid() {
  local pid="$1"
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$APP"
}

if [[ -e "$PID_FILE" ]]; then
  pid="$(tr -d '[:space:]' < "$PID_FILE")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    if is_managed_pid "$pid"; then
      echo "Task5 v1 already running: PID=$pid"
      echo "http://$PUBLIC_HOST:$PORT/"
      exit 0
    fi
    archive_pid_file
    echo "Refusing unmanaged live PID from stale Task5 pidfile: $pid" >&2
    exit 1
  fi
  archive_pid_file
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "Task5 Python is not executable: $PYTHON" >&2
  exit 1
fi

if [[ ! -f "$ROS_SETUP" ]]; then
  echo "Task5 ROS setup is missing: $ROS_SETUP" >&2
  exit 1
fi
# The Cobot Noetic bindings are built for Python 3.8 and are exported here.
# Catkin setup scripts legitimately inspect unset variables, so suspend
# nounset only for this trusted workspace setup and restore it immediately.
set +u
# shellcheck disable=SC1090
source "$ROS_SETUP"
set -u

if ! "$PYTHON" - "$HOST" "$PORT" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
try:
    with socket.create_connection((probe_host, port), timeout=0.2):
        raise SystemExit(1)
except OSError:
    raise SystemExit(0)
PY
then
  echo "Port $HOST:$PORT is already in use; no process was stopped." >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="$LOG_DIR/task5-$stamp.log"
cd -- "$PROJECT_DIR"
nohup env \
  PYTHONPATH="$V1_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  ROS_HOME="$RUNTIME_DIR/ros" \
  ROS_LOG_DIR="$LOG_DIR/ros" \
  "$PYTHON" -m uvicorn "$APP" --host "$HOST" --port "$PORT" \
  --timeout-graceful-shutdown "$GRACEFUL_SHUTDOWN_TIMEOUT" \
  >>"$log_file" 2>&1 &
pid=$!
printf '%s\n' "$pid" > "$PID_FILE.tmp"
mv -- "$PID_FILE.tmp" "$PID_FILE"

deadline=$((SECONDS + START_TIMEOUT))
while (( SECONDS < deadline )); do
  if ! kill -0 "$pid" 2>/dev/null; then
    archive_pid_file
    echo "Task5 v1 exited before readiness. Log: $log_file" >&2
    tail -n 40 -- "$log_file" >&2 || true
    exit 1
  fi
  if "$PYTHON" - "$PORT" <<'PY'
import json
import sys
import urllib.request

try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{sys.argv[1]}/healthz", timeout=0.5) as response:
        payload = json.load(response)
except Exception:
    raise SystemExit(1)
if payload.get("status") not in {"ok", "not_ready"}:
    raise SystemExit(1)
PY
  then
    echo "Task5 v1 PID: $pid"
    echo "Task5 v1 log: $log_file"
    echo "http://$PUBLIC_HOST:$PORT/"
    exit 0
  fi
  sleep 0.2
done

if is_managed_pid "$pid"; then
  kill -TERM "$pid" 2>/dev/null || true
fi
archive_pid_file
echo "Task5 v1 readiness timeout. Log: $log_file" >&2
exit 1

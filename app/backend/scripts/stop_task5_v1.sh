#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
V1_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RUNTIME_DIR="${TASK5_RUNTIME_DIR:-$V1_DIR/runtime}"
PID_FILE="$RUNTIME_DIR/task5.pid"
APP="capture_core.api:app"
STOP_TIMEOUT="${TASK5_STOP_TIMEOUT_SECONDS:-15}"

mkdir -p -- "$RUNTIME_DIR/archive"
archive_pid_file() {
  [[ -e "$PID_FILE" ]] || return 0
  local stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mv -- "$PID_FILE" "$RUNTIME_DIR/archive/task5.pid.$stamp.$$"
}

process_running() {
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/stat" ]] || return 1
  [[ "$(awk '{print $3}' "/proc/$pid/stat")" != "Z" ]]
}

if [[ ! -r "$PID_FILE" ]]; then
  echo "Task5 v1 is not running"
  exit 0
fi
pid="$(tr -d '[:space:]' < "$PID_FILE")"
if [[ ! "$pid" =~ ^[0-9]+$ ]] || [[ ! -r "/proc/$pid/cmdline" ]]; then
  archive_pid_file
  echo "Archived stale Task5 pidfile"
  exit 0
fi
if ! tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq -- "$APP"; then
  archive_pid_file
  echo "PID $pid is not the Task5 API; pidfile archived and no signal sent." >&2
  exit 1
fi

kill -TERM "$pid"
deadline=$((SECONDS + STOP_TIMEOUT))
while process_running; do
  if (( SECONDS >= deadline )); then
    echo "Task5 v1 did not stop after ${STOP_TIMEOUT}s; SIGKILL was not sent." >&2
    exit 1
  fi
  sleep 0.1
done
archive_pid_file
echo "Task5 v1 stopped: PID=$pid"

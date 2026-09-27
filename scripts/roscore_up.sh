#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$COBOT_RUNTIME_ROOT/roscore"
mkdir -p "$RUNTIME"
set +u
source "$TASK5_ROS_SETUP"
set -u

if rosnode list >/dev/null 2>&1; then
  echo "ROS Core 已就绪。"
  exit 0
fi

exec 9>"$RUNTIME/start.lock"
flock -x 9
if rosnode list >/dev/null 2>&1; then
  echo "ROS Core 已就绪。"
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
log="$RUNTIME/roscore-$stamp.log"
setsid /opt/ros/noetic/bin/roscore -p 11311 </dev/null >>"$log" 2>&1 &
pid=$!
printf '%s\n' "$pid" > "$RUNTIME/pid"

for _ in $(seq 1 80); do
  if rosnode list >/dev/null 2>&1; then
    echo "ROS Core 已启动：PID=$pid"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "ROS Core 启动失败，请查看 $log" >&2
    exit 1
  fi
  sleep 0.1
done

kill -INT "-$pid" 2>/dev/null || true
echo "ROS Core 启动超时，请查看 $log" >&2
exit 1

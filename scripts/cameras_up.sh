#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
set +u
source "$TASK5_ROS_SETUP"
set -u
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/roscore_up.sh"
if rosnode list 2>/dev/null | grep -Eq '^/camera_(f|l|r)/camera$'; then
  echo '三相机节点已存在；请先使用其原启动终端停止，避免重复占用 USB。' >&2
  exit 1
fi
export ROS_LOG_DIR="$COBOT_RUNTIME_ROOT/cameras/logs"
mkdir -p "$ROS_LOG_DIR"
exec roslaunch "$COBOT_PLATFORM_ROOT/integrations/legacy_control/launch/multi_camera_shuai.launch"

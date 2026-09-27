#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ARM_ARGS=("$@")
set --

# The web console runs in cobot-station, while every Piper driver imports
# piper_sdk from the registered aloha environment. Make the launch independent
# of whichever environment started the web service.
set +u
source /home/agilex/miniconda3/etc/profile.d/conda.sh
conda activate aloha
source "$TASK5_ROS_SETUP"
set -u
hash -r

if ! python3 -c 'import piper_sdk' >/dev/null 2>&1; then
  echo "机械臂启动环境错误：aloha 中无法导入 piper_sdk；未启动任何 CAN owner。" >&2
  exit 1
fi
"$ROOT/scripts/roscore_up.sh"
if rosnode list 2>/dev/null | grep -Eq '^/(piper_rear_(left|right)_teach_task2|task2_teach_handover)$'; then
  echo "机械臂节点已运行。若状态不是 3/3，请先点网页右侧红色停止，等待变灰后再启动。" >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
export ROS_PACKAGE_PATH="$ROOT/robot:${ROS_PACKAGE_PATH:-}"
export ROS_LOG_DIR="$COBOT_RUNTIME_ROOT/arms/logs"
mkdir -p "$ROS_LOG_DIR"
exec roslaunch "$ROOT/robot/arms/arms.launch" "${ARM_ARGS[@]}"

#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TELEOP_ARGS=("$@")
set --
set +u
source "$TASK5_ROS_SETUP"
set -u
export PYTHONDONTWRITEBYTECODE=1
exec /home/agilex/miniconda3/envs/aloha/bin/python "$ROOT/robot/teleop.py" "${TELEOP_ARGS[@]}"

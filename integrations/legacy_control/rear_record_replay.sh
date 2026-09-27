#!/usr/bin/env bash
# One terminal; the Python supervisor owns only the roslaunch it starts.
set -eo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPER_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

unset VIRTUAL_ENV
source /home/agilex/miniconda3/etc/profile.d/conda.sh
conda activate aloha
export PATH="$CONDA_PREFIX/bin:$PATH"
hash -r
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
cd "$PIPER_ROOT"
exec python3 -u data_collect/rear_record_replay.py "$@"

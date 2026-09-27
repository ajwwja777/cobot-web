#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
export COBOT_READ_ONLY=1
export COBOT_DATA_UI_PORT="${COBOT_DATA_UI_PORT:-8018}"
export COBOT_RUNTIME_ROOT="$COBOT_RUNTIME_ROOT/web-preview"
export COBOT_DATA_UI_RUNTIME_DIR="$COBOT_RUNTIME_ROOT/data-console"
export COBOT_CONSOLE_JOB_RUNTIME="$COBOT_RUNTIME_ROOT/console-jobs"
# A preview never records into, resolves or initializes the live rollout directory.
export COBOT_RLT_TASK5_DATA_ROOT="$COBOT_RUNTIME_ROOT/data/rlt"
export TASK5_SEGMENTED_DATA_ROOT="$COBOT_RUNTIME_ROOT/data/raw"
export TASK5_SEGMENTED_ALLOWED_DATA_ROOT="$COBOT_RUNTIME_ROOT/data"
export COBOT_ADDITIONAL_DATA_ROOTS="[]"
exec python3 "$COBOT_PLATFORM_ROOT/scripts/workflow.py" "${1:-ui-up}"

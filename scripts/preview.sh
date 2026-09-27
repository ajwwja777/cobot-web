#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
export COBOT_READ_ONLY=1
export COBOT_DATA_UI_PORT="${COBOT_DATA_UI_PORT:-8018}"
export COBOT_RUNTIME_ROOT="$COBOT_RUNTIME_ROOT/web-preview"
export COBOT_DATA_UI_RUNTIME_DIR="$COBOT_RUNTIME_ROOT/data-console"
export COBOT_CONSOLE_JOB_RUNTIME="$COBOT_RUNTIME_ROOT/console-jobs"
exec python3 "$COBOT_PLATFORM_ROOT/scripts/workflow.py" "${1:-ui-up}"

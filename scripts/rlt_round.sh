#!/usr/bin/env bash
# Round-based offline updates (replaces the five-episode online cycle): status | critic | train | compare A B | switch RELEASE
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
set -Eeuo pipefail
ROOT="$COBOT_RLT_PROJECT_ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
cd "$ROOT"
exec /usr/bin/python3 -m methods.openpi_rlt.plug_v2.rtc_round "$@"

#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
exec "$COBOT_CONTROL_PROJECT_ROOT/scripts/cameras_up.sh" "$@"

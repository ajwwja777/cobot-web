#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
exec bash "$COBOT_PLATFORM_ROOT/integrations/legacy_control/can_config_cobot.sh" task2

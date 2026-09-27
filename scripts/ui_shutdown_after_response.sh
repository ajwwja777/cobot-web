#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Give the API response time to reach the browser before stopping this server.
sleep 1
exec "$ROOT/ui_down.sh"

#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
PLATFORM="$COBOT_PLATFORM_ROOT"
export NO_PROXY=127.0.0.1,localhost
export no_proxy="$NO_PROXY"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
set +u
source "$TASK5_ROS_SETUP"
set -u
case "${1:-}" in
  plug-v3-reference|plug-v3-warmup-5k)
    export COBOT_RLT_EVALUATION=1
    export COBOT_DEPLOYMENT_MODEL_ID="$1"
    export COBOT_EVAL_SNAPSHOT="${2:?snapshot required}"
    export COBOT_EVAL_CONFIG="${3:?configuration required}"
    if [[ "$1" == plug-v3-reference ]]; then mode=reference; else mode=frozen; fi
    exec "$PLATFORM/scripts/rlt_v3_up.sh" "$mode"
    ;;
  pi05-in-the-pot|pi05-in-the-pot-dagger)
    export PI05_RUNTIME_STATE_ROOT="$COBOT_RUNTIME_ROOT/deployment/pi05"
    export MAX_PUBLISH_STEP=100000000
    exec "$PLATFORM/scripts/deployment_pi05.sh" "$1"
    ;;
  *) echo '未登记的部署模型' >&2; exit 2;;
esac

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
MODEL_ENTRY="${COBOT_DEPLOYMENT_ADAPTER:-${1:-}}"
case "$MODEL_ENTRY" in
  plug-v3-reference|plug-v3-warmup-5k|plug_v3-frozen-latest|plug_v3-online-latest)
    unset COBOT_RLT_EVALUATION
    export COBOT_RLT_SHARED_MODEL=1
    export COBOT_DEPLOYMENT_MODEL_ID="$1"
    export COBOT_EVAL_SNAPSHOT="${2:?snapshot required}"
    export COBOT_EVAL_CONFIG="${3:?configuration required}"
    if [[ "$MODEL_ENTRY" == plug-v3-reference ]]; then mode=reference
    elif [[ "$MODEL_ENTRY" == plug_v3-online-latest ]]; then mode=online
    else mode=frozen; fi
    exec "$PLATFORM/scripts/rlt_v3_up.sh" "$mode"
    ;;
  pi05-in-the-pot|pi05-in-the-pot-dagger)
    export PI05_RUNTIME_STATE_ROOT="$COBOT_RUNTIME_ROOT/deployment/pi05"
    export MAX_PUBLISH_STEP=100000000
    exec "$PLATFORM/scripts/deployment_pi05.sh" "$MODEL_ENTRY"
    ;;
  fluxvla-pi05-*|galaxea-g05-*|xr1-*|xr1_dagger-*)
    exec /usr/bin/python3 "${COBOT_PLATFORM_ROOT%/*}/vla-platform/integrations/cobot/managed_model.py" "$MODEL_ENTRY"
    ;;
  *) echo '未登记的部署模型' >&2; exit 2;;
esac

#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
PLATFORM="$COBOT_PLATFORM_ROOT"
if [[ "${1:-}" == pi05-in-the-pot-dagger ]]; then
  ROOT="$COBOT_PI05_DAGGER_ROOT"
  STEP=3000
  export POLICY_CONFIG=pi05_jiaan_task5_in_the_pot_dagger_round001
  export POLICY_ASSET_ID=task5_cobot_in_the_pot_round_001
elif [[ "${1:-}" == pi05-in-the-pot ]]; then
  ROOT="$COBOT_PI05_ROOT"
  STEP=2000
  export POLICY_CONFIG=pi05_wja_cobot_in_the_pot
  export POLICY_ASSET_ID=wja/cobot_in_the_pot_40episodes
else
  echo '未登记的 π0.5 模型' >&2; exit 2
fi
# Use the historical package's own preflight without modifying it.
PI05_RTC_DRY_RUN=1 "$ROOT/run_checkpoint_rtc_task2.sh" "$STEP" live
export PI05_RUNTIME_ROOT=/home/agilex/junfeng/workspace/pi05_cobot
if [[ "${COBOT_DEPLOY_DRY_RUN:-0}" == 1 ]]; then
  export PI05_RTC_RUNTIME_DRY_RUN=1
else
  "$PI05_RUNTIME_ROOT/.venv-server/bin/python" "$ROOT/common/runtime/install_openpi_task_config.py" "$PI05_RUNTIME_ROOT/openpi/src/openpi/training/config.py"
fi
export CHECKPOINT_DIR="$ROOT/checkpoints/step_$STEP"
export PROMPT='Open the pot lid, put the object into the pot, then close the lid.'
export RIGHT_GRIPPER_THRESHOLD=0.02 RIGHT_GRIPPER_MODE=continuous MIN_EXECUTION_HORIZON=25
export PUPPET_ARM_LEFT_CMD_TOPIC=/task2/policy/joint_left
export PUPPET_ARM_RIGHT_CMD_TOPIC=/task2/policy/joint_right
export SHADOW_MODE=false USE_INIT_POSE=false
export COBOT_PI05_CLIENT_ROOT="$ROOT/common/robot"
export COBOT_PI05_GATE_STATE="$COBOT_RUNTIME_ROOT/deployment/pi05-gate.json"
export PI05_RTC_CLIENT_SCRIPT="$PLATFORM/app/backend/cobot_console/deployment_pi05_client.py"
exec "$ROOT/common/inference_pi05_rtc.sh"

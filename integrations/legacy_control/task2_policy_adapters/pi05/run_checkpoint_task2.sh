#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$#" -ne 1 ]]; then
  printf 'Usage: %s <positive checkpoint step>\n' "$0" >&2
  exit 2
fi
readonly STEP="$1"
if [[ ! "${STEP}" =~ ^[1-9][0-9]*$ ]]; then
  printf 'Checkpoint step must be a positive integer: %s\n' "${STEP}" >&2
  exit 2
fi

readonly CHECKPOINT_DIR="${ROOT}/checkpoints/step_${STEP}"
readonly COMMON_DIR="${ROOT}/common"
readonly ASSET_ID="wja/cobot_in_the_pot_40episodes"
for required in \
  "${CHECKPOINT_DIR}/params/_METADATA" \
  "${CHECKPOINT_DIR}/_CHECKPOINT_METADATA" \
  "${CHECKPOINT_DIR}/assets/${ASSET_ID}/norm_stats.json" \
  "${COMMON_DIR}/inference_pi05_task2.sh" \
  "${COMMON_DIR}/robot/inference_pi05_task2.py" \
  "${COMMON_DIR}/runtime/install_openpi_task_config.py"; do
  if [[ ! -e "${required}" ]]; then
    printf 'Incomplete Pi0.5 Task2 step_%s; missing %s\n' \
      "${STEP}" "${required}" >&2
    exit 1
  fi
done

export CHECKPOINT_DIR
export POLICY_CONFIG="pi05_wja_cobot_in_the_pot"
export PROMPT="Open the pot lid, put the object into the pot, then close the lid."
export POLICY_ASSET_ID="${ASSET_ID}"
export RIGHT_GRIPPER_THRESHOLD="${RIGHT_GRIPPER_THRESHOLD:-0.02}"
export RIGHT_GRIPPER_MODE="${RIGHT_GRIPPER_MODE:-continuous}"
export PI05_RUNTIME_ROOT="${PI05_RUNTIME_ROOT:-/home/agilex/junfeng/workspace/pi05_cobot}"
readonly OPENPI_CONFIG="${PI05_OPENPI_CONFIG:-${PI05_RUNTIME_ROOT}/openpi/src/openpi/training/config.py}"
"${PI05_RUNTIME_ROOT}/.venv-server/bin/python" \
  "${COMMON_DIR}/runtime/install_openpi_task_config.py" \
  "${OPENPI_CONFIG}"

export AUTO_CONTINUE=true
export EXECUTE_STEPS="${EXECUTE_STEPS:-25}"
export SHADOW_MODE=false
export USE_INIT_POSE=false
export PUPPET_ARM_LEFT_CMD_TOPIC="/task2/policy/joint_left"
export PUPPET_ARM_RIGHT_CMD_TOPIC="/task2/policy/joint_right"
printf '%s\n' 'TASK2 LIVE: policy starts paused; M=manual, S=policy, Q=quit keyboard.'
exec "${COMMON_DIR}/inference_pi05_task2.sh"

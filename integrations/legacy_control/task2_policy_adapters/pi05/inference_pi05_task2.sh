#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PI05_RUNTIME_ROOT="${PI05_RUNTIME_ROOT:?PI05_RUNTIME_ROOT is required}"
PI05_RUNTIME_STATE_ROOT="${PI05_RUNTIME_STATE_ROOT:-${PI05_RUNTIME_ROOT}/state}"
OPENPI_ROOT="${OPENPI_ROOT:-${PI05_RUNTIME_ROOT}/openpi}"
SERVER_PYTHON="${SERVER_PYTHON:-${PI05_RUNTIME_ROOT}/.venv-server/bin/python}"
CLIENT_PYTHON="${CLIENT_PYTHON:-${PI05_RUNTIME_ROOT}/.venv-client/bin/python}"
CHECKPOINT_DIR="${CHECKPOINT_DIR:-${ROOT}}"

PROMPT="${PROMPT:-Open the pot lid, put the object into the pot, then close the lid.}"
POLICY_CONFIG="${POLICY_CONFIG:-pi05_wja_cobot_in_the_pot}"
POLICY_ASSET_ID="${POLICY_ASSET_ID:-wja/cobot_in_the_pot_40episodes}"
POLICY_HOST="${POLICY_HOST:-127.0.0.1}"
POLICY_PORT="${POLICY_PORT:-8000}"
POLICY_START_TIMEOUT="${POLICY_START_TIMEOUT:-600}"
PUBLISH_RATE="${PUBLISH_RATE:-20}"
MAX_PUBLISH_STEP="${MAX_PUBLISH_STEP:-10000}"
EXECUTE_STEPS="${EXECUTE_STEPS:-25}"
AUTO_CONTINUE="${AUTO_CONTINUE:-true}"
USE_INIT_POSE=false
SHADOW_MODE="${SHADOW_MODE:-false}"
MAX_SYNC_SKEW="${MAX_SYNC_SKEW:-0.1}"

RIGHT_GRIPPER_THRESHOLD="${RIGHT_GRIPPER_THRESHOLD:-0.02}"
RIGHT_GRIPPER_CLOSED="${RIGHT_GRIPPER_CLOSED:-0.0}"
RIGHT_GRIPPER_OPEN="${RIGHT_GRIPPER_OPEN:-0.09}"
RIGHT_GRIPPER_MODE="${RIGHT_GRIPPER_MODE:-continuous}"
ARM_STEPS_LENGTH="${ARM_STEPS_LENGTH:-0.01,0.01,0.01,0.01,0.01,0.01,0.2}"

IMG_FRONT_TOPIC="${IMG_FRONT_TOPIC:-/camera_f/color/image_raw}"
IMG_LEFT_TOPIC="${IMG_LEFT_TOPIC:-/camera_l/color/image_raw}"
IMG_RIGHT_TOPIC="${IMG_RIGHT_TOPIC:-/camera_r/color/image_raw}"
PUPPET_ARM_LEFT_TOPIC="${PUPPET_ARM_LEFT_TOPIC:-/puppet/joint_left}"
PUPPET_ARM_RIGHT_TOPIC="${PUPPET_ARM_RIGHT_TOPIC:-/puppet/joint_right}"
# These topics are fixed safety boundaries and are intentionally not overridable.
PUPPET_ARM_LEFT_CMD_TOPIC="/task2/policy/joint_left"
PUPPET_ARM_RIGHT_CMD_TOPIC="/task2/policy/joint_right"

export OPENPI_DATA_HOME="${OPENPI_DATA_HOME:-${PI05_RUNTIME_ROOT}/openpi_data}"
export HF_HOME="${HF_HOME:-${PI05_RUNTIME_STATE_ROOT}/cache/huggingface}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${PI05_RUNTIME_STATE_ROOT}/cache/xdg}"
export JAX_COMPILATION_CACHE_DIR="${JAX_COMPILATION_CACHE_DIR:-${PI05_RUNTIME_STATE_ROOT}/cache/jax}"
export XLA_PYTHON_CLIENT_PREALLOCATE="${XLA_PYTHON_CLIENT_PREALLOCATE:-false}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

for required_path in \
  "${SERVER_PYTHON}" \
  "${CLIENT_PYTHON}" \
  "${OPENPI_ROOT}/scripts/serve_policy.py" \
  "${ROOT}/robot/inference_pi05.py" \
  "${ROOT}/robot/inference_pi05_task2.py" \
  "${CHECKPOINT_DIR}/params" \
  "${CHECKPOINT_DIR}/_CHECKPOINT_METADATA" \
  "${CHECKPOINT_DIR}/assets/${POLICY_ASSET_ID}/norm_stats.json" \
  "${OPENPI_DATA_HOME}/big_vision/paligemma_tokenizer.model"; do
  if [[ ! -e "${required_path}" ]]; then
    printf 'Missing required Task2 deployment path: %s\n' "${required_path}" >&2
    exit 1
  fi
done

mkdir -p \
  "${PI05_RUNTIME_STATE_ROOT}/logs" \
  "${JAX_COMPILATION_CACHE_DIR}" \
  "${HF_HOME}" \
  "${XDG_CACHE_HOME}"

if ss -ltnH | awk '{print $4}' | grep -Eq "(:|])${POLICY_PORT}$"; then
  printf 'Policy port is already in use: %s:%s\n' "${POLICY_HOST}" "${POLICY_PORT}" >&2
  exit 1
fi

timestamp="$(date +%Y%m%d_%H%M%S)"
server_log="${PI05_RUNTIME_STATE_ROOT}/logs/policy_server_task2_${timestamp}.log"
server_pid=""
client_pid=""

cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [[ -n "${client_pid}" ]] && kill -0 "${client_pid}" 2>/dev/null; then
    kill "${client_pid}" 2>/dev/null || true
    wait "${client_pid}" 2>/dev/null || true
  fi
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
  fi
  exit "${status}"
}
trap cleanup EXIT INT TERM

(
  cd "${OPENPI_ROOT}"
  export PYTHONPATH=""
  exec "${SERVER_PYTHON}" scripts/serve_policy.py \
    --port "${POLICY_PORT}" \
    --default_prompt "${PROMPT}" \
    policy:checkpoint \
    --policy.config "${POLICY_CONFIG}" \
    --policy.dir "${CHECKPOINT_DIR}"
) >"${server_log}" 2>&1 &
server_pid=$!

printf 'Pi0.5 Task2 policy server PID: %s\n' "${server_pid}"
printf 'Pi0.5 Task2 policy server log: %s\n' "${server_log}"

deadline=$((SECONDS + POLICY_START_TIMEOUT))
while true; do
  if ss -ltnH | awk '{print $4}' | grep -Eq "(:|])${POLICY_PORT}$"; then
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    wait "${server_pid}" || server_status=$?
    printf 'Policy server exited before listening (status %s).\n' \
      "${server_status:-0}" >&2
    tail -n 80 "${server_log}" >&2 || true
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    printf 'Timed out waiting for policy server on %s:%s.\n' \
      "${POLICY_HOST}" "${POLICY_PORT}" >&2
    tail -n 80 "${server_log}" >&2 || true
    exit 1
  fi
  sleep 1
done

set +u
source /opt/ros/noetic/setup.bash
source /home/agilex/Desktop/agilex_ws/devel/setup.bash
source /home/agilex/cobot_magic/camera_ws/devel/setup.bash
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
set -u

"${CLIENT_PYTHON}" "${ROOT}/robot/inference_pi05_task2.py" \
  --host "${POLICY_HOST}" \
  --port "${POLICY_PORT}" \
  --prompt "${PROMPT}" \
  --publish-rate "${PUBLISH_RATE}" \
  --max-publish-step "${MAX_PUBLISH_STEP}" \
  --execute-steps "${EXECUTE_STEPS}" \
  --auto-continue "${AUTO_CONTINUE}" \
  --use-init-pose "${USE_INIT_POSE}" \
  --shadow-mode "${SHADOW_MODE}" \
  --max-sync-skew "${MAX_SYNC_SKEW}" \
  --right-gripper-threshold "${RIGHT_GRIPPER_THRESHOLD}" \
  --right-gripper-closed "${RIGHT_GRIPPER_CLOSED}" \
  --right-gripper-open "${RIGHT_GRIPPER_OPEN}" \
  --right-gripper-mode "${RIGHT_GRIPPER_MODE}" \
  --arm-steps-length "${ARM_STEPS_LENGTH}" \
  --img-front-topic "${IMG_FRONT_TOPIC}" \
  --img-left-topic "${IMG_LEFT_TOPIC}" \
  --img-right-topic "${IMG_RIGHT_TOPIC}" \
  --puppet-arm-left-topic "${PUPPET_ARM_LEFT_TOPIC}" \
  --puppet-arm-right-topic "${PUPPET_ARM_RIGHT_TOPIC}" \
  --puppet-arm-left-cmd-topic "${PUPPET_ARM_LEFT_CMD_TOPIC}" \
  --puppet-arm-right-cmd-topic "${PUPPET_ARM_RIGHT_CMD_TOPIC}" &
client_pid=$!
set +e
wait "${client_pid}"
client_status=$?
set -e
client_pid=""
exit "${client_status}"

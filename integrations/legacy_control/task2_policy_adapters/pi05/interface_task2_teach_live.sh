#!/usr/bin/env bash
# Start the paused pi0.5 Task2 policy, arm the rear arms through the
# physical-teach coordinator, then hand the terminal to the M/S keyboard.
#
# The policy side is unchanged from the dynamic-role variant: it still
# publishes /task2/policy/joint_{left,right} and serves /task2/policy/set_paused.
# Only the coordinator services differ.
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

for service in \
  /task2/teach_handover/request_manual \
  /task2/teach_handover/request_policy \
  /task2/teach_handover/reset_fault; do
  if ! rosservice info "${service}" >/dev/null 2>&1; then
    printf 'Required Task2 teach coordinator service is missing: %s\n' "${service}" >&2
    exit 1
  fi
done

readonly RUNNER="${TASK2_RUNNER:-${ROOT}/run_checkpoint_task2.sh}"
readonly POLICY_SERVICE_TIMEOUT="${TASK2_POLICY_SERVICE_TIMEOUT:-120}"
policy_pid=""

cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [[ -n "${policy_pid}" ]] && kill -0 "${policy_pid}" 2>/dev/null; then
    kill "${policy_pid}" 2>/dev/null || true
    wait "${policy_pid}" 2>/dev/null || true
  fi
  exit "${status}"
}
trap cleanup EXIT INT TERM

"${RUNNER}" "${STEP}" &
policy_pid=$!
sleep 0.1

deadline=$((SECONDS + POLICY_SERVICE_TIMEOUT))
while ! rosservice info /task2/policy/set_paused >/dev/null 2>&1; do
  if ! kill -0 "${policy_pid}" 2>/dev/null; then
    wait "${policy_pid}" || child_status=$?
    printf 'Task2 policy child exited before pause service (status %s).\n' \
      "${child_status:-0}" >&2
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    printf 'Timed out waiting for /task2/policy/set_paused.\n' >&2
    exit 1
  fi
  sleep 0.2
done

printf '%s\n' '正在武装后双臂：不要按住任何示教按钮。'
if ! resume_output="$(rosservice call /task2/teach_handover/request_policy)"; then
  printf 'Task2 teach coordinator request_policy call failed.\n' >&2
  exit 1
fi
printf '%s\n' "${resume_output}"
if ! grep -Eq 'success:[[:space:]]*(True|true)' <<<"${resume_output}"; then
  printf 'Task2 teach coordinator refused POLICY mode.\n' >&2
  exit 1
fi

printf '%s\n' 'M=请求接管(随后按下左右后臂示教按钮)，S=恢复模型(再按一次按钮)，Q=只退出键盘。'
printf '%s\n' '扶住五臂并保持急停可及。'
rosrun piper task2_teach_handover_keyboard.py

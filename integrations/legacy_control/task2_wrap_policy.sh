#!/usr/bin/env bash
# Run ANY existing policy deployment script under Task2 takeover, unmodified.
#
#   task2_wrap_policy.sh <interface script> [args...]
#
# e.g.  task2_wrap_policy.sh .../pi05/interface_live.sh 2000
#       task2_wrap_policy.sh .../some_future_model/interface.sh --whatever
#
# How it works: the wrapper starts the generic policy gate, points the policy's
# command topics at the gate's inputs, and lets the takeover coordinator drive
# the gate through /task2/policy/set_paused.  The policy itself is untouched.
#
# LIMITATION, and it is not small.  The gate is outside the policy process, so
# it cannot clear the policy's action chunk or re-anchor the policy's own step
# limiter.  A chunked policy resumes by replaying actions planned for the world
# as it was before the operator intervened.  The gate ramps that jump away from
# the arm's measured position so it arrives smoothly, but the actions are still
# stale.  For anything beyond a demo, integrate properly the way
# common/robot/inference_pi05_rtc_task2.py does - see
# multi_arm_launch_tools/docs/2026-08-07-task2-deployment-profiles.md.
#
# The two topic environment variables below are the pi0.5 deployment
# convention.  A model that names its command topics differently needs its own
# override here; that is the only per-model work this wrapper implies.
set -euo pipefail

if [[ "$#" -lt 1 ]]; then
  printf 'Usage: %s <interface script> [args...]\n' "$0" >&2
  exit 2
fi
readonly INTERFACE="$1"
shift
if [[ ! -x "${INTERFACE}" ]]; then
  printf '不是可执行文件: %s\n' "${INTERFACE}" >&2
  exit 2
fi

if ! rosservice info /task2/teach_handover/reset_fault >/dev/null 2>&1; then
  printf 'Task2 按钮协调器没有运行，先起 launch:\n' >&2
  printf '  roslaunch .../start_ms_piper_5arm_button_teach_task2.launch\n' >&2
  exit 1
fi

# A policy that serves the pause service itself is already integrated; wrapping
# it would put two providers on one service name and the coordinator would talk
# to whichever registered last.
if rosservice info /task2/policy/set_paused >/dev/null 2>&1; then
  printf '/task2/policy/set_paused 已经被占用。\n' >&2
  printf '如果这是深度集成的策略(例如 interface_task2_teach_rtc_live.sh)，直接运行它，不要用本包装脚本。\n' >&2
  printf '如果是残留进程，先结束它。\n' >&2
  exit 1
fi

gate_pid=""
policy_pid=""

cleanup() {
  status=$?
  trap - EXIT INT TERM
  for pid in "${policy_pid}" "${gate_pid}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  done
  exit "${status}"
}
trap cleanup EXIT INT TERM

rosrun piper task2_policy_gate_node.py &
gate_pid=$!

deadline=$((SECONDS + 30))
while ! rosservice info /task2/policy/set_paused >/dev/null 2>&1; do
  if ! kill -0 "${gate_pid}" 2>/dev/null; then
    printf '策略闸门启动失败。\n' >&2
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    printf '等待策略闸门超时。\n' >&2
    exit 1
  fi
  sleep 0.5
done
printf '策略闸门已就绪（当前暂停），正在启动策略: %s\n' "${INTERFACE}"

export PUPPET_ARM_LEFT_CMD_TOPIC="/task2/policy_raw/joint_left"
export PUPPET_ARM_RIGHT_CMD_TOPIC="/task2/policy_raw/joint_right"

"${INTERFACE}" "$@" &
policy_pid=$!

printf '\n%s\n' '============ 策略已启动，闸门处于暂停 ============'
printf '%s\n' '后臂是失能的（软的），可以徒手搬到顺手的位置。'
printf '%s\n' '按下任一后臂示教按钮即可随时接管，再按一下交还。'
printf '%s\n' '注意：本包装脚本无法清空策略内部的动作块，交还后策略会'
printf '%s\n' '      接着执行接管前算好的动作（闸门会把跳变限速，但动作是旧的）。'
printf '%s\n' '================================================='
read -r -p '确认无误后按 Enter 开始推理（Ctrl-C 放弃）: ' _

rosservice call /task2/policy/set_paused false
printf '%s\n' '推理已开始。Ctrl-C 结束。'
wait "${policy_pid}"

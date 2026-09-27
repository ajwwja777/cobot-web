#!/usr/bin/env bash
# ===========================================================================
# Task2 部署脚本模板 —— 复制这个文件，改下面三个变量，就完成了一个新模型的部署入口。
#
# 前提：你的策略进程满足接口契约的 6 条要求。契约全文见
#   multi_arm_launch_tools/docs/2026-08-07-task2-integration-contract.md §4
# 最关键的三条：
#   · 指令发到 /task2/policy/joint_{left,right}，不要直接发 /master/joint_*
#   · 提供 /task2/policy/set_paused (std_srvs/SetBool)，并且启动后处于暂停
#   · 恢复时丢弃已排队的动作，并把步长限幅锚定在实测关节位置
#
# 改完之后用自检脚本验证，不需要找人：
#   multi_arm_launch_tools/task2_policy_contract_check.sh
#
# 如果你的策略【没有】做契约集成，不要用这个模板，用
#   multi_arm_launch_tools/task2_wrap_policy.sh <你的 interface 脚本>
# 它会插一个通用闸门代替策略提供 set_paused（代价见契约 §4.4）。
# ===========================================================================
set -euo pipefail

# --------------------------- 改这里 ---------------------------------------
POLICY_NAME="CHANGE_ME"                       # 显示用的名字
POLICY_RUNNER="/abs/path/to/run_checkpoint_xxx.sh"   # 真正起策略的脚本
# 传给 POLICY_RUNNER 的参数。默认把本脚本收到的参数原样转发。
POLICY_RUNNER_ARGS=("$@")
# 策略从启动到注册 set_paused 的最长等待。冷启动要加载 checkpoint 和预热采样器，
# 通常几分钟，给宽一点没有坏处。
POLICY_SERVICE_TIMEOUT="${POLICY_SERVICE_TIMEOUT:-900}"
# --------------------------------------------------------------------------

if [[ "${POLICY_NAME}" == "CHANGE_ME" ]]; then
  printf '请先编辑本脚本顶部的 POLICY_NAME / POLICY_RUNNER。\n' >&2
  exit 2
fi
if [[ ! -x "${POLICY_RUNNER}" ]]; then
  printf 'POLICY_RUNNER 不是可执行文件: %s\n' "${POLICY_RUNNER}" >&2
  exit 2
fi

# 协调器必须已经在跑：它是前臂唯一的指令源，没有它策略发的东西没人接。
if ! rosservice info /task2/teach_handover/reset_fault >/dev/null 2>&1; then
  printf 'Task2 按钮协调器没有运行。先起 launch:\n' >&2
  printf '  roslaunch .../start_ms_piper_5arm_button_teach_task2.launch \\\n' >&2
  printf '    front_mode:=1 front_auto_enable:=true mid_auto_enable:=true rear_auto_enable:=true\n' >&2
  exit 1
fi
for topic in /task2/teach/rear_left/teach_active /task2/teach/rear_right/teach_active; do
  if ! rostopic info "${topic}" >/dev/null 2>&1; then
    printf '后臂示教驱动的话题缺失: %s\n' "${topic}" >&2
    exit 1
  fi
done

# 协调器 latch 了 fault 的话，接管和推理转发都会停摆。启动前清掉，
# 否则要等模型加载完几分钟才发现按钮没反应。
coordinator_mode="$(rostopic echo -n 1 /task2/teach_handover/mode 2>/dev/null \
  | sed -n 's/^data: "\(.*\)"$/\1/p' | head -1)"
if [[ "${coordinator_mode}" == fault ]]; then
  coordinator_fault="$(rostopic echo -n 1 /task2/teach_handover/fault 2>/dev/null \
    | sed -n 's/^data: "\(.*\)"$/\1/p' | head -1)"
  printf '协调器处于 fault，正在清除：%s\n' "${coordinator_fault}" >&2
  if ! rosservice call /task2/teach_handover/reset_fault >/dev/null; then
    printf '无法清除协调器 fault，请检查后臂驱动。\n' >&2
    exit 1
  fi
  printf '已清除。\n' >&2
fi

# 两个 set_paused 提供者会让协调器连到后注册的那个，行为不可预测。
if rosservice info /task2/policy/set_paused >/dev/null 2>&1; then
  printf '/task2/policy/set_paused 已经被占用，可能是上一次的残留进程或验收桩。\n' >&2
  printf '先结束它再重试。\n' >&2
  exit 1
fi

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

printf '正在启动策略: %s\n' "${POLICY_NAME}"
"${POLICY_RUNNER}" "${POLICY_RUNNER_ARGS[@]}" &
policy_pid=$!

deadline=$((SECONDS + POLICY_SERVICE_TIMEOUT))
while ! rosservice info /task2/policy/set_paused >/dev/null 2>&1; do
  if ! kill -0 "${policy_pid}" 2>/dev/null; then
    wait "${policy_pid}" || child_status=$?
    printf '策略在注册 set_paused 之前就退出了 (status %s)。\n' "${child_status:-0}" >&2
    printf '契约要求策略提供 /task2/policy/set_paused，见契约文档 §4.1。\n' >&2
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    printf '等待 /task2/policy/set_paused 超时 (%ss)。\n' "${POLICY_SERVICE_TIMEOUT}" >&2
    exit 1
  fi
  sleep 1
done

printf '\n%s\n' '============== 策略已就绪，当前处于暂停 =============='
printf '%s\n' '前臂不会动。后臂是失能的（软的），可以徒手搬到顺手的位置。'
printf '%s\n' ''
printf '%s\n' '  推理中   按下任一后臂示教按钮 → 推理暂停，该侧前臂跟随该侧后臂'
printf '%s\n' '           另一侧前臂定住不动'
printf '%s\n' '  操作完   再按一下同一个按钮 → 后臂变软，推理自动继续'
printf '%s\n' ''
printf '%s\n' '扶住五臂，确认急停可及、相机和工作区就绪。'
printf '%s\n' '====================================================='
read -r -p '确认无误后按 Enter 开始推理（Ctrl-C 放弃）: ' _

if ! rosservice call /task2/policy/set_paused false; then
  printf '解除暂停失败。\n' >&2
  exit 1
fi
printf '推理已开始。按示教按钮即可随时接管。Ctrl-C 结束。\n'

wait "${policy_pid}"

#!/usr/bin/env bash
# 逐条检查正在运行的策略是否满足 Task2 接管接口契约。
#
# 契约全文: multi_arm_launch_tools/docs/2026-08-07-task2-integration-contract.md §4
#
# 用法:
#   task2_policy_contract_check.sh          只做不动手臂的检查（默认）
#   task2_policy_contract_check.sh --live   额外验证解除暂停后确实转发【手臂会动】
#
# 先起协调器和策略，让策略停在暂停状态（部署模板会停在等 Enter 那一步），
# 然后在另一个终端跑这个脚本。
set -uo pipefail

LIVE=0
[[ "${1:-}" == "--live" ]] && LIVE=1

pass=0
fail=0
warn=0

ok()   { printf '  \033[32m通过\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31m不通过\033[0m %s\n' "$1"; printf '         %s\n' "${2:-}"; fail=$((fail + 1)); }
note() { printf '  \033[33m注意\033[0m  %s\n' "$1"; warn=$((warn + 1)); }

has_service() { rosservice info "$1" >/dev/null 2>&1; }
has_topic()   { rostopic info "$1" >/dev/null 2>&1; }

# 某个话题在 N 秒内有没有消息。用 rostopic echo 的超时行为判断，
# 比 rostopic hz 更可靠地区分"零消息"和"低频"。
topic_is_silent() {
  local topic="$1" seconds="$2"
  ! timeout "${seconds}" rostopic echo -n 1 "${topic}" >/dev/null 2>&1
}

publishers_of() {
  rostopic info "$1" 2>/dev/null \
    | awk '/^Publishers:/{f=1;next} /^Subscribers:/{f=0} f && /^ \*/ {print $2}'
}

printf '\n===== Task2 接口契约自检 =====\n\n'

# --- 前置：协调器 ----------------------------------------------------------
printf '前置条件\n'
if has_service /task2/teach_handover/reset_fault; then
  ok "协调器在运行"
  mode="$(rostopic echo -n 1 /task2/teach_handover/mode 2>/dev/null \
    | sed -n 's/^data: "\(.*\)"$/\1/p' | head -1)"
  if [[ "${mode}" == fault ]]; then
    note "协调器当前处于 fault，先 rosservice call /task2/teach_handover/reset_fault"
  else
    ok "协调器状态: ${mode:-未知}"
  fi
else
  bad "协调器没有运行" "先起 start_ms_piper_5arm_button_teach_task2.launch"
  printf '\n协调器不在，后面的检查没有意义。\n'
  exit 1
fi

# --- 契约 1: 不直接发前臂 ---------------------------------------------------
printf '\n契约 1  不得直接发布 /master/joint_*\n'
for side in left right; do
  topic="/master/joint_${side}"
  pubs="$(publishers_of "${topic}")"
  if [[ -z "${pubs}" ]]; then
    note "${topic} 没有发布者（协调器可能还没发过第一条）"
  else
    strangers="$(grep -v -e task2_teach_handover -e task2_teach_button <<<"${pubs}" || true)"
    if [[ -z "${strangers}" ]]; then
      ok "${topic} 只有协调器在发"
    else
      bad "${topic} 有协调器以外的发布者" "$(tr '\n' ' ' <<<"${strangers}")"
    fi
  fi
done

# --- 契约 3: 暂停服务 -------------------------------------------------------
printf '\n契约 3  提供 /task2/policy/set_paused\n'
if has_service /task2/policy/set_paused; then
  ok "服务已注册"
  srv_type="$(rosservice type /task2/policy/set_paused 2>/dev/null)"
  if [[ "${srv_type}" == "std_srvs/SetBool" ]]; then
    ok "类型正确 (std_srvs/SetBool)"
  else
    bad "类型不对: ${srv_type}" "契约要求 std_srvs/SetBool"
  fi
  node="$(rosservice node /task2/policy/set_paused 2>/dev/null)"
  if [[ "${node}" == *policy_gate* ]]; then
    note "由通用闸门提供，不是策略自己。闸门无法丢弃策略内部的动作块，见契约 §4.4"
  fi
else
  bad "服务不存在" "策略必须提供它；没做集成的话用 task2_wrap_policy.sh"
fi

# --- 契约 4: 启动后暂停 -----------------------------------------------------
printf '\n契约 4  启动后处于暂停状态\n'
silent=1
for side in left right; do
  topic="/task2/policy/joint_${side}"
  if ! has_topic "${topic}"; then
    note "${topic} 还没有被 advertise（策略可能仍在加载）"
    silent=0
    continue
  fi
  if topic_is_silent "${topic}" 3; then
    ok "${topic} 静默（符合暂停语义）"
  else
    bad "${topic} 正在发消息" "策略应当启动后暂停，等操作员显式恢复"
    silent=0
  fi
done

# --- 契约 2 + 5 + 6: 需要真的跑起来 -----------------------------------------
printf '\n契约 2/5/6  消息格式、暂停生效、恢复重规划\n'
if [[ "${LIVE}" -ne 1 ]]; then
  note "跳过（需要 --live，那一步手臂会动）"
else
  if ! has_service /task2/policy/set_paused; then
    bad "无法验证" "暂停服务不存在"
  else
    printf '  \033[33m即将解除暂停，手臂会动。扶好，急停就位。\033[0m\n'
    read -r -p '  按 Enter 继续，Ctrl-C 放弃: ' _
    rosservice call /task2/policy/set_paused false >/dev/null

    moved=1
    for side in left right; do
      topic="/task2/policy/joint_${side}"
      if topic_is_silent "${topic}" 5; then
        bad "${topic} 解除暂停后仍然静默" "策略没有恢复发布"
        moved=0
      else
        ok "${topic} 恢复后有数据"
        sample="$(timeout 3 rostopic echo -n 1 "${topic}" 2>/dev/null)"
        count="$(grep -c '^  -' <<<"$(sed -n '/^position:/,/^velocity:/p' <<<"${sample}")" || true)"
        if [[ "${count}" == 7 ]]; then
          ok "${topic} position 是 7 个值"
        else
          bad "${topic} position 有 ${count} 个值" "契约要求 7 个（6 关节 + 夹爪）"
        fi
        if grep -q "joint0" <<<"${sample}"; then
          ok "${topic} name 字段已填充"
        else
          bad "${topic} name 字段为空或不对" "契约要求 [joint0..joint6]"
        fi
      fi
    done

    if [[ "${moved}" -eq 1 ]]; then
      rosservice call /task2/policy/set_paused true >/dev/null
      sleep 1
      for side in left right; do
        topic="/task2/policy/joint_${side}"
        if topic_is_silent "${topic}" 3; then
          ok "${topic} 暂停后停止发布"
        else
          bad "${topic} 暂停后仍在发布" "契约 5：set_paused(true) 必须立即停止"
        fi
      done
      printf '  策略已重新置为暂停。\n'
    fi
  fi
fi

printf '\n===== 结果: 通过 %d，不通过 %d，注意 %d =====\n' "${pass}" "${fail}" "${warn}"
if [[ "${fail}" -gt 0 ]]; then
  printf '不满足契约。对照 docs/2026-08-07-task2-integration-contract.md §4 逐条修。\n'
  exit 1
fi
if [[ "${LIVE}" -ne 1 ]]; then
  printf '静态检查通过。契约 2/5/6 需要 --live 才能验证。\n'
fi
exit 0

#!/bin/bash
# Bring up exactly the CAN interfaces one deployment profile needs.
#
# Why this exists.  can_config_shuai.sh pins EXPECTED_CAN_COUNT=4 and exits on
# any USB port it does not recognise, so plugging in the two rear-arm adapters
# breaks it before a single interface is configured - "检测到的 CAN 模块数量 (6)
# 与预期数量 (4) 不符" - whether or not the rear arms are powered.  That makes
# the two scripts mutually exclusive on the same machine, and the old
# deployments are still needed.
#
# So: one port map, per-profile REQUIREMENTS, and unknown adapters are reported
# and skipped instead of being fatal.  A profile fails only when an interface it
# actually needs is missing.
#
# Usage:
#   bash can_config_cobot.sh legacy3   # 前左/前右/中 + 底盘 (start_ms_piper_3arm.launch)
#   bash can_config_cobot.sh task2     # 上面 + 左后/右后  (5 臂示教接管 / 采数据)
#   bash can_config_cobot.sh probe     # 只打印当前接线，什么都不改
set -uo pipefail

# bus-info -> name:bitrate.  Keep in sync with the hardware; this is the only
# place the wiring is written down.
declare -A PORT_MAP=(
  ["1-13.1.2:1.0"]="can_left:1000000"
  ["1-13.1.3:1.0"]="can_right:1000000"
  ["1-13.1.4:1.0"]="can_mid:1000000"
  ["1-13.1.1:1.0"]="can_rear_left:1000000"
  ["1-13.2:1.0"]="can_rear_right:1000000"
  ["1-4:1.0"]="can0:500000"
)

declare -A PROFILE_REQUIRES=(
  ["legacy3"]="can_left can_right can_mid"
  ["task2"]="can_left can_right can_mid can_rear_left can_rear_right"
  ["probe"]=""
)

PROFILE="${1:-}"
if [[ -z "${PROFILE}" || -z "${PROFILE_REQUIRES[${PROFILE}]+set}" ]]; then
  printf 'Usage: %s <legacy3|task2|probe>\n' "$0" >&2
  printf '  legacy3  前左/前右/中 (旧的 3 臂部署)\n' >&2
  printf '  task2    上面 + 左后/右后 (示教接管、采数据)\n' >&2
  printf '  probe    只打印接线，不做任何修改\n' >&2
  exit 2
fi

for tool in ip ethtool; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    printf '缺少 %s，请先 sudo apt install ethtool can-utils\n' "${tool}" >&2
    exit 1
  fi
done

if [[ "${PROFILE}" != probe ]]; then
  if ! sudo modprobe gs_usb; then
    printf '错误: 无法加载 gs_usb 模块。\n' >&2
    exit 1
  fi
fi

declare -A FOUND=()
declare -a UNKNOWN=()

mapfile -t INTERFACES < <(ip -br link show type can 2>/dev/null | awk '{print $1}')
if [[ "${#INTERFACES[@]}" -eq 0 ]]; then
  printf '错误: 系统中没有检测到任何 CAN 接口。\n' >&2
  exit 1
fi

# CAN 控制器状态。BUS-OFF 是"发出去的帧一直没有节点应答"的终点：
# 发送错误计数器涨满之后控制器自己下线，之后所有发送都失败。
# 接口名字还在、UP 还在、比特率也对 —— 只查这三样是查不出来的。
can_state() {
  ip -details link show "$1" 2>/dev/null \
    | grep -oE "ERROR-ACTIVE|ERROR-WARNING|ERROR-PASSIVE|BUS-OFF|STOPPED" \
    | head -1
}

# 发送能不能出去。收和发是两回事：接口可以收得好好的，发却因为
# 发送队列堵死而全部失败（write: No buffer space available）——
# 帧发出去没人应答时控制器会无限重传，qlen 只有 10，很快就堵满。
# 这种状态下 state 仍然是 ERROR-ACTIVE、收包照常，只查收是查不出来的。
#
# 用 0x7FF：标准帧里优先级最低的 ID，Piper 不使用，零字节数据，
# 对总线上的设备是惰性的。
can_transmit() {
  cansend "$1" '7FF#' >/dev/null 2>&1
}

# 总线上有没有设备在说话。Piper 手臂常态 200 Hz 广播，半秒内必然有包。
# 零包 = 接口配好了但对面没人 —— 手臂没上电、线松了、或者插错口。
bus_is_alive() {
  local iface="$1" before after
  before=$(cat "/sys/class/net/${iface}/statistics/rx_packets" 2>/dev/null || echo 0)
  sleep 0.5
  after=$(cat "/sys/class/net/${iface}/statistics/rx_packets" 2>/dev/null || echo 0)
  (( after > before ))
}

configure_one() {
  local iface="$1" target_name="$2" target_bitrate="$3"
  local is_up current_bitrate
  is_up=$(ip link show "${iface}" | grep -q "UP" && echo yes || echo no)
  current_bitrate=$(ip -details link show "${iface}" | grep -oP 'bitrate \K\d+' || true)

  local want_restart="${CAN_RESTART_MS:-100}"
  local current_restart
  current_restart=$(ip -details link show "${iface}" | grep -oP 'restart-ms \K[0-9]+' || echo 0)

  if [[ "${is_up}" == yes && "${current_bitrate:-0}" -eq "${target_bitrate}" \
        && "${current_restart:-0}" -eq "${want_restart}" ]]; then
    if [[ "${iface}" != "${target_name}" ]]; then
      sudo ip link set "${iface}" down
      sudo ip link set "${iface}" name "${target_name}"
      sudo ip link set "${target_name}" up
      printf '  %s -> %s (已激活, %s bps, 已重命名)\n' \
        "${iface}" "${target_name}" "${target_bitrate}"
    else
      printf '  %s (已就绪, %s bps, restart-ms %s)\n' \
        "${target_name}" "${target_bitrate}" "${current_restart}"
    fi
    return 0
  fi

  sudo ip link set "${iface}" down
  # restart-ms: 让内核在 bus-off 之后自动把控制器拉回来。
  # 默认是 0，也就是永不自动恢复 —— 一次接触不良就要人工重启整套系统。
  sudo ip link set "${iface}" type can bitrate "${target_bitrate}" \
    restart-ms "${want_restart}"
  sudo ip link set "${iface}" up
  if [[ "${iface}" != "${target_name}" ]]; then
    sudo ip link set "${iface}" down
    sudo ip link set "${iface}" name "${target_name}"
    sudo ip link set "${target_name}" up
  fi
  printf '  %s -> %s (已设置 %s bps 并激活)\n' \
    "${iface}" "${target_name}" "${target_bitrate}"
}

printf '检测到 %d 个 CAN 接口，目标 profile: %s\n' "${#INTERFACES[@]}" "${PROFILE}"

for iface in "${INTERFACES[@]}"; do
  # ethtool -i needs no privileges for bus-info, and reading it without sudo
  # is what lets "probe" run without a password prompt.
  bus_info=$(ethtool -i "${iface}" 2>/dev/null | awk '/bus-info/ {print $2}')
  if [[ -z "${bus_info}" ]]; then
    bus_info=$(sudo ethtool -i "${iface}" 2>/dev/null | awk '/bus-info/ {print $2}')
  fi
  if [[ -z "${bus_info}" ]]; then
    printf '  %s: 无法读取 bus-info，跳过\n' "${iface}" >&2
    continue
  fi
  entry="${PORT_MAP[${bus_info}]:-}"
  if [[ -z "${entry}" ]]; then
    UNKNOWN+=("${iface} @ ${bus_info}")
    continue
  fi
  IFS=':' read -r target_name target_bitrate <<<"${entry}"
  FOUND["${target_name}"]="${iface}"

  if [[ "${PROFILE}" == probe ]]; then
    printf '  %-16s @ %-16s -> %s\n' "${iface}" "${bus_info}" "${target_name}"
    continue
  fi

  # Configure everything recognised, not only what the profile requires: an
  # adapter left down and misnamed is a trap for the next profile, and bringing
  # a bus up costs nothing when nothing addresses it.
  configure_one "${iface}" "${target_name}" "${target_bitrate}"
  # configure_one may rename canN to the stable target name.  Health checks
  # must use the post-rename interface, not the now-nonexistent canN name.
  FOUND["${target_name}"]="${target_name}"
done

if [[ "${#UNKNOWN[@]}" -gt 0 ]]; then
  printf '未在接线表中的适配器 (已忽略，不影响本次部署):\n'
  for entry in "${UNKNOWN[@]}"; do
    printf '  %s\n' "${entry}"
  done
fi

missing=()
for required in ${PROFILE_REQUIRES[${PROFILE}]}; do
  if [[ -z "${FOUND[${required}]+set}" ]]; then
    missing+=("${required}")
  fi
done

if [[ "${PROFILE}" != probe && "${#missing[@]}" -gt 0 ]]; then
  printf '\n错误: profile "%s" 需要以下接口，但没有找到对应的适配器:\n' "${PROFILE}" >&2
  for name in "${missing[@]}"; do
    printf '  %s\n' "${name}" >&2
  done
  printf '请检查 USB 接线，或用 "%s probe" 查看当前实际接线。\n' "$0" >&2
  exit 1
fi

# 到这里只证明了"接口配好了"。下面这一段才回答"这条总线能不能用"。
printf '\n链路健康检查:\n'
if [[ "${PROFILE}" == probe ]]; then
  check_list="${!FOUND[*]}"
else
  check_list="${PROFILE_REQUIRES[${PROFILE}]}"
fi
unhealthy=0
bad_ifaces=()
for required in ${check_list}; do
  iface="${FOUND[${required}]:-${required}}"
  state="$(can_state "${iface}")"
  case "${state}" in
    BUS-OFF|STOPPED)
      printf '  %-16s %s -> 正在复位\n' "${required}" "${state}"
      sudo ip link set "${iface}" down
      sudo ip link set "${iface}" up
      state="$(can_state "${iface}")"
      ;;
  esac
  rx_ok=no; tx_ok=no
  bus_is_alive "${iface}" && rx_ok=yes
  can_transmit "${iface}" && tx_ok=yes
  if [[ "${rx_ok}" == yes && "${tx_ok}" == yes ]]; then
    printf '  %-16s %s, 收发正常\n' "${required}" "${state:-UNKNOWN}"
  else
    detail=""
    [[ "${rx_ok}" == no ]] && detail="收不到数据"
    [[ "${tx_ok}" == no ]] && detail="${detail:+${detail}, }发送失败"
    printf '  %-16s %s, \033[31m%s\033[0m\n' "${required}" "${state:-UNKNOWN}" "${detail}"
    unhealthy=1
    bad_ifaces+=("${required}:${iface}:${detail}")
  fi
done

if (( unhealthy )); then
  printf '\n\033[31m有总线不可用。\033[0m接口本身配好了（名字、UP、比特率都对），\n' >&2
  printf '但它在这条总线上收不到数据、或者发不出去。\n' >&2
  if [[ "${PROFILE}" == probe ]]; then
    printf '（probe 只报告，不修改任何东西）\n' >&2
  fi
  printf '这种状态下起 launch，会看到 SEND_MESSAGE_FAILED (100017) 然后使能超时退出。\n' >&2
  printf '\n处置:\n' >&2
  for entry in "${bad_ifaces[@]}"; do
    name="${entry%%:*}"; rest="${entry#*:}"; iface="${rest%%:*}"; why="${rest#*:}"
    if [[ "${why}" == *发送失败* ]]; then
      printf '  %s: 发送队列堵死，先复位接口\n' "${name}" >&2
      printf '    sudo ip link set %s down\n' "${iface}" >&2
      printf '    sudo ip link set %s type can bitrate %s restart-ms %s\n' \
        "${iface}" "$(ip -details link show "${iface}" | grep -oP 'bitrate \K[0-9]+' || echo 1000000)" \
        "${CAN_RESTART_MS:-100}" >&2
      printf '    sudo ip link set %s up\n' "${iface}" >&2
      printf '  还不行就把这条对应的 USB 适配器拔下来重插。\n' >&2
    else
      printf '  %s: 检查这条臂的电源、急停、CAN 线和航空插头\n' "${name}" >&2
    fi
  done
  exit 1
fi

if [[ "${PROFILE}" == probe ]]; then
  printf '\n所有接口收发正常。\n'
  exit 0
fi

printf '\nprofile "%s" 所需的 CAN 接口已全部就绪，链路健康。\n' "${PROFILE}"

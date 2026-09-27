#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"

MODE="${1:-}"
case "${MODE}" in
  configure|reset) ;;
  *)
    printf 'Usage: %s <configure|reset>\n' "$0" >&2
    exit 2
    ;;
esac

IFS= read -r SUDO_PASSWORD || true
if [[ -z "${SUDO_PASSWORD:-}" ]]; then
  printf '错误: 网页没有提供本次 CAN 操作所需的 sudo 密码。\n' >&2
  exit 1
fi
trap 'unset SUDO_PASSWORD' EXIT

# Keep the credential only in this process and feed every privileged command
# through stdin. A sudo timestamp created by a web child process is not
# reliable across the upstream script's nested shells.
sudo() {
  printf '%s\n' "${SUDO_PASSWORD}" | command sudo -S -p '' "$@"
}

if ! sudo -v; then
  printf '错误: sudo 认证失败；请重新输入密码。\n' >&2
  exit 1
fi

UPSTREAM="$COBOT_PLATFORM_ROOT/integrations/legacy_control/can_config_cobot.sh"
REQUIRED=(can_left can_right can_mid can_rear_left can_rear_right)

reset_existing() {
  local iface missing=0
  sudo modprobe gs_usb
  for iface in "${REQUIRED[@]}"; do
    if [[ ! -e "/sys/class/net/${iface}" ]]; then
      printf '缺少 %s；请检查电源或 USB-CAN 适配器。\n' "${iface}" >&2
      missing=1
      continue
    fi
    printf '复位 %s\n' "${iface}"
    sudo ip link set "${iface}" down
    sudo ip link set "${iface}" type can bitrate 1000000 restart-ms 100
    sudo ip link set "${iface}" up
  done
  (( missing == 0 ))
}

run_profile() {
  (
    source "${UPSTREAM}" task2
  )
}

if [[ "${MODE}" == reset ]]; then
  printf '正在将现有 Task2 CAN 接口复位为 1 Mbps / restart-ms 100。\n'
  reset_existing
  printf 'CAN 接口复位完成；请点击配置 CAN 重新检查五臂链路。\n'
  exit 0
else
  if run_profile; then
    printf 'Task2 CAN 配置完成。\n'
    exit 0
  fi
  printf '首次配置未通过，正在执行一次接口复位并重试。\n' >&2
  reset_existing
  if run_profile; then
    printf '复位重试成功，Task2 五臂链路已就绪。\n'
    exit 0
  fi
fi

printf 'CAN 复位重试后仍未通过。请按日志指出的接口检查臂电源和线缆；若发送仍失败，请插拔对应 USB-CAN 适配器后再点 CAN 重置。\n' >&2
exit 1

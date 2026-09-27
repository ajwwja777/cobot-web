#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PIPER_PACKAGE_DIR="${PIPER_PACKAGE_DIR:-/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper}"
readonly PI05_DEPLOYMENT_DIR="${PI05_DEPLOYMENT_DIR:-/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05}"

readonly -a SOURCES=(
  "${SCRIPT_DIR}/task2_handover/task2_handover_core.py"
  "${SCRIPT_DIR}/task2_handover/task2_handover_node.py"
  "${SCRIPT_DIR}/task2_handover/task2_handover_keyboard.py"
  "${SCRIPT_DIR}/task2_policy_adapters/pi05/inference_pi05_task2.py"
  "${SCRIPT_DIR}/task2_policy_adapters/pi05/inference_pi05_task2.sh"
  "${SCRIPT_DIR}/task2_policy_adapters/pi05/run_checkpoint_task2.sh"
  "${SCRIPT_DIR}/task2_policy_adapters/pi05/interface_task2_live.sh"
)
readonly -a DESTINATIONS=(
  "${PIPER_PACKAGE_DIR}/scripts/task2_handover_core.py"
  "${PIPER_PACKAGE_DIR}/scripts/task2_handover_node.py"
  "${PIPER_PACKAGE_DIR}/scripts/task2_handover_keyboard.py"
  "${PI05_DEPLOYMENT_DIR}/common/robot/inference_pi05_task2.py"
  "${PI05_DEPLOYMENT_DIR}/common/inference_pi05_task2.sh"
  "${PI05_DEPLOYMENT_DIR}/run_checkpoint_task2.sh"
  "${PI05_DEPLOYMENT_DIR}/interface_task2_live.sh"
)
readonly -a MODES=(0644 0755 0755 0755 0755 0755 0755)

usage() {
  printf 'Usage: %s <--install|--check>\n' "$0" >&2
  exit 2
}

[[ "$#" -eq 1 ]] || usage
readonly ACTION="$1"
[[ "${ACTION}" == --install || "${ACTION}" == --check ]] || usage

for index in "${!SOURCES[@]}"; do
  source_path="${SOURCES[$index]}"
  destination="${DESTINATIONS[$index]}"
  mode="${MODES[$index]}"
  if [[ ! -f "${source_path}" ]]; then
    printf 'Missing tracked Task2 source: %s\n' "${source_path}" >&2
    exit 1
  fi
  if [[ "${ACTION}" == --install ]]; then
    install -D -m "${mode}" "${source_path}" "${destination}"
    printf 'INSTALLED %s\n' "${destination}"
  else
    if [[ ! -f "${destination}" ]]; then
      printf 'MISSING %s\n' "${destination}" >&2
      exit 1
    fi
    if ! cmp -s "${source_path}" "${destination}"; then
      printf 'DRIFT %s\n' "${destination}" >&2
      exit 1
    fi
    actual_mode="$(stat -c '%a' "${destination}")"
    expected_mode="${mode#0}"
    if [[ "${actual_mode}" != "${expected_mode}" ]]; then
      printf 'MODE %s expected=%s actual=%s\n' \
        "${destination}" "${expected_mode}" "${actual_mode}" >&2
      exit 1
    fi
    printf 'OK %s\n' "${destination}"
  fi
done

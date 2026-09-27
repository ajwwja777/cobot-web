#!/usr/bin/env bash
# Install (or verify) the Task2 physical-teach handover files into the Piper
# ROS1 package.  The Teleop repo holds the tracked sources; piper/scripts is
# only a runtime install location, so --check must stay clean in git.
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PIPER_PACKAGE_DIR="${PIPER_PACKAGE_DIR:-/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper}"
readonly PI05_DEPLOYMENT_DIR="${PI05_DEPLOYMENT_DIR:-/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05}"

readonly -a SOURCES=(
  "${SCRIPT_DIR}/task2_homing/task2_homing_core.py"
  "${SCRIPT_DIR}/task2_teach_driver/task2_rear_teach_core.py"
  "${SCRIPT_DIR}/task2_teach_driver/piper_rear_teach_task2_node.py"
  "${SCRIPT_DIR}/task2_teach_handover/task2_teach_handover_core.py"
  "${SCRIPT_DIR}/task2_teach_handover/task2_teach_handover_node.py"
  "${SCRIPT_DIR}/task2_teach_handover/task2_teach_button_node.py"
  "${SCRIPT_DIR}/task2_teach_handover/task2_policy_gate_node.py"
  "${SCRIPT_DIR}/task2_teach_handover/task2_teach_handover_keyboard.py"
  "${SCRIPT_DIR}/task2_policy_adapters/pi05/interface_task2_teach_live.sh"
)
readonly -a DESTINATIONS=(
  "${PIPER_PACKAGE_DIR}/scripts/task2_homing_core.py"
  "${PIPER_PACKAGE_DIR}/scripts/task2_rear_teach_core.py"
  "${PIPER_PACKAGE_DIR}/scripts/piper_rear_teach_task2_node.py"
  "${PIPER_PACKAGE_DIR}/scripts/task2_teach_handover_core.py"
  "${PIPER_PACKAGE_DIR}/scripts/task2_teach_handover_node.py"
  "${PIPER_PACKAGE_DIR}/scripts/task2_teach_button_node.py"
  "${PIPER_PACKAGE_DIR}/scripts/task2_policy_gate_node.py"
  "${PIPER_PACKAGE_DIR}/scripts/task2_teach_handover_keyboard.py"
  "${PI05_DEPLOYMENT_DIR}/interface_task2_teach_live.sh"
)
readonly -a MODES=(0644 0644 0755 0644 0755 0755 0755 0755 0755)

usage() {
  printf 'Usage: %s <--install|--check>\n' "$0" >&2
  exit 2
}

[[ "$#" -eq 1 ]] || usage
readonly ACTION="$1"
[[ "${ACTION}" == --install || "${ACTION}" == --check ]] || usage

# task2_teach_handover_core.py imports the validated primitives from the
# dynamic-role coordinator, so that file must already be installed.
readonly SHARED_CORE="${PIPER_PACKAGE_DIR}/scripts/task2_handover_core.py"
if [[ ! -f "${SHARED_CORE}" ]]; then
  printf 'Missing prerequisite %s; run install_task2_handover.sh --install first\n' \
    "${SHARED_CORE}" >&2
  exit 1
fi

for index in "${!SOURCES[@]}"; do
  source_path="${SOURCES[$index]}"
  destination="${DESTINATIONS[$index]}"
  mode="${MODES[$index]}"
  if [[ ! -f "${source_path}" ]]; then
    printf 'Missing tracked Task2 teach source: %s\n' "${source_path}" >&2
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

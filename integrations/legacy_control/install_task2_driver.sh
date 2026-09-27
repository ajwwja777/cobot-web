#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_DIR="${SCRIPT_DIR}/task2_driver"
PIPER_PACKAGE_DIR="${PIPER_PACKAGE_DIR:-/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper}"
TARGET_DIR="${PIPER_PACKAGE_DIR}/scripts"

CORE_FILE="task2_rear_role_core.py"
NODE_FILE="piper_rear_role_task2_node.py"
DEPENDENCY_HELPER="${SCRIPT_DIR}/task2_driver/ensure_task2_ros_dependencies.py"

usage() {
  echo "Usage: $0 --check | --install" >&2
}

require_paths() {
  test -f "${SOURCE_DIR}/${CORE_FILE}"
  test -f "${SOURCE_DIR}/${NODE_FILE}"
  test -f "${DEPENDENCY_HELPER}"
  test -d "${TARGET_DIR}"
}

case "${1:-}" in
  --check)
    require_paths
    cmp --silent "${SOURCE_DIR}/${CORE_FILE}" "${TARGET_DIR}/${CORE_FILE}"
    cmp --silent "${SOURCE_DIR}/${NODE_FILE}" "${TARGET_DIR}/${NODE_FILE}"
    python3 "${DEPENDENCY_HELPER}" check "${PIPER_PACKAGE_DIR}"
    echo "Task2 driver files match the tracked sources."
    ;;
  --install)
    require_paths
    install -m 0644 "${SOURCE_DIR}/${CORE_FILE}" "${TARGET_DIR}/${CORE_FILE}"
    install -m 0755 "${SOURCE_DIR}/${NODE_FILE}" "${TARGET_DIR}/${NODE_FILE}"
    python3 "${DEPENDENCY_HELPER}" install "${PIPER_PACKAGE_DIR}"
    echo "Task2 driver files installed into ${TARGET_DIR}."
    ;;
  *)
    usage
    exit 2
    ;;
esac

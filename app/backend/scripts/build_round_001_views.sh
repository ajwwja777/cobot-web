#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 DEMO_HDF5_ROOT ROLLOUT_HDF5_ROOT LABELS_ROOT OUTPUT_ROOT" >&2
}

if [[ $# -ne 4 ]]; then
  usage
  exit 2
fi

DEMO_ROOT="$(realpath "$1")"
ROLLOUT_ROOT="$(realpath "$2")"
LABELS_ROOT="$(realpath "$3")"
OUTPUT_ROOT="$(realpath -m "$4")"
ROUND_ROOT="${OUTPUT_ROOT}/data/derived/in_the_pot/pi05/round_001"
TASK_TEXT="open the pot lid, put the object into the pot, and close the lid"

if [[ -e "${ROUND_ROOT}" ]]; then
  echo "refusing existing round output: ${ROUND_ROOT}" >&2
  exit 1
fi

python -m converters.convert_rollout_hdf5_to_lerobot \
  --mode legacy \
  --input "${DEMO_ROOT}" \
  --output "${ROUND_ROOT}/lerobot_demo" \
  --repo-id jiaan/in_the_pot_demo_round_001 \
  --task "${TASK_TEXT}"

python -m converters.convert_rollout_hdf5_to_lerobot \
  --mode rollout_v1 \
  --input "${ROLLOUT_ROOT}" \
  --output "${ROUND_ROOT}/lerobot_rollout" \
  --repo-id jiaan/in_the_pot_rollout_round_001 \
  --task "${TASK_TEXT}"

python -m dataset_tools.build_dagger_view \
  --view safe \
  --action-horizon 50 \
  --demo-dataset "${ROUND_ROOT}/lerobot_demo" \
  --rollout-dataset "${ROUND_ROOT}/lerobot_rollout" \
  --labels-root "${LABELS_ROOT}" \
  --output "${ROUND_ROOT}/safe"

python -m dataset_tools.build_dagger_view \
  --view masked \
  --action-horizon 50 \
  --demo-dataset "${ROUND_ROOT}/lerobot_demo" \
  --rollout-dataset "${ROUND_ROOT}/lerobot_rollout" \
  --labels-root "${LABELS_ROOT}" \
  --output "${ROUND_ROOT}/masked"

du -sh "${ROUND_ROOT}"/*
find "${ROUND_ROOT}" -name '*manifest.json' -type f -print | sort

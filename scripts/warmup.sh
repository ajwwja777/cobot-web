#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
ROOT="$COBOT_RLT_PROJECT_ROOT"
export PYTHONPATH="${ROOT}/envs/machine-a-py311-overlay:${ROOT}:${ROOT}/code/openpi-rlt/src"
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
exec /home/agilex/junfeng/workspace/pi05_cobot/.venv-server/bin/python -u -m methods.openpi_rlt.plug_v2.training_flow warmup

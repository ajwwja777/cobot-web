#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
uv sync --frozen --python 3.8
echo "Python environment installed in $ROOT/.venv; no services started."

#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
for arg in "$@"; do
 case "$arg" in
  --restart|--shadow|--no-record) ;;
  *) echo '用法：./scripts/rlt_demo.sh [--restart] [--shadow] [--no-record]；固定当前已选 actor，关闭在线学习与探索' >&2; exit 2;;
 esac
done
exec "$(dirname "${BASH_SOURCE[0]}")/rlt_up.sh" --frozen-actor "$@"

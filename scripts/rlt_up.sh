#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
ROOT="$COBOT_RLT_PROJECT_ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
ACTOR=reference
if [[ -f "$ROOT/runs/plug_v2/learning/warmup/actor.pt" || -f "$ROOT/runs/plug_v2/learning/rtc-v5/current.json" ]]; then ACTOR=latest; fi
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reference) ACTOR=reference;;
    --frozen-actor) ACTOR=warmup;;
    --corrective) ACTOR=corrective;;
    --restart) ARGS+=("$1");;
    --explore|--shadow|--no-record) ARGS+=("$1");;
    *) echo "新入口不需要checkpoint数字；使用 rlt_up.sh [--reference|--frozen-actor|--corrective] [--explore] [--no-record] [--restart]" >&2; exit 2;;
  esac
  shift
done
/usr/bin/python3 - <<'PY'
import json,urllib.request
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
 with opener.open('http://127.0.0.1:8015/api/console/config',timeout=3) as r:cfg=json.load(r)
except Exception as e:raise SystemExit('先运行 ./scripts/ui_up.sh：'+str(e))
if cfg.get('profile')!='plug_v2':
 raise SystemExit('网页需选择 plug_v2 配置')
if cfg.get('rlt_enabled') is not True:raise SystemExit('新模型尚未验收，请查看网页模型状态')
PY
exec /usr/bin/python3 -m methods.openpi_rlt.plug_v2.cli up --foreground --actor "$ACTOR" "${ARGS[@]}"

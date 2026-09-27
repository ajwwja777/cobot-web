#!/usr/bin/env bash
# Release only the registered plug_v3 Stage-1 model process.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
set -Eeuo pipefail
ROOT="$COBOT_RLT_PROJECT_ROOT"
registry="$ROOT/runs/plug_v3_yyshadow/model-server/process.json"
[[ -s "$registry" ]] || { echo "plug_v3 模型未运行"; exit 0; }
read -r pid ticks < <(/usr/bin/python3 - "$registry" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]));print(p['pid'],p['start_ticks'])
PY
)
if /usr/bin/python3 - "$pid" "$ticks" <<'PY'
import sys
try:
 t=open(f'/proc/{sys.argv[1]}/stat').read();p=t[t.rfind(')')+2:].split();ok=p[0]!='Z' and int(p[19])==int(sys.argv[2])
except Exception:ok=False
raise SystemExit(0 if ok else 1)
PY
then
  cmd=$(tr '\0' ' ' <"/proc/$pid/cmdline")
  [[ "$cmd" == *"plug_v3_yyshadow.serve_stage1"* ]] || { echo "登记 PID 已不属于 plug_v3，拒绝停止" >&2; exit 2; }
  kill -TERM -- "-$pid"
  for _ in {1..100}; do kill -0 "$pid" 2>/dev/null || break; sleep .2; done
  kill -0 "$pid" 2>/dev/null && { echo "模型仍在退出中" >&2; exit 1; }
fi
rm -f -- "$registry"
echo "plug_v3 Stage-1 模型已停止，GPU0 显存已释放"

#!/usr/bin/env bash
# Start/reuse the frozen plug_v3 Stage-1 server, then run upstream online RLT.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
# Ctrl-C stops Machine B and the robot Session; the loaded Stage-1 model remains.
set -Eeuo pipefail

ROOT="$COBOT_RLT_PROJECT_ROOT"
METHOD="$ROOT/methods/openpi_rlt/plug_v3_yyshadow"
RUN="$ROOT/runs/plug_v3_yyshadow"
MANIFEST="$ROOT/deployments/plug_v3_yyshadow/manifest.json"
MACHINE_PY=/home/agilex/junfeng/workspace/pi05_cobot/.venv-server/bin/python
ONLINE_PY="$ROOT/envs/rlt-online-py310/bin/python"
MODEL_PORT=8030
SESSION_PORT=8026
MODE=${1:-warmup}

if [[ "$MODE" != reference && "$MODE" != warmup && "$MODE" != frozen && "$MODE" != online ]]; then
  echo "用法: ./scripts/rlt_v3_up.sh [reference|warmup|frozen|online]" >&2
  exit 2
fi
[[ -s "$MANIFEST" ]] || { echo "plug_v3 发布清单不存在: $MANIFEST" >&2; exit 2; }
checkpoint=$(/usr/bin/python3 - "$MANIFEST" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['checkpoint'])
PY
)
[[ -d "$checkpoint/params" ]] || { echo "checkpoint 参数不存在: $checkpoint" >&2; exit 2; }

DATA_PHASE="$MODE"
[[ "$MODE" == reference ]] && DATA_PHASE=warmup
[[ "$MODE" == frozen ]] && DATA_PHASE=online
RLT_CONFIG="$METHOD/online_rl.yaml"
[[ "$MODE" == frozen ]] && RLT_CONFIG="$METHOD/online_rl_frozen.yaml"
DATA_ROOT="/media/agilex/Getea1/jiaan/data/rlt/plug_v3_yyshadow/$DATA_PHASE"
mkdir -p "$RUN/model-server" "$RUN/logs" "$RUN/online/metrics" "$DATA_ROOT"

port_open() {
  /usr/bin/python3 - "$1" <<'PY'
import socket,sys
s=socket.socket();s.settimeout(.2)
try: ok=s.connect_ex(('127.0.0.1',int(sys.argv[1])))==0
finally:s.close()
raise SystemExit(0 if ok else 1)
PY
}

model_registry="$RUN/model-server/process.json"
model_alive() {
  [[ -s "$model_registry" ]] || return 1
  /usr/bin/python3 - "$model_registry" "$checkpoint" <<'PY'
import json,os,sys
p=json.load(open(sys.argv[1]));pid=int(p['pid'])
try:
 text=open(f'/proc/{pid}/stat').read();parts=text[text.rfind(')')+2:].split()
 ok=parts[0]!='Z' and int(parts[19])==int(p['start_ticks']) and p['checkpoint']==sys.argv[2]
except Exception:ok=False
raise SystemExit(0 if ok else 1)
PY
}

if ! model_alive; then
  port_open "$MODEL_PORT" && { echo "端口 $MODEL_PORT 被未登记进程占用" >&2; exit 2; }
  log="$RUN/logs/model-$(date -u +%Y%m%dT%H%M%SZ).log"
  export PYTHONPATH="$ROOT/envs/machine-a-py311-overlay:$ROOT:$ROOT/code/openpi-rlt/src:$ROOT/code/openpi-rlt/scripts${PYTHONPATH:+:$PYTHONPATH}"
  setsid env CUDA_VISIBLE_DEVICES=0 XLA_PYTHON_CLIENT_MEM_FRACTION=.72 \
    PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 \
    "$MACHINE_PY" -u -m methods.openpi_rlt.plug_v3_yyshadow.serve_stage1 \
      --project-root "$ROOT" --checkpoint "$checkpoint" --port "$MODEL_PORT" \
      </dev/null >>"$log" 2>&1 &
  pid=$!
  start_ticks=$(/usr/bin/python3 - "$pid" <<'PY'
import sys
t=open(f'/proc/{sys.argv[1]}/stat').read();print(t[t.rfind(')')+2:].split()[19])
PY
)
  /usr/bin/python3 - "$model_registry" "$pid" "$start_ticks" "$checkpoint" "$log" <<'PY'
import json,os,sys,tempfile
path,pid,ticks,checkpoint,log=sys.argv[1:]
payload={'pid':int(pid),'start_ticks':int(ticks),'checkpoint':checkpoint,'log':log}
tmp=path+'.tmp';open(tmp,'w').write(json.dumps(payload,indent=2)+'\n');os.replace(tmp,path)
PY
  echo "Stage-1 正在加载（首次约数分钟）: $log"
fi

deadline=$((SECONDS+1800))
while ! port_open "$MODEL_PORT"; do
  model_alive || { echo "Stage-1 服务退出，请查看登记日志" >&2; exit 1; }
  (( SECONDS < deadline )) || { echo "Stage-1 加载超时" >&2; exit 1; }
  sleep 2
done
echo "Stage-1 已就绪: $(basename "$checkpoint")"

port_open 11311 || { echo "ROS master 未启动；先启动机械臂和相机节点" >&2; exit 2; }
port_open 8015 || { echo "数据网页未启动；先运行 cobot-platform/scripts/ui_up.sh" >&2; exit 2; }
port_open "$SESSION_PORT" && { echo "旧 RLT Session 仍占用 $SESSION_PORT；先 Ctrl-C 旧 rlt_v3_up 终端" >&2; exit 2; }

source "$TASK5_ROS_SETUP"
export PYTHONPATH="$ROOT:$ROOT/code/openpi-rlt/rlt_online_rl/src${PYTHONPATH:+:$PYTHONPATH}"
export COBOT_RLT_TRACE_DIR="$RUN/online/traces/$MODE"
export COBOT_RLT_SHADOW=0
export COBOT_RLT_SESSION_UI=1
export COBOT_RLT_SESSION_UI_PORT="$SESSION_PORT"
export COBOT_RLT_TASK5_URL=http://127.0.0.1:8015/api/rlt-recorder
export RLT_ACTOR_READY_TIMEOUT_SEC="${RLT_ACTOR_READY_TIMEOUT_SEC:-120}"
export RLT_REPLAY_READY_TIMEOUT_SEC="${RLT_REPLAY_READY_TIMEOUT_SEC:-120}"
export COBOT_RLT_TASK5_DATA_ROOT="$DATA_ROOT"
export COBOT_RLT_TASK_ID=plug_v3_yyshadow
export COBOT_RLT_MODEL_ID=openpi_rlt
export COBOT_RLT_CHECKPOINT_ID="$(basename "$checkpoint")"
export COBOT_RLT_DATASET_ROUND="$DATA_PHASE"
export COBOT_RLT_COLLECTION_PHASE="$DATA_PHASE"
export COBOT_RLT_WARMUP_MIN_SIZE=600
export COBOT_RLT_TASK5_MAX_TIMESTEPS=3000
export COBOT_RLT_PROMPT="Insert the held plug into the socket."
# Let each terminal UI request decide whether to home.  The runtime remains
# paused while finalization and the requested home operation complete.
export COBOT_RLT_HOME_AFTER_TERMINAL=1
export COBOT_RLT_HOME_TARGET=all
export COBOT_RLT_HOME_POSE=plug2
if [[ "$MODE" == reference ]]; then
  export COBOT_RLT_MIN_ONLINE_ACTOR_VERSION=2147483647
  # Reference is a frozen Stage-1 rollout.  Once replay grows beyond the
  # warmup threshold, the phase controller would otherwise wait forever for
  # a learner that is intentionally disabled in this mode.
  export COBOT_RLT_DISABLE_PHASE_CONTROLLER=1
  # Reference collects replay but must never start the learner.  It uses the
  # frozen Stage-1 ref_chunk for every action.
  export RLT_DISABLE_LEARNER=1
else
  # Upstream updates the actor every two learner steps: 20k warmup -> version 10k.
  export COBOT_RLT_MIN_ONLINE_ACTOR_VERSION=2500
  unset COBOT_RLT_DISABLE_PHASE_CONTROLLER
  unset RLT_DISABLE_LEARNER
fi
if [[ "$MODE" == reference || "$MODE" == warmup ]]; then
  # A reachable actor service with version -1 is expected before warmup has
  # published its first snapshot.  Runtime still falls back to ref_chunk.
  export RLT_ALLOW_UNINITIALIZED_ACTOR=1
else
  unset RLT_ALLOW_UNINITIALIZED_ACTOR
fi
export COBOT_RLT_LEARNER_STATUS_PATH="$RUN/online/metrics/learner_status.json"
export COBOT_RLT_REPLAY_URL=http://127.0.0.1:9132
export COBOT_RLT_ACTOR_URL=http://127.0.0.1:9131
export COBOT_RLT_CONTROL_HZ=20
export COBOT_RLT_CHUNK_EXEC_HORIZON=10
export COBOT_RLT_JOINT_STEP_LIMIT=0.03
export COBOT_RLT_GRIPPER_STEP_LIMIT=0.004
export COBOT_RLT_MAX_EPISODE_STEPS=0
export PYTHONDONTWRITEBYTECODE=1
# All RLT coordination services are loopback-only.  Never route actor,
# replay, Session or Task5 requests through the workstation HTTP proxy.
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy

ENV_FACTORY=methods.openpi_rlt.plug_v3_yyshadow.right_arm_env:create_right_arm_online_env
if [[ "${COBOT_RLT_EVALUATION:-0}" == 1 ]]; then
  PLATFORM="$COBOT_PLATFORM_ROOT"
  export PYTHONPATH="$PLATFORM/app/backend:$PYTHONPATH"
  "$ONLINE_PY" -m cobot_console.evaluation_env "$MODE" "$COBOT_EVAL_SNAPSHOT" "$COBOT_EVAL_CONFIG"
  RLT_CONFIG="$COBOT_EVAL_CONFIG"
  export RLT_DISABLE_LEARNER=1 COBOT_RLT_DISABLE_PHASE_CONTROLLER=1
  export COBOT_RLT_HOME_AFTER_TERMINAL=0
  export COBOT_RLT_TRACE_DIR="$COBOT_RUNTIME_ROOT/deployment/unused-traces"
  ENV_FACTORY=cobot_console.evaluation_env:create_evaluation_env
fi

# The web owns a single loaded model. Session preparation chooses collection
# or evaluation; the environment latches that choice before each episode.
if [[ "${COBOT_RLT_SHARED_MODEL:-0}" == 1 ]]; then
  export PYTHONPATH="$COBOT_PLATFORM_ROOT/app/backend:$PYTHONPATH"
  if [[ "$MODE" != online ]]; then
    "$ONLINE_PY" -m cobot_console.shared_model_env "$MODE" "$COBOT_EVAL_SNAPSHOT" "$COBOT_EVAL_CONFIG"
    RLT_CONFIG="$COBOT_EVAL_CONFIG"
    export RLT_DISABLE_LEARNER=1 COBOT_RLT_DISABLE_PHASE_CONTROLLER=1
  fi
  export COBOT_RLT_HOME_AFTER_TERMINAL=0
  export COBOT_RLT_TRACE_DIR="$COBOT_RUNTIME_ROOT/deployment/collection-traces/$MODE"
  ENV_FACTORY=cobot_console.shared_model_env:create_shared_env
fi

echo "RLT $MODE 已启动；操作页沿用 http://127.0.0.1:8015/。Ctrl-C 停止本次 Session，Stage-1 模型保留。"
# Upstream resolves artifact paths relative to the process working directory,
# not relative to the YAML file.  Keep it anchored beside the config so every
# entry under ../../../runs lands in this RLT project's run root regardless of
# whether the caller is a terminal or the web console.
cd "$METHOD"
exec env CUDA_VISIBLE_DEVICES=0 XLA_PYTHON_CLIENT_PREALLOCATE=false \
  "$ONLINE_PY" -u -m methods.openpi_rlt.scripts.online_role \
    --upstream-root "$ROOT/code/openpi-rlt" \
    --config "$RLT_CONFIG" \
    --env-factory "$ENV_FACTORY"

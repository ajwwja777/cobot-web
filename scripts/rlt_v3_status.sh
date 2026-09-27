#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
ROOT="$COBOT_RLT_PROJECT_ROOT"
RUN="$ROOT/runs/plug_v3_yyshadow"
/usr/bin/python3 - "$RUN" <<'PY'
import json,sys,urllib.request
import socket
from pathlib import Path
run=Path(sys.argv[1])
def read(path):
 try:return json.loads(path.read_text())
 except Exception:return None
def get(url):
 try:
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  with opener.open(url,timeout=1) as r:return json.load(r)
 except Exception:return None
registry=read(run/'model-server/process.json')
model='offline'
if registry:
 try:
  text=Path(f"/proc/{registry['pid']}/stat").read_text();parts=text[text.rfind(')')+2:].split()
  if parts[0]!='Z' and int(parts[19])==int(registry['start_ticks']):model='loading'
 except Exception:pass
if model=='loading':
 s=socket.socket();s.settimeout(.2)
 try:
  if s.connect_ex(('127.0.0.1',8030))==0:model='ready'
 finally:s.close()
replay=get('http://127.0.0.1:9132/stats') or {}
learner=read(run/'online/metrics/learner_status.json') or {}
session=get('http://127.0.0.1:8026/api/session') or {}
print(json.dumps({
 'model':model,
 'session_phase':session.get('phase','offline'),
 'episode':session.get('episode_id'),
 'replay_transitions':replay.get('size',0),
 'replay_adds_total':replay.get('adds_total',0),
 'learner_step':learner.get('global_step',0),
 'warmup_ready':learner.get('ready_for_online',False),
 'actor_version':learner.get('actor_version',learner.get('published_actor_version')),
},indent=2,ensure_ascii=False))
PY

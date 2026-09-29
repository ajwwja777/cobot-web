"""Read-only cached adapter to rl-platform telemetry. No model/hardware commands."""
import os
import threading
import time
from .paths import RLT, RUNTIME_ROOT
from .rl_import import runtime_package
from .rlt_progress import published_actor

class AnalysisReader:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}

    def snapshot(self, run=-1):
        with self._lock:
            now = time.monotonic()
            if run in self._cache and now-self._cache[run][0] < 8:
                return self._cache[run][1]
            runtime_package()
            from integrations.cobot_runtime.analysis import snapshot, read_json
            root = os.environ.get("COBOT_RLT_RUN_ROOT", str(RLT/"outputs/rlt/plug_v3_yyshadow"))
            config = RLT/"configs/rlt/plug_v3_yyshadow/online_rl.yaml"
            value = snapshot(root, config, run)
            actor = value.get("config", {}).get("runtime", {}).get("actor_service", {}).get("snapshot_path")
            value["publication"] = published_actor(actor) if actor else {}
            record = read_json(RUNTIME_ROOT/"deployment/process.json")
            model = record.get("model", {})
            value["deployment_record"] = {"model": {k: model.get(k) for k in
                ("id", "label", "kind", "checkpoint", "base_checkpoint")}, "pid": record.get("pid")}
            # At most the latest request and one historical run remain cached.
            if len(self._cache) >= 2: self._cache.clear()
            self._cache[run] = (now, value)
            return value

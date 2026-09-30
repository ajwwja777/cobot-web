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
            record = read_json(RUNTIME_ROOT/"deployment/process.json")
            model = record.get("model", {})
            profile_error = None
            if model.get('online_run_root') and model.get('online_config'):
                root, config = model['online_run_root'], model['online_config']
            elif model.get("runtime_profile"):
                from integrations.cobot_runtime.experiment_profiles import resolve
                try:
                    profile = resolve(model.get("id"), RLT)
                    if profile is None: raise ValueError("Runtime profile registration is missing")
                    root, config = profile["run_dir"], profile["config"]
                except (OSError, ValueError, KeyError) as exc:
                    profile_error = str(exc)
            value = snapshot(root, config, run)
            value["run_root"] = str(root)
            value["runtime_profile_error"] = profile_error
            if model.get("runtime_profile"):
                # Shared offline diagnostics retain their own source/checkpoint
                # provenance; live metrics/batches always come from this run.
                from pathlib import Path
                common = RLT/"outputs/rlt/plug_v3_yyshadow/analysis"
                for key, filename in (("credit_assignment", "credit_assignment.json"),
                        ("sensitivity", "rl_sensitivity.json"), ("visual_sensitivity", "visual_sensitivity.json")):
                    if not value.get(key): value[key] = read_json(common/filename)
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

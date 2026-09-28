"""Read project-owned registrations without importing model frameworks."""
import json
import runpy
from pathlib import Path
from .paths import PROJECT
def configured_models(values):
    result = []
    for project, name in [("rl-platform", "deployment_models.json"), ("vla-platform", "pi05_models.json")]:
        path = PROJECT.parent / project / "configs" / name
        data = json.loads(path.read_text())
        def expand(value):
            if isinstance(value, str):
                for key, replacement in values.items():
                    value = value.replace("{" + key + "}", replacement)
                return value
            if isinstance(value, list): return [expand(v) for v in value]
            if isinstance(value, dict): return {k: expand(v) for k,v in value.items()}
            return value
        result.extend(expand(data["models"]))
    ids = [row["id"] for row in result]
    if len(ids) != len(set(ids)): raise ValueError("Duplicate deployment registration")
    return result

def external_models():
    path = PROJECT.parent / "vla-platform/configs/external_models.json"
    if not path.is_file(): return []
    result = []
    for row in runpy.run_path(str(PROJECT.parent / "vla-platform/integrations/cobot/registry.py"))["load_models"]("external_models.json"):
        command = row.get("command") or []
        valid = isinstance(command, list) and all(isinstance(p, str) for p in command)
        required = row.get("required", []) + ([command[0]] if valid and command else [])
        if row.get("cwd"): required.append(row["cwd"])
        else: valid = False
        missing = [p for p in required if not Path(p).exists()]
        safe = row.get("load_behavior") in {"paused", "server_only"} and row.get("process_group") == "foreground"
        reason = ("Missing files: " + ", ".join(missing)) if missing else "" if safe and valid and command else "Declare paused/server-only foreground execution before managed loading"
        result.append({**row, "kind":"external", "mode":"external", "label":row.get("label", row["id"]),
            "checkpoint":row.get("checkpoint",""), "available":not reason, "unavailable_reason":reason,
            "unavailable_reason_en":reason, "availability":"files_present" if not reason else "unregistered",
            "evaluation_allowed":False, "training_enabled":False, "integration_level":"process_only",
            "capabilities":{key:key in {"load","stop","unload","logs","pid"} for key in
                ("load","ready","start","pause","resume","stop","unload","logs","pid","capture","hil","evaluate","train")},
            "verification":{"files":"present" if not missing else "missing","process_ready":"not_checked","inference":"not_checked","robot":"pending"}})
    return result

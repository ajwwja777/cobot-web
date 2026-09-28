"""Use the control project's shared host storage check for production assets."""
from functools import lru_cache
import os
import json
from pathlib import Path
import runpy

@lru_cache(maxsize=1)
def _checker():
    project = Path(__file__).resolve().parents[3]
    control = Path(os.environ.get("COBOT_CONTROL_PROJECT_ROOT", project.parent / "cobot-control"))
    return runpy.run_path(str(control / "robot/asset_storage.py"))["require_storage"]

def require_storage(path, write=False):
    target = Path(path).expanduser().absolute()
    mount = Path(os.environ.get("COBOT_ASSET_MOUNT") or "/media/agilex/Getea1")
    if target == mount or mount in target.parents:
        return _checker()(target, write=write)

def migrated_path(value):
    value = str(value)
    aliases = json.loads(os.environ.get("COBOT_DATA_ROOT_ALIASES") or "{}")
    for old, new in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if value == old or value.startswith(old + "/"):
            return new + value[len(old):]
    return value

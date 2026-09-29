"""Model-owned optional paths plus site selection defaults; never derived from weight filenames."""
import json
from pathlib import Path
from .paths import PROJECT, DATA

def model_directories(model, config=None, data_root=None):
    path = Path(config or PROJECT / "configs/model_directories.json")
    root = Path(data_root or DATA)
    try: mappings = json.loads(path.read_text()).get("models", {})
    except (OSError, ValueError): mappings = {}
    values = dict(mappings.get(model.get("id"), {}))
    values.update(model.get("data_directories") or {})
    result = {}
    for kind in ("collection", "evaluation"):
        value = values.get(kind)
        if not isinstance(value, str) or not value: continue
        candidate = Path(value.replace("{DATA}", str(root))).expanduser()
        # Backend storage endpoints still enforce mount, writer and dataset validation.
        if candidate.is_absolute(): result[kind] = str(candidate)
    return result

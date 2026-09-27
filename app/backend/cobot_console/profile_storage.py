"""Flat plug_v3 storage selection compatible with the unified recorder API."""
from __future__ import annotations
import json
from pathlib import Path

from .paths import LEGACY_DATA as ALLOWED, RLT
BASE = ALLOWED / "rlt/plug_v3_yyshadow"
RUN = RLT / "runs/plug_v3_yyshadow"
SETTINGS = RUN / "online/storage.json"

def settings():
    try:
        value=json.loads(SETTINGS.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}

def default_root(phase):
    return BASE / ("online" if phase=="online" else "warmup")

def validate_root(value):
    path=Path(value).expanduser()
    if not path.is_absolute(): raise ValueError("RLT data root must be absolute")
    resolved=path.resolve(strict=False)
    allowed=BASE.resolve(strict=False)
    try: resolved.relative_to(allowed)
    except ValueError as error: raise ValueError("RLT data root must stay inside "+str(allowed)) from error
    if path.exists() and path.is_symlink(): raise ValueError("RLT data root cannot be a symlink")
    return resolved

def selected_root(phase):
    value=(settings().get("current") or {}).get(phase)
    return validate_root(value) if value else default_root(phase)

def roots_for_phase(phase):
    values=[selected_root(phase),default_root(phase)]
    values.extend(Path(x) for x in ((settings().get("history") or {}).get(phase) or []))
    result=[]
    for value in values:
        try: value=validate_root(value)
        except ValueError: continue
        if value not in result: result.append(value)
    return result

def history(root):
    result=[]
    # plug_v3 uses Task5 label sidecars, not the plug_v2 *.rlt.json format.
    # Reading these small JSON files gives the episode list an immediate HIL
    # marker without opening every multi-GB HDF5 recording.
    for path in sorted(Path(root).glob("episode_*.labels.json")):
        episode_path=path.with_name(path.name[:-len(".labels.json")]+".hdf5")
        if not episode_path.is_file() or episode_path.is_symlink():
            continue
        try:
            value=json.loads(path.read_text())
            outcome=value.get("episode_outcome")
            uuid=value.get("episode_uuid")
            if outcome not in ("success","failure","aborted","unknown") or not uuid:
                continue
            result.append({
                "episode_uuid":uuid,
                "episode_index":int(path.name[len("episode_"):len("episode_")+6]),
                "outcome":outcome,
                "hil_frames":int(bool(value.get("interventions"))),
                "cohort":"plug_v3_yyshadow",
                "data_phase":Path(root).name,
            })
        except (OSError,ValueError,TypeError):
            continue
    for path in sorted(Path(root).glob("episode_*.rlt.json")):
        try:
            value=json.loads(path.read_text())
            if isinstance(value,dict): result.append(value)
        except (OSError,ValueError): pass
    return result

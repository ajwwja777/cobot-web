"""Site-owned launch paths and model registrations. Saving never starts a task."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import threading
from pathlib import Path
from uuid import uuid4
from typing import List

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .paths import PROJECT, CONTROL, RUNTIME_ROOT, SETTINGS, PI05, PI05_DAGGER

STATE = RUNTIME_ROOT / "data-console/site-options.json"
MODEL_ROOT = Path(SETTINGS.get("model_root", PROJECT.parent / "models"))
LOCK = threading.RLock()
TEMPLATES = {
    "plug-v3-warmup-5k": ("RLT · plug_insertion · frozen", "rlt"),
    "pi05-in-the-pot": ("π0.5 · in_the_pot · baseline RTC", "pi05"),
    "pi05-in-the-pot-dagger": ("π0.5 · in_the_pot · DAgger RTC", "pi05"),
}
ASSETS = {
    "pi05-in-the-pot": "wja/cobot_in_the_pot_40episodes",
    "pi05-in-the-pot-dagger": "task5_cobot_in_the_pot_round_001",
}


def read():
    try:
        value = json.loads(STATE.read_text())
        if not isinstance(value, dict):
            raise ValueError("Invalid site options")
        return value
    except FileNotFoundError:
        return {}


def write(value):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_name(STATE.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    os.replace(temporary, STATE)


def roots():
    values = [MODEL_ROOT, PROJECT.parent, Path.home()]
    setup = SETTINGS.get("ros_setup")
    if setup:
        values.append(Path(setup).parent.parent)
    values.extend(Path(p) for p in SETTINGS.get("extension_roots", []))
    return list(dict.fromkeys(p.expanduser().resolve() for p in values))


def checked_path(value, *, directory=None):
    path = Path(value).expanduser()
    if not value or not path.is_absolute():
        raise ValueError("Please choose an absolute server path / 请选择服务器绝对路径")
    path = path.resolve(strict=True)
    if not any(path == root or root in path.parents for root in roots()):
        raise ValueError("Path outside configured roots; add extension_roots in configs/local.json / 路径超出允许范围")
    if directory is True and not path.is_dir():
        raise ValueError("Directory required / 请选择目录")
    if directory is False and not path.is_file():
        raise ValueError("File required / 请选择文件")
    return path


def browse(value=""):
    if not value:
        return {"path": "", "parent": "", "entries": [
            {"path": str(p), "name": str(p), "directory": True} for p in roots() if p.is_dir()]}
    path = checked_path(value, directory=True)
    entries = []
    for p in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if p.name.startswith("."):
            continue
        try:
            checked = checked_path(str(p))
        except (ValueError, OSError):
            continue
        entries.append({"path": str(checked), "name": p.name, "directory": p.is_dir()})
        if len(entries) >= 250:
            break
    parent = str(path.parent) if any(path.parent == r or r in path.parent.parents for r in roots()) else ""
    return {"path": str(path), "parent": parent, "entries": entries}


from .control_import import control_package
control_package()
from cobot_control.site_hardware import hardware, device_marker, hardware_terminal, device_command, save_hardware

def validate_checkpoint(template, checkpoint):
    if template not in TEMPLATES:
        raise ValueError("Adapter not installed / 尚未安装该模型适配器")
    path = checked_path(checkpoint)
    if TEMPLATES[template][1] == "rlt":
        if path.name != "actor_snapshot.pkl" or not path.is_file():
            raise ValueError("RLT requires actor_snapshot.pkl, not a learner checkpoint")
        required = [path.parent.parent / "action_norm_stats.json"]
    else:
        if not path.is_dir():
            raise ValueError("π0.5 requires the complete checkpoint directory")
        required = [path / "params/_METADATA", path / "_CHECKPOINT_METADATA",
                    path / "assets" / ASSETS[template] / "norm_stats.json"]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise ValueError("Missing matching files / 缺少配套文件: " + ", ".join(missing))
    return str(path)


def register(template, checkpoint, label, compatible):
    if not compatible:
        raise ValueError("Confirm the task, cameras, action layout and normalization match the adapter")
    checkpoint = validate_checkpoint(template, checkpoint)
    identifier = "custom-" + hashlib.sha256((template + ":" + checkpoint).encode()).hexdigest()[:16]
    with LOCK:
        state = read()
        rows = state.setdefault("models", [])
        row = {"id": identifier, "template": template, "checkpoint": checkpoint,
               "label": label.strip() or Path(checkpoint).name}
        state["models"] = [r for r in rows if r["id"] != identifier] + [row]
        write(state)
    return identifier


def registered_models(builtins):
    templates = {m["id"]: m for m in builtins}
    result = []
    for row in read().get("models", []):
        template = templates.get(row["template"])
        if not template:
            continue
        reason = ""
        try:
            validate_checkpoint(row["template"], row["checkpoint"])
            if template["kind"] == "rlt":
                if not (Path(template["base_checkpoint"]) / "params").is_dir():
                    raise ValueError("RLT Stage 1 base is missing")
            else:
                root = PI05_DAGGER if row["template"].endswith("dagger") else PI05
                if not (root / "run_checkpoint_rtc_task2.sh").is_file():
                    raise ValueError("RTC adapter entry is missing")
        except (OSError, ValueError) as error:
            reason = str(error)
        result.append({**template, **row, "adapter_id": row["template"], "custom": True,
                       "available": not reason, "unavailable_reason": reason,
                       "step": None, "actor_version": None,
                       "validation": "Required files present; evaluation pending",
                       "training_lineage": None, "training_enabled": False})
    return result


def discover():
    """Bounded directory scan; never deserialize checkpoints or import model code."""
    result, visited = [], 0
    if not MODEL_ROOT.is_dir():
        return result
    for current, dirs, files in os.walk(MODEL_ROOT, followlinks=False):
        path = Path(current)
        visited += 1
        if visited > 3000:
            break
        depth = len(path.relative_to(MODEL_ROOT).parts)
        if depth > 8:
            dirs[:] = []
            continue
        candidate, format_name = None, ""
        if "params" in dirs:
            candidate, format_name = path, "OpenPI / Orbax"
        elif "actor_snapshot.pkl" in files:
            candidate, format_name = path / "actor_snapshot.pkl", "RLT actor snapshot"
        elif any(f.endswith((".safetensors", ".safetensors.index.json")) for f in files):
            candidate, format_name = path, "Safetensors"
        elif any(f.endswith(".pt") for f in files) and ("deployment_manifest.json" in files or "mp_rank_00_model_states.pt" in files):
            candidate, format_name = path, "PyTorch / distributed checkpoint"
        elif "deployment_manifest.json" in files:
            candidate, format_name = path, "Metadata only; checkpoint missing locally"
        dirs[:] = sorted(d for d in dirs if d not in {
            "params", "assets", "optimizer", "opt_state", "hf_processor", "replay_clean_v1"
        } and not d.startswith("."))
        if candidate:
            result.append({"checkpoint": str(candidate), "format": format_name,
                           "relative": str(candidate.relative_to(MODEL_ROOT))})
    return result


def inventory_models(builtins):
    known = {m["checkpoint"] for m in builtins}
    return [dict(id="asset-" + hashlib.sha256(row["checkpoint"].encode()).hexdigest()[:16],
                 label=row["relative"], kind="unadapted", mode="unavailable",
                 checkpoint=row["checkpoint"], available=False, format=row["format"],
                 availability="missing_files" if row["format"].startswith("Metadata only") else "unregistered",
                 validation="Web control adapter not registered",
                 unavailable_reason=("仅元数据，本机缺少权重" if row["format"].startswith("Metadata only")
                                     else "选择同类适配器登记；不同模型需要推理、暂停、HIL 与动作映射适配"),
                 unavailable_reason_en=("Metadata only; checkpoint missing locally" if row["format"].startswith("Metadata only")
                                        else "Register a matching adapter; different families need inference and control adaptation"))
            for row in discover() if row["checkpoint"] not in known]


class HardwareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component: str
    path: str = ""
    setup: str = ""
    cwd: str = ""
    args: List[str] = Field(default_factory=list, max_length=50)


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template: str
    checkpoint: str
    label: str = Field(default="", max_length=120)
    compatible: bool = False
    make_default: bool = False


class DefaultRequest(BaseModel):
    model_id: str


def install_routes(app, manager, devices):
    def guard():
        state = manager.status()
        if state.get("status_stale") or manager.busy() or state.get("operation") or state.get("active") or state.get("phase") not in {"offline", "checking"}:
            raise HTTPException(409, "Finish capture and release the model before changing runtime configuration / 请先结束采集并释放模型")

    @app.get("/api/site/options")
    def options():
        return {"hardware": hardware(), "defaults": {
            "arms": str(CONTROL / "scripts/arms_up.sh"),
            "cameras": str(CONTROL / "scripts/cameras_up.sh"),
            "setup": SETTINGS.get("ros_setup", "/opt/ros/noetic/setup.bash")},
            "templates": [{"id": key, "label": value[0]} for key, value in TEMPLATES.items()],
            "models": manager.models, "selected_model": manager.settings.get("model_id"),
            "roots": [str(p) for p in roots()], "state_path": str(STATE)}

    @app.get("/api/site/browse")
    def files(path: str = ""):
        try:
            return browse(path)
        except (OSError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    @app.post("/api/site/hardware")
    def update_hardware(request: HardwareRequest):
        with manager.lock, devices._job_lock:
            guard()
            # Configuration cannot change identity while its process is running.
            jobs = devices.status().get("jobs", {})
            from .device_control import _STOP_MARKERS
            external = any(devices.process_finder(marker) for component in ("arms", "cameras")
                           for marker in {_STOP_MARKERS[component], devices._marker(component)})
            if external or any(job.get("phase") in {"running", "stopping"} for job in jobs.values()):
                raise HTTPException(409, "Stop running device tasks before changing launch paths / 请先停止设备任务")
            try:
                save_hardware(**request.model_dump())
            except (OSError, ValueError) as error:
                raise HTTPException(422, str(error)) from error
        return options()

    @app.post("/api/site/models")
    def add_model(request: RegistrationRequest):
        with manager.lock:
            guard()
            try:
                identifier = register(request.template, request.checkpoint, request.label, request.compatible)
            except (OSError, ValueError) as error:
                raise HTTPException(422, str(error)) from error
            if request.make_default:
                from .deployment import atomic_json
                manager.settings["model_id"] = identifier
                atomic_json(manager.directory / "settings.json", manager.settings)
        manager.refresh_catalog()
        return {"model_id": identifier, **options()}

    @app.post("/api/site/default")
    def default(request: DefaultRequest):
        with manager.lock:
            guard()
            if not any(m["id"] == request.model_id and m["available"] for m in manager.models):
                raise HTTPException(422, "Model is not available / 模型尚不可用")
            from .deployment import atomic_json
            manager.settings["model_id"] = request.model_id
            atomic_json(manager.directory / "settings.json", manager.settings)
        return options()

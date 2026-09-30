"""Single-port composition of normal Task5 capture and RLT control."""

from __future__ import annotations

import os
import json
import shutil
import socket
import fcntl
import asyncio
import importlib.util
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from capture_core.api import create_app as create_rollout_app
from capture_core.config import RecorderConfig
from capture_core.labels import LabelStore
from capture_core.preview_jobs import PreviewJobManager
from capture_core.readiness import evaluate_readiness
from capture_core.recorder import RolloutRecorder
from capture_core.ros_cache import LatestMessageCache
from segmented_capture.api import (
    DEFAULT_ALLOWED_DATA_ROOT,
    DEFAULT_DATA_ROOT,
    create_app as create_segmented_app,
)
from segmented_capture.capture_service import SegmentedCaptureService
from segmented_capture.ports import CaptureGate

from .paths import CONTROL, RUNTIME_ROOT, RLT, RLT_MODELS, LEGACY_DATA, SETTINGS
from .mode import ModeConflict, RecorderModeCoordinator
from .diagnostics import ConsoleDiagnostics
from .rlt_proxy import RltBackendClient, RltBackendError, RltLifecycleRegistry
from .release_select import ReleaseSelectionError, ReleaseSelector
from .model_catalog import ModelCatalog, ModelSelectionError

DEFAULT_RLT_DATA_ROOT = Path(
    os.environ.get(
        "COBOT_RLT_TASK5_DATA_ROOT",
        str(LEGACY_DATA / "rlt/plug/online"),
    )
)
DEFAULT_LIFECYCLE_STATE = Path(
    os.environ.get(
        "COBOT_RLT_LIFECYCLE_STATE",
        str(RLT / "outputs/rlt/plug_v3_yyshadow/backend/state.json"),
    )
)


def load_rlt_storage():
    root = RLT
    source = root / "methods/openpi_rlt/plug_v2/storage.py"
    if not source.is_file():
        source = Path(__file__).with_name("legacy_storage.py")
    spec = importlib.util.spec_from_file_location("legacy_storage_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RltStorageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_root: str
    reset_on_refresh: bool = False



class DeviceOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component: str
    action: str
    target: Optional[str] = None
    pose: Optional[str] = None
    model: Optional[str] = None
    arms: Optional[List[str]] = None

class DeviceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Dict[str, object]
    confirmation_token: str

class ModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["normal", "rlt"]

class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release: str


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str


def _default_recorder(
    cache: LatestMessageCache, gate: CaptureGate, data_root: Path
) -> RolloutRecorder:
    config = RecorderConfig(
        data_root=data_root,
        # The external disk sustains roughly 12 raw three-camera frames/s.
        # Record at 10 Hz so long RLT episodes cannot fill the bounded writer
        # queue; policy observation/control still run independently at 20 Hz.
        sample_rate_hz=10.0,
        max_duration_seconds=300.0,
        min_free_disk_bytes=30 * 1024**3,
        stop_free_disk_bytes=20 * 1024**3,
        api_port=8015,
    )
    from capture_core.cached_writer import CachedEpisodeWriter
    return RolloutRecorder(
        config,
        cache,
        capture_gate=gate,
        writer_factory=CachedEpisodeWriter,
        drain_timeout_seconds=90.0,
    )


def create_app(
    *,
    cache: Optional[LatestMessageCache] = None,
    bridge: Optional[Any] = None,
    recorder: Optional[Any] = None,
    segmented_service: Optional[Any] = None,
    capture_gate: Optional[CaptureGate] = None,
    coordinator: Optional[RecorderModeCoordinator] = None,
    preview_manager: Optional[Any] = None,
    backend_client: Optional[Any] = None,
    lifecycle_registry: Optional[Any] = None,
    allowed_data_root: Optional[Path] = None,
    rlt_data_root: Optional[Path] = None,
    monotonic: Any = time.monotonic,
    diagnostics_provider: Optional[Any] = None,
    camera_preview: Optional[Any] = None,
    device_controller: Optional[Any] = None,
    release_selector: Optional[Any] = None,
):
    shared_cache = cache or LatestMessageCache()
    shared_gate = capture_gate or CaptureGate(enabled=False)
    selected_rlt_root = (rlt_data_root or DEFAULT_RLT_DATA_ROOT).expanduser().resolve()
    selected_profile = os.environ.get("COBOT_DATA_PROFILE", "legacy-camera-v1")
    profile_rlt_enabled = os.environ.get("COBOT_PROFILE_RLT_ENABLED", "1") == "1"
    if not selected_rlt_root.is_absolute():
        raise ValueError("RLT data root must be absolute")
    shared_recorder = recorder or _default_recorder(
        shared_cache, shared_gate, selected_rlt_root
    )
    shared_service = segmented_service or SegmentedCaptureService(
        recorder=shared_recorder,
        cache=shared_cache,
        gate=shared_gate,
        sidecar_root=None,
    )
    # The console is commonly started before ROS Core and the cameras.  Use the
    # reconnecting bridge for every data profile so later camera/arm launches
    # become visible without restarting the web service.
    from .ros_bridge import ReconnectingBridge
    shared_bridge = bridge or ReconnectingBridge(shared_cache)
    shared_previews = preview_manager or PreviewJobManager()
    modes = coordinator or RecorderModeCoordinator(initial_mode="normal")
    backend = backend_client or RltBackendClient(os.environ.get("COBOT_RLT_BACKEND_URL", "http://127.0.0.1:8016"))
    registry = lifecycle_registry or RltLifecycleRegistry(DEFAULT_LIFECYCLE_STATE)
    generation = str(uuid4())
    diagnostics = diagnostics_provider or ConsoleDiagnostics(monotonic=monotonic)
    releases = release_selector or ReleaseSelector()
    models = ModelCatalog(selected_profile)
    if camera_preview is None:
        from .camera_sync import SynchronizedCameraPreview
        camera_preview = SynchronizedCameraPreview(shared_cache, clock=monotonic)
    if device_controller is None:
        from .device_control import DeviceController
        device_controller = DeviceController(CONTROL / 'runtime/devices', extra_runtime=Path(os.environ.get('COBOT_CONSOLE_JOB_RUNTIME',str(RUNTIME_ROOT / 'console-jobs'))))

    def model_release() -> Dict[str, object]:
        manager = getattr(application.state, "deployment_manager", None)
        loaded = manager.status() if manager else {}
        if loaded.get("phase") != "offline" and loaded.get("model", {}).get("kind") == "rlt":
            return {**loaded["model"], "validated": bool(loaded["model"].get("available"))}
        selected = models.current()
        if selected is not None:
            return {**selected, "validated": bool(selected.get("available"))}
        manifest = os.environ.get("COBOT_RLT_MODEL_MANIFEST")
        if not manifest:
            return {"status": "legacy", "validated": profile_rlt_enabled}
        import json
        try:
            payload = json.loads(Path(manifest).read_text())
            valid = payload.get("cohort") == selected_profile and payload.get("status") == "offline_validated"
            return {"status": payload.get("status", "unknown"), "validated": valid,
                    "cohort": payload.get("cohort"), "checkpoint_updates": payload.get("checkpoint_updates"),
                    "rtc": payload.get("rtc"), "path": manifest}
        except FileNotFoundError:
            return {"status": "awaiting_stage1_deployment", "validated": False, "path": manifest}
        except (OSError, ValueError, TypeError):
            return {"status": "invalid_manifest", "validated": False, "path": manifest}

    def rlt_available() -> bool:
        return profile_rlt_enabled and bool(model_release()["validated"])

    def storage_contract():
        if selected_profile == "plug_v3_yyshadow":
            from . import profile_storage
            return profile_storage
        return load_rlt_storage()

    def readiness() -> Dict[str, object]:
        result=evaluate_readiness(
            shared_bridge.status(), shared_cache.snapshot(monotonic)
        )
        return result

    application = create_segmented_app(
        service=shared_service,
        bridge=shared_bridge,
        cache=shared_cache,
        allowed_data_root=allowed_data_root or DEFAULT_ALLOWED_DATA_ROOT,
        preview_manager=shared_previews,
        monotonic=monotonic,
        writer_coordinator=modes,
        mount_frontend=False,
    )
    if os.environ.get("COBOT_READ_ONLY") == "1":
        @application.middleware("http")
        async def read_only_preview(request, call_next):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                return JSONResponse({"detail": "Read-only migration preview"}, status_code=403)
            return await call_next(request)
    rollout = create_rollout_app(
        recorder=shared_recorder,
        label_store=LabelStore(selected_rlt_root),
        cache=shared_cache,
        ros_bridge_factory=lambda _cache: shared_bridge,
        preview_manager=shared_previews,
        monotonic=monotonic,
        readiness_provider=readiness,
        writer_coordinator=modes,
        capture_gate=shared_gate,
        mount_frontend=False,
        require_previous_labels=False,
    )

    @rollout.get("/rlt/identity")
    def rollout_identity() -> Dict[str, object]:
        return {
            "service": "rlt-continuous-task5-v1",
            "data_root": str(selected_rlt_root),
            "console_service": "cobot-data-console-v1",
        }

    def recording_directories():
        base = DEFAULT_ALLOWED_DATA_ROOT.expanduser().resolve()
        choices = [Path(p) for p in SETTINGS["recording_roots"]] if SETTINGS.get("recording_roots") else [DEFAULT_DATA_ROOT.expanduser(), base / "raw", LEGACY_DATA / "test"]
        tasks = base / "rlt"
        if tasks.is_dir():
            for task in sorted(tasks.iterdir()):
                if task.is_dir() and not task.is_symlink():
                    for phase in ("demonstrations", "warmup", "online"):
                        choices.append(task / phase)
        config_path = Path(__file__).resolve().parents[3] / "configs/paths.json"
        if config_path.is_file():
            import json
            config = json.loads(config_path.read_text())
            choices.extend(Path(p) for p in config.get("previous_data_roots", []))
        return list(dict.fromkeys(str(p) for p in choices if p.is_absolute() and any(root.resolve() in p.resolve().parents for root in (base, LEGACY_DATA))))

    @application.get("/api/console/config")
    def console_config() -> Dict[str, object]:
        return {
            "profile": selected_profile,
            "read_only": os.environ.get("COBOT_READ_ONLY") == "1",
            "normal_data_root": str(DEFAULT_DATA_ROOT.expanduser().resolve()),
            "test_data_root": str((allowed_data_root or DEFAULT_ALLOWED_DATA_ROOT).expanduser().resolve() / "datasets/test"),
            "data_root_choices": recording_directories(),
            "data_root_aliases": SETTINGS.get("data_root_aliases", {}),
            "storage_layout": "flat",
            "rlt_data_root": str(selected_rlt_root),
            "dataset_round": os.environ.get("COBOT_DATASET_ROUND", "node_pilot_v1"),
            "rlt_enabled": rlt_available(),
            "rlt_model": model_release(),
        }

    @application.get("/api/console/host")
    def console_host() -> Dict[str, object]:
        """Read-only local runtime facts for the operator's host panel."""
        project = Path(__file__).resolve().parents[3]
        data_root = DEFAULT_ALLOWED_DATA_ROOT.expanduser()
        probe = Path(os.environ.get("COBOT_HOST_DISK_PATH", "/")).expanduser()
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        disk = shutil.disk_usage(probe)
        try:
            load_1m = round(os.getloadavg()[0], 2)
        except OSError:
            load_1m = None
        return {
            "hostname": socket.gethostname(),
            "console_pid": os.getpid(),
            "cpu_count": os.cpu_count(),
            "load_1m": load_1m,
            "disk": {"path": str(probe), "total_bytes": disk.total,
                     "used_bytes": disk.used, "free_bytes": disk.free},
            "paths": {"platform": str(project), "data": str(data_root),
                      "normal_capture": str(DEFAULT_DATA_ROOT.expanduser()),
                      "rlt_capture": str(selected_rlt_root),
                      "rlt_project": str(RLT),
                      "training": str(RLT / "outputs/rlt/plug_v3_yyshadow"),
                      "deployment": str(RLT_MODELS)},
            "scripts": {name: str(project / "scripts" / name) for name in
                        ("ui_up.sh", "ui_down.sh", "home.sh", "recover.sh",
                         "rlt_up.sh", "rlt_down.sh", "rlt_demo.sh")
                        if (project / "scripts" / name).is_file()},
            "camera_preview_limit_fps": 20,
        }

    def rlt_storage_state():
        if selected_profile not in ("plug_v2", "plug_v3_yyshadow"):
            raise HTTPException(status_code=409, detail="storage_selection_requires_rlt_profile")
        storage = storage_contract()
        # Selecting a destination does not need a loaded or healthy Session.
        # A shared model latches this preference when the next episode starts.
        status = {"phase": "offline"}
        try:
            response = backend.request("GET", "/api/session")
            if response.status == 200:
                status = response.payload
        except RltBackendError:
            pass
        current = model_release()
        mode = current.get("mode") or current.get("start_target")
        phase = status.get("data_phase") or ("warmup" if mode == "reference" else "online")
        return storage, phase, status

    def recorder_details():
        raw = shared_recorder.status()
        return {**{k: raw.get(k) for k in ("state", "error", "last_error", "last_start_error",
                "path", "writer_thread_alive", "acquisition_active", "publication_status")},
                "readiness": readiness()}

    @application.get("/api/rlt/recorder-diagnostics")
    def recorder_diagnostics():
        return recorder_details()

    def check_recorder(data_root=None):
        from capture_core.validation import PreflightError
        if data_root is not None and not isinstance(data_root, str):
            raise HTTPException(422, "data_root_must_be_a_path_string")
        target = Path(data_root or selected_rlt_root).expanduser().resolve()
        allowed = (allowed_data_root or DEFAULT_ALLOWED_DATA_ROOT).expanduser().resolve()
        if target != allowed and allowed not in target.parents:
            raise HTTPException(422, "recording_path_outside_allowed_root")
        health = readiness()
        if health["status"] != "ok":
            return {"status": "not_ready", "error_code": health.get("error_code"),
                    "detail": ", ".join(health.get("stale_keys") or []), "data_root": str(target)}
        try:
            shared_recorder.check_ready(target)
            # The same flat-directory inspection used by the mounted recorder.
            # A saved unlabeled demonstration is not an unfinished recording.
            from capture_core.labels import LabelValidationError
            from capture_core.storage import StoragePathError
            try:
                prepared, _, inspection = rollout.state.inspect_storage(
                    data_root=target, task_id="recording", model_id="shared",
                    dataset_round="collection", storage_layout="flat")
            except (LabelValidationError, StoragePathError) as error:
                return {"status": "not_ready", "error_code": str(error),
                        "detail": str(target), "data_root": str(target)}
            return {"status": "ok", "error_code": None, "data_root": str(target),
                    "next_episode_index": prepared.next_episode_index,
                    "latest_labels_complete": inspection["latest_labels_complete"],
                    "label_blocked": inspection["label_blocked"]}
        except PreflightError as error:
            return {"status": "not_ready", "error_code": error.code,
                    "detail": error.detail, "data_root": str(target)}

    @application.post("/api/rlt/recorder-check")
    def check_rlt_recorder(body: Dict[str, object]):
        if shared_recorder.status().get("state") not in ("idle", "stopped"):
            raise HTTPException(409, "finish_recording_before_check")
        result = check_recorder(body.get("data_root"))
        return {**recorder_details(), "preflight": result}

    @application.post("/api/rlt/recover-runtime")
    def recover_rlt_runtime():
        manager = application.state.deployment_manager
        with manager.lock:
            if manager.operation or manager.active:
                raise HTTPException(409, "finish_model_operation_before_recovery")
            manager.operation = "runtime_recovery"
        try:
            from .runtime_lock import operation
            with operation(manager.runtime.directory):
                state = manager.runtime.status()
                if state.get("phase") != "error" or not (state.get("runtime_failure") or {}).get("recoverable"):
                    raise HTTPException(409, "runtime_not_recoverable_with_retained_model")
                snapshot, raw = modes.snapshot(), shared_recorder.status()
                if snapshot.active_mode not in (None, "rlt"):
                    raise HTTPException(409, "normal_recording_owns_writer")
                if (raw.get("active") or raw.get("writer_thread_alive") or raw.get("acquisition_active")
                        or raw.get("state") not in ("idle", "stopped")
                        or raw.get("completion_state") not in (None, "complete")):
                    raise HTTPException(409, "pending_episode_finalization")
                # No deletion, relabeling or implicit Replay insertion.
                rollout.state.release_completed_writer()
                if modes.snapshot().active_mode is not None:
                    raise HTTPException(409, "pending_episode_finalization")
                result = manager.runtime.recover_runtime()
                manager.collection_session = False
                manager.error = None
                return {**result, "model_retained": True, "manual_start_required": True}
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(409, str(error)) from error
        finally:
            manager.refresh()
            with manager.lock:
                manager.operation = None

    @application.post("/api/rlt/recover-recorder")
    def recover_rlt_recorder(body: Dict[str, object]):
        # Coordinate with load/unload and episode requests. Recovery never calls
        # start/resume, home or unload; the model remains paused in memory.
        manager = application.state.deployment_manager
        with manager.lock:
            if manager.operation or manager.active:
                raise HTTPException(409, "finish_model_operation_before_recovery")
            manager.operation = "recorder_recovery"
        try:
            from .runtime_lock import operation
            with operation(manager.runtime.directory):
                try:
                    response = backend.request("GET", "/api/session")
                except RltBackendError as error:
                    raise HTTPException(503, "cannot_verify_session_stopped") from error
                session = response.payload
                if response.status != 200 or session.get("policy_paused") is not True:
                    raise HTTPException(409, "pause_policy_before_recorder_recovery")
                snapshot = modes.snapshot()
                raw = shared_recorder.status()
                if snapshot.active_mode not in (None, "rlt"):
                    raise HTTPException(409, "normal_recording_owns_writer")
                if raw.get("writer_thread_alive") or raw.get("acquisition_active") or raw.get("state") in ("starting", "recording", "stopping"):
                    raise HTTPException(409, "recorder_worker_still_active")
                if session.get("phase") == "fault" and body.get("reset_fault_session") is True:
                    # No uncertain in-flight episode may be silently discarded.
                    if session.get("task5_episode_uuid") is not None or snapshot.active_mode is not None:
                        raise HTTPException(409, "pending_episode_finalization")
                    response = backend.request("POST", "/api/session/stop",
                        {"episode_id": session["episode_id"], "generation": session["generation"]})
                    if response.status != 200:
                        raise HTTPException(response.status, response.payload)
                    response = backend.request("GET", "/api/session")
                    session = response.payload
                if response.status != 200 or session.get("phase") != "stopped" or session.get("policy_paused") is not True:
                    raise HTTPException(409, "stop_session_before_recorder_recovery")
                recovered = False
                if raw.get("state") in ("error", "fatal"):
                    try:
                        raw = rollout.state.recover_failed_recorder()
                        recovered = True
                    except Exception as error:
                        raise HTTPException(409, str(error)) from error
                if modes.snapshot().active_mode is not None:
                    raise HTTPException(409, "pending_episode_finalization")
                result = {"state": raw.get("state"), "retained_incomplete_file": raw.get("path"),
                          "recovered": recovered, "model_retained": True, "session_phase": "stopped"}
                if body.get("reset_fault_session") is True:
                    result["preflight"] = check_recorder(body.get("data_root"))
                return result
        finally:
            with manager.lock:
                manager.operation = None
            manager.refresh()

    def can_reset_rlt_storage(status):
        manager = getattr(application.state, "deployment_manager", None)
        model = manager.status() if manager else {}
        phase = status.get("phase")
        idle = phase in ("disarmed", "ready", "armed", "waiting_scene", "stopped")
        if phase == "offline":
            idle = model.get("phase", "offline") == "offline"
        return (idle and not model.get("operation") and not model.get("active")
                and model.get("phase") != "running"
                and modes.snapshot().active_mode is None
                and shared_recorder.status().get("state") in ("idle", "stopped"))

    @application.get("/api/rlt/storage")
    def get_rlt_storage():
        storage, phase, status = rlt_storage_state()
        return {"data_root": str(storage.selected_root(phase)), "data_phase": phase,
                "editable": True, "applies_to": "next_episode",
                "recording_data_root": status.get("recording_data_root"),
                "can_reset_on_refresh": can_reset_rlt_storage(status),
                "recent_data_roots": list(dict.fromkeys([str(storage.selected_root(phase))] +
                    [p for paths in (storage.settings().get("history") or {}).values() for p in paths])),
                "data_root_choices": list(dict.fromkeys(([str(storage.ALLOWED / "test")] if not SETTINGS.get("recording_roots") else []) +
                    [str(p) for kind in ("warmup", "online") for p in storage.roots_for_phase(kind)]))}

    @application.post("/api/rlt/storage")
    def set_rlt_storage(request: RltStorageRequest):
        storage = storage_contract()
        folder = RUNTIME_ROOT / "data-console" if selected_profile == "plug_v3_yyshadow" else storage.RUN / "learning"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / ("storage.lock" if selected_profile == "plug_v3_yyshadow" else "operation.lock")).open("a") as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(status_code=409, detail="learning_or_start_in_progress")
            storage, phase, status = rlt_storage_state()
            if request.reset_on_refresh and not can_reset_rlt_storage(status):
                raise HTTPException(status_code=409, detail="active_task_keeps_recording_directory")
            # Legacy workers latch their directory at startup; only shared
            # workers support staging a directory during an active episode.
            if (status["phase"] not in ("offline", "disarmed", "ready", "armed", "waiting_scene", "stopped", "fault")
                    and not status.get("shared_model")):
                raise HTTPException(status_code=409, detail="先结束本轮再修改RLT录制目录")
            try:
                root = storage.validate_root(request.data_root)
                from capture_core.asset_storage import require_storage
                require_storage(root, write=True)
                root.mkdir(parents=True, exist_ok=True)
            except (ValueError, OSError) as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            data = storage.settings()
            old_root = str(storage.selected_root(phase))
            data["selected"] = str(root)
            data.setdefault("current", {})[phase] = str(root)
            previous = data.setdefault("history", {}).setdefault(phase, [])
            data["history"][phase] = list(dict.fromkeys([str(root), old_root] + previous))[:12]
            storage.SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            temporary = storage.SETTINGS.with_suffix(".tmp")
            with temporary.open("w") as f:
                json.dump(data, f, indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(temporary, storage.SETTINGS)
        return {"data_root": str(root), "data_phase": phase, "editable": True}

    @application.get("/api/rlt/history")
    def rlt_history():
        storage, phase, status = rlt_storage_state()
        root = storage.selected_root(phase)
        items = [item for item in storage.history(root) if item.get("outcome") in ("success", "failure", "unknown")]
        return {"data_root": str(root), "saved_count": len(items), "episodes": [
            {"number": i+1, "episode_uuid": d["episode_uuid"], "outcome": d["outcome"],
             "hil": d.get("hil_frames", 0)>0, "actor_version": d.get("actor_version", -1)}
            for i, d in enumerate(items)]}

    from .device_health import DeviceHealth
    device_health = DeviceHealth()

    @application.get("/api/console/devices")
    def console_devices() -> Dict[str, object]:
        value = device_controller.status()
        systems = dict(value.get("systems", {}))
        health_keys = [key for key in shared_cache.snapshot_keys() if not key.startswith("camera_")]
        snap = shared_cache.snapshot(float(monotonic()), keys=health_keys)
        systems["device_health"] = device_health.evaluate(systems, snap,
            home_started=float((value.get("jobs", {}).get("home") or {}).get("started_at") or 0))
        return {**value, "systems": systems}

    @application.post("/api/console/devices/confirm")
    def confirm_device_action(request: DeviceOperationRequest) -> Dict[str, object]:
        from .device_control import DeviceControlError
        try:
            return device_controller.confirm(request.model_dump(exclude_none=True))
        except DeviceControlError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.post("/api/console/devices/action")
    def run_device_action(request: DeviceActionRequest) -> Dict[str, object]:
        from .device_control import DeviceControlError
        try:
            deployment = application.state.deployment_manager
            if request.operation.get("component") in {"rlt", "rlt_model"} and deployment.status()["phase"] != "offline":
                raise DeviceControlError("请先在部署页释放当前评估模型")
            return device_controller.start(request.operation, request.confirmation_token)
        except DeviceControlError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.get("/api/console/cameras")
    def console_cameras() -> Dict[str, object]:
        return camera_preview.state()

    @application.get("/api/console/cameras/{camera_key}.jpg")
    def console_camera_image(camera_key: str, generation: int) -> Response:
        from .camera_sync import CAMERA_KEYS, CameraFrameUnavailable
        if camera_key not in CAMERA_KEYS:
            raise HTTPException(status_code=404, detail="camera_not_found")
        try:
            payload = camera_preview.image(camera_key, generation=generation)
        except CameraFrameUnavailable as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(payload, media_type="image/jpeg", headers={"Cache-Control":"no-store"})

    @application.get("/api/console/cameras/{camera_key}.mjpg")
    async def console_camera_stream(camera_key: str) -> StreamingResponse:
        from .camera_sync import CAMERA_KEYS, CameraFrameUnavailable
        if camera_key not in CAMERA_KEYS:
            raise HTTPException(status_code=404, detail="camera_not_found")

        async def stream():
            last_generation = -1
            while True:
                # JPEG encoding can take long enough to block the single ASGI
                # event loop when three MJPEG clients request frames together.
                loop = asyncio.get_running_loop()
                state = await loop.run_in_executor(None, camera_preview.state)
                generation = int(state.get("generation", 0))
                if generation > 0 and generation != last_generation:
                    try:
                        frame = await loop.run_in_executor(
                            None, lambda: camera_preview.image(camera_key, generation=generation)
                        )
                    except CameraFrameUnavailable:
                        frame = None
                    if frame:
                        last_generation = generation
                        yield (
                            b"--frame\r\nContent-Type: image/jpeg\r\n"
                            b"Cache-Control: no-store\r\n\r\n" + frame + b"\r\n"
                        )
                await asyncio.sleep(0.05)

        return StreamingResponse(
            stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control":"no-store"},
        )

    @application.get("/api/console/identity")
    def identity() -> Dict[str, object]:
        return {
            "service": "cobot-data-console-v1",
            "pid": os.getpid(),
            "generation": generation,
            "port": int(os.environ.get("COBOT_DATA_UI_PORT", "8015")),
            "rlt_recorder_prefix": "/api/rlt-recorder",
        }

    recording_counts_cache = {
        "value": {},
        "updated_at": 0.0,
        "refreshing": False,
    }
    recording_counts_lock = threading.Lock()

    def compute_recording_counts():
        cohort = "plug_v3_yyshadow" if selected_profile == "plug_v3_yyshadow" else "plug_v2"
        base=storage_contract().BASE
        result={}
        for phase in ('demonstrations','warmup','online'):
            root=base/phase;items={}
            roots=[root] if phase=='demonstrations' else storage_contract().roots_for_phase(phase)
            for selected in roots:
                if cohort == 'plug_v3_yyshadow' and phase != 'demonstrations':
                    for value in storage_contract().history(selected):
                        if value.get('cohort') == cohort:
                            items[value['episode_uuid']] = value.get('outcome', 'unknown')
                else:
                    for meta in selected.glob('episode_*.rlt.json'):
                        try:
                            value=json.loads(meta.read_text())
                            if value.get('cohort', cohort)!=cohort or value.get('data_phase',phase)!=phase:continue
                            items[value['episode_uuid']]=value.get('outcome','unknown')
                        except (OSError,ValueError,KeyError):continue
            summary=root/'recording_summary.json'
            if phase=='demonstrations' and summary.exists():
                try:result[phase]={'total':json.loads(summary.read_text())['success_episode_count'],'success':json.loads(summary.read_text())['success_episode_count']};continue
                except (OSError,ValueError,KeyError):pass
            if phase=='demonstrations':
                for manifest in sorted(root.glob('*/conversion_manifest.json')):
                    try:
                        converted=json.loads(manifest.read_text())
                        count=int(converted['accepted_episode_count'])
                        if converted.get('status')=='validated':
                            result[phase]={'total':count,'attempts':count,'success':count,'failure':0,'aborted':0}
                            break
                    except (OSError,ValueError,TypeError,KeyError):pass
                if phase in result: continue
            result[phase]={'total':sum(v in ('success','failure') for v in items.values()),'attempts':len(items),**{label:sum(v==label for v in items.values()) for label in ('success','failure','aborted')}}
        return result

    def refresh_recording_counts():
        try:
            value = compute_recording_counts()
        except Exception:
            value = None
        with recording_counts_lock:
            if value is not None:
                recording_counts_cache["value"] = value
                recording_counts_cache["updated_at"] = float(monotonic())
            recording_counts_cache["refreshing"] = False

    def recording_counts():
        now = float(monotonic())
        initial = False
        with recording_counts_lock:
            stale = now - float(recording_counts_cache["updated_at"]) >= 10.0
            initial = not recording_counts_cache["value"] and stale
            if stale and not initial and not recording_counts_cache["refreshing"]:
                recording_counts_cache["refreshing"] = True
                threading.Thread(
                    target=refresh_recording_counts,
                    name="cobot-recording-counts",
                    daemon=True,
                ).start()
            cached = {
                key: dict(value)
                for key, value in recording_counts_cache["value"].items()
            }
        if initial:
            value = compute_recording_counts()
            with recording_counts_lock:
                recording_counts_cache["value"] = value
                recording_counts_cache["updated_at"] = now
            return {key: dict(item) for key, item in value.items()}
        return cached

    from .analysis import AnalysisReader
    analysis_reader = AnalysisReader()

    @application.get("/api/analysis/rlt")
    def rlt_analysis(run: int = -1) -> Dict[str, object]:
        return analysis_reader.snapshot(run)

    @application.get("/api/console/diagnostics")
    def console_diagnostics() -> Dict[str, object]:
        def load_session():
            try:
                response = backend.request("GET", "/api/session")
            except RltBackendError:
                return None
            return response.payload if response.status == 200 else None

        payload = diagnostics.snapshot(shared_cache, load_session)
        current = models.current()
        if current is not None:
            payload["model"] = current
            payload["parameters"] = current.get("parameters", [])
        return payload

    @application.get("/api/console/status")
    def console_status() -> Dict[str, object]:
        try:
            lifecycle = registry.read()
            lifecycle_payload = asdict(lifecycle)
        except RltBackendError as error:
            lifecycle_payload = {
                "phase": "fault",
                "error_code": str(error),
                "generation": None,
            }
        # plug_v3 starts the upstream role directly and has no lifecycle
        # supervisor file. Fall back to its authoritative Session endpoint so
        # a healthy disarmed backend is not reported as offline.
        if selected_profile == "plug_v3_yyshadow" and lifecycle_payload.get("phase") == "offline":
            try:
                response = backend.request("GET", "/api/session")
            except RltBackendError:
                response = None
            if response is not None and response.status == 200:
                session = response.payload
                session_phase = str(session.get("phase") or "offline")
                lifecycle_phase = {
                    "disarmed": "ready_disarmed",
                    "armed": "ready_armed",
                    "ready": "ready_armed",
                    "fault": "fault",
                }.get(session_phase, "running")
                lifecycle_payload = {
                    **lifecycle_payload,
                    "phase": lifecycle_phase,
                    "generation": session.get("generation"),
                    "mode": selected_profile,
                    "error_code": session.get("fault_reason"),
                    "children": {"session": {"phase": session_phase}},
                }
        snapshot = modes.snapshot()
        return {
            **asdict(snapshot),
            "data_profile": selected_profile,
            "recording_counts": recording_counts() if selected_profile in ("plug_v2", "plug_v3_yyshadow") else {},
            "rlt_enabled": rlt_available(),
            "rlt_model": model_release(),
            "capture_phase": shared_service.status().get("capture_state", "unknown"),
            "recorder_state": shared_recorder.status().get("state", "unknown"),
            "ros_readiness": readiness(),
            "rlt_backend_phase": lifecycle_payload["phase"],
            "rlt_lifecycle": lifecycle_payload,
        }

    @application.post("/api/console/mode")
    def select_mode(request: ModeRequest) -> Dict[str, object]:
        if request.mode == "rlt" and not rlt_available():
            raise HTTPException(status_code=409, detail="profile_collection_only: adapt and validate the new camera model before RLT")
        try:
            return asdict(modes.select(request.mode))
        except ModeConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    def proxy(method: str, path: str, body: Optional[Dict[str, object]] = None):
        manager = application.state.deployment_manager
        deployment = manager.status()
        managed = deployment["phase"] != "offline" or deployment.get("operation")
        if modes.snapshot().selected_mode != "rlt":
            raise HTTPException(status_code=409, detail="rlt_mode_not_selected")
        if method == "POST" and path in {"/api/session/arm", "/api/session/prepare", "/api/session/start", "/api/session/resume", "/api/episode/next"} and not rlt_available():
            raise HTTPException(status_code=409, detail="plug_v2_model_not_validated")
        try:
            if method == "POST" and managed:
                from .deployment import DeploymentError
                try:
                    response = manager.collection_action(path, body)
                except DeploymentError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
            else:
                response = backend.request(method, path, body)
        except RltBackendError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return JSONResponse(status_code=response.status, content=response.payload)

    @application.get("/api/rlt/session")
    def rlt_session():
        return proxy("GET", "/api/session")

    def register_post(public_path: str, backend_path: str) -> None:
        # Blocking upstream HTTP must run in FastAPI's worker thread.
        # Session start/finalize calls this same server's recorder API.
        def action(body: Dict[str, object]):
            return proxy("POST", backend_path, body)

        application.add_api_route(public_path, action, methods=["POST"])

    for public_path, backend_path in (
        ("/api/rlt/session/prepare", "/api/session/prepare"),
        ("/api/rlt/episode/marker", "/api/episode/marker"),
        ("/api/rlt/episode/save", "/api/episode/save"),
        ("/api/rlt/session/arm", "/api/session/arm"),
        ("/api/rlt/session/start", "/api/session/start"),
        ("/api/rlt/session/pause", "/api/session/pause"),
        ("/api/rlt/session/resume", "/api/session/resume"),
        ("/api/rlt/session/stop", "/api/session/stop"),
        ("/api/rlt/episode/next", "/api/episode/next"),
        ("/api/rlt/episode/success", "/api/episode/success"),
        ("/api/rlt/episode/failure", "/api/episode/failure"),
        ("/api/rlt/episode/abort", "/api/episode/abort"),
    ):
        register_post(public_path, backend_path)

    @application.get("/api/rlt/models")
    def rlt_models() -> Dict[str, object]:
        return models.listing()

    @application.post("/api/rlt/model")
    def rlt_select_model(request: ModelRequest) -> Dict[str, object]:
        deployment = application.state.deployment_manager.status()
        if deployment["phase"] != "offline" or deployment.get("operation"):
            raise HTTPException(status_code=409, detail="请先在部署页释放评估模型")
        snapshot = modes.snapshot()
        if snapshot.active_mode is not None:
            raise HTTPException(status_code=409, detail="end_active_session_before_model_switch")
        try:
            response = backend.request("GET", "/api/session")
            phase = response.payload.get("phase") if response.status == 200 else None
        except RltBackendError:
            phase = "offline"
        if phase not in (None, "offline", "disarmed", "ready", "stopped", "waiting_scene"):
            raise HTTPException(status_code=409, detail="end_active_session_before_model_switch")
        try:
            return models.select(request.model_id)
        except ModelSelectionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.get("/api/rlt/releases")
    def rlt_releases() -> Dict[str, object]:
        return releases.listing()

    @application.post("/api/rlt/release")
    def rlt_select_release(request: ReleaseRequest) -> Dict[str, object]:
        deployment = application.state.deployment_manager.status()
        if deployment["phase"] != "offline" or deployment.get("operation"):
            raise HTTPException(status_code=409, detail="请先在部署页释放评估模型")
        # Blocking: runs the RLT switch and Session restart in FastAPI's worker thread.
        if modes.snapshot().selected_mode != "rlt":
            raise HTTPException(status_code=409, detail="rlt_mode_not_selected")
        try:
            response = backend.request("GET", "/api/session")
            phase = response.payload.get("phase") if response.status == 200 else None
        except RltBackendError:
            phase = None
        try:
            return releases.switch(request.release, phase)
        except ReleaseSelectionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    from .deployment import install_routes
    install_routes(application, camera_preview, modes, device_controller)

    application.mount("/api/rlt-recorder", rollout, name="rlt-recorder")

    review_frontend = Path(__file__).resolve().parents[1] / "frontend"

    @application.get("/rlt-review/", response_class=HTMLResponse)
    def rlt_review_page():
        html = (review_frontend / "index.html").read_text(encoding="utf-8")
        html = html.replace('href="/styles.css"', 'href="/rlt-review/styles.css"')
        html = html.replace('src="/workflow.js"', 'src="/rlt-review/workflow.js"')
        html = html.replace('src="/app.js"', 'src="/rlt-review/app.js"')
        html = html.replace("</head>", '<script src="/console_ui.js"></script></head>')
        html = html.replace("<main>", '<main><p class="notice">RLT 历史审核与回放；录制启停由统一控制台的 Session 管理。</p><p><a href="/">返回统一控制台</a></p>')
        return HTMLResponse(html)

    application.mount("/rlt-review", StaticFiles(directory=review_frontend), name="rlt-review-assets")
    frontend = Path(__file__).resolve().parents[1] / "segmented_frontend"
    application.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    application.state.shared_cache = shared_cache
    application.state.shared_bridge = shared_bridge
    application.state.shared_recorder = shared_recorder
    application.state.shared_gate = shared_gate
    application.state.mode_coordinator = modes
    return application


app = create_app()

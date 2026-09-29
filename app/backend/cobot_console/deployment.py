"""Managed, paused model loading and disk-backed physical evaluation records."""
from __future__ import annotations

import json
import os
import signal
import re
import shutil
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlencode
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from .rlt_proxy import RltBackendClient, RltBackendError
from .shared_model_env import BETWEEN_EPISODES
from capture_core.asset_storage import require_storage

from .paths import (SETTINGS as HOST_SETTINGS, PROJECT as PLATFORM, RLT, DATA, PI05 as LEGACY, PI05_DAGGER as DAGGER,
                    RUNTIME_ROOT, RLT_WARMUP, RLT_MODELS, PI05_CHECKPOINT, PI05_DAGGER_CHECKPOINT, migrated_data_path)
RUN = RLT / "outputs/rlt/plug_v3_yyshadow"
RUNTIME = RUNTIME_ROOT / "deployment"


class DeploymentError(RuntimeError):
    pass


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def catalog():
    manifest = read_json(RLT / "configs/rlt/plug_v3_yyshadow/manifest.json")
    base = str(manifest.get("checkpoint") or "")
    actor = RLT_WARMUP / "actor_snapshot/actor_snapshot.pkl"
    from .registry import configured_models
    definitions = configured_models({
        "RLT_WARMUP": str(RLT_WARMUP), "RLT_MODELS": str(RLT_MODELS),
        "BASE_CHECKPOINT": base, "PI05_CHECKPOINT": str(PI05_CHECKPOINT),
        "PI05_DAGGER_CHECKPOINT": str(PI05_DAGGER_CHECKPOINT),
    })
    for item in definitions:
        checkpoint = Path(item["checkpoint"]) if item["checkpoint"] else None
        available = checkpoint is not None and checkpoint.exists()
        if item["kind"] == "rlt":
            available = available and bool(base) and (Path(base) / "params").is_dir()
            if item.get("custom"):
                available = available and (checkpoint.parent.parent / "action_norm_stats.json").is_file()
        else:
            entry_root = DAGGER if item["id"].endswith("dagger") else LEGACY
            available = available and (entry_root / "run_checkpoint_rtc_task2.sh").is_file()
        if item.get("runtime_profile"):
            import runpy
            from .rlt_progress import published_actor
            try:
                profile = runpy.run_path(str(RLT / "integrations/cobot_runtime/experiment_profiles.py"))["resolve"](item["id"], RLT)
                publication = published_actor(checkpoint)
                learner = read_json(Path(profile["run_dir"]) / "online/metrics/learner_status.json")
                item.update(**publication, publication_tracked=True,
                    learner_step=learner.get("global_step", publication.get("published_learner_step")),
                    learner_actor_version=learner.get("actor_version", publication.get("published_actor_version")),
                    actor_version=publication.get("published_actor_version"),
                    step=publication.get("published_learner_step"),
                    experiment_label=item.get("experiment_label", item["runtime_profile"]))
            except (OSError, ValueError, KeyError):
                available = False
        item.update(available=bool(available), unavailable_reason="" if available else "权重或运行入口缺失",
                    entry=str(PLATFORM / "scripts/deployment_run.sh"), training_enabled=bool(item.get("capabilities", {}).get("train")),
                    recording="结果与三相机首尾帧")
    # Same catalog and process owner in both pages. Online training remains an
    # explicit choice; evaluation uses the frozen sibling of that checkpoint.
    from .model_catalog import ModelCatalog
    for item in ModelCatalog("plug_v3_yyshadow").listing()["models"]:
        if item["kind"] not in {"online", "frozen"}:
            continue
        online = item["kind"] == "online"
        definitions.append({
            **item, "kind": "rlt", "mode": item["start_target"],
            "base_checkpoint": base, "control_hz": 20, "home_pose": "plug2",
            "training_enabled": online, "evaluation_allowed": not online,
            "deterministic": not online,
            "entry": str(PLATFORM / "scripts/deployment_run.sh"),
        })
    from .model_metadata import vla_models, describe
    definitions.extend(vla_models())
    from .registry import external_models
    definitions.extend(external_models())
    from .site_options import registered_models, inventory_models
    definitions.extend(registered_models(definitions))
    definitions.extend(inventory_models(definitions))
    return [describe(item) for item in definitions]


def process_identity(pid):
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        return int(fields[19]) if fields[0] != "Z" else None
    except (OSError, ValueError, IndexError):
        return None


def tail(path):
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 32768))
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


from .runtime_lock import serialized

class ManagedRuntime:
    def __init__(self, directory=RUNTIME):
        self.directory = Path(directory)
        self.registry = self.directory / "process.json"
        self.backend = RltBackendClient("http://127.0.0.1:8026", timeout_seconds=.7)
        self.process = None

    def _alive(self, state):
        return bool(state.get("pid") and process_identity(state["pid"]) == state.get("start_ticks"))

    def _owned_members(self, state):
        """Keep control of children if their recorded session leader has exited."""
        pid, ticks = state.get("pid"), state.get("start_ticks")
        if not pid or ticks is None:
            return []
        current = process_identity(pid)
        if current is not None and current != ticks:
            return []
        members = []
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                raw = path.read_text()
                fields = raw[raw.rfind(")") + 2:].split()
                if fields[0] != "Z" and int(fields[2]) == pid and int(fields[3]) == pid and int(fields[19]) >= ticks:
                    members.append(int(path.parent.name))
            except (OSError, ValueError, IndexError):
                pass
        return members

    @serialized
    def load(self, model):
        from .device_control import _default_process_finder
        if model.get("capabilities", {}).get("load") is False:
            raise DeploymentError("Adapter does not support load")
        if self._owned_members(read_json(self.registry)):
            raise DeploymentError("请先释放当前部署模型")
        for marker in ("methods.openpi_rlt.scripts.online_role", "inference_pi05_rtc_task2.py", "deployment_pi05_client.py", "g05_task2_client.py", "inference_xr1_async.py", "adapters.fluxvla_cobot.task2_client", "methods.openpi_rlt.plug_v2.runtime"):
            if _default_process_finder(marker):
                raise DeploymentError("已有推理进程，请先结束并释放当前模型")
        # An existing v3 Stage-1 server can be reused only by v3 evaluation.
        if model["kind"] != "rlt" and self._alive(read_json(RUN / "model-server/process.json")):
            raise DeploymentError("插孔 Stage 1 仍占用显存，请先在 RL 采集释放模型")
        self.directory.mkdir(parents=True, exist_ok=True)
        log = self.directory / ("model-" + time.strftime("%Y%m%dT%H%M%S") + ".log")
        command = [str(PLATFORM / "scripts/deployment_run.sh"), model["id"]]
        cwd = None
        if model["kind"] == "external":
            command = model["command"]
            cwd = model["cwd"]
        if model["kind"] == "rlt":
            command.extend([model["checkpoint"], str(self.directory / "evaluation.yaml")])
        atomic_json(self.directory / "session-use.json", {
            "use": "collection" if model.get("training_enabled") else "evaluation"})
        environment = {**os.environ, "COBOT_MODEL_SESSION_USE": str(self.directory / "session-use.json")}
        import runpy
        vla_config = PLATFORM.parent / "vla-platform/integrations/cobot/registry.py"
        environment.update(runpy.run_path(str(vla_config))["runtime_environment"]())
        environment.pop("COBOT_DEPLOYMENT_ADAPTER", None)
        environment.pop("COBOT_CUSTOM_CHECKPOINT", None)
        if model["kind"] == "vla":
            environment["COBOT_MODEL_GATE_STATE"] = str(self.directory / "vla-gate.json")
            atomic_json(self.directory / "vla-gate.json", {"ready": False, "paused": True})
        if model.get("custom"):
            environment["COBOT_DEPLOYMENT_ADAPTER"] = model["adapter_id"]
            environment["COBOT_CUSTOM_CHECKPOINT"] = model["checkpoint"]
        with log.open("ab") as stream:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                            stderr=subprocess.STDOUT, start_new_session=True, env=environment, cwd=cwd)
        state = {"model": model, "pid": self.process.pid, "start_ticks": process_identity(self.process.pid),
                 "log_path": str(log), "phase": "loading", "started_at": time.time()}
        atomic_json(self.registry, state)
        return self.status()

    def status(self):
        saved = read_json(self.registry)
        if not saved:
            return {"phase": "offline"}
        if self.process is not None:
            self.process.poll()
        output = tail(saved.get("log_path", ""))
        if not self._alive(saved):
            return {**saved, "phase": "offline" if saved.get("phase") == "offline" else "error",
                    "detail": saved.get("detail") or output[-1200:] or "模型进程已退出", "log_tail": output}
        saved.update(process_started=True, model_ready=bool(saved.get("ready_confirmed")), inference_verified=False)
        model = saved["model"]
        if time.time() - saved["started_at"] > 1800 and not saved.get("ready_confirmed"):
            return {**saved, "phase": "error", "detail": "加载超过 30 分钟，请查看模型输出并释放后重试", "log_tail": output}
        if model["kind"] == "external":
            return {**saved, "phase":"process_running", "process_started":True,
                "model_ready":False, "inference_verified":False, "log_tail":output,
                "detail":"Process started; this shell entry has no model readiness protocol"}
        session = None
        if model["kind"] == "rlt":
            try:
                response = self.backend.request("GET", "/api/session")
                if response.status == 200 and (response.payload.get("shared_model") or response.payload.get("evaluation_only")) and response.payload.get("deployment_model_id") == model["id"]:
                    session = response.payload
            except RltBackendError:
                pass
            phase = session.get("phase") if session else "loading"
            mapped = "ready" if phase in ("disarmed", "armed", "ready", "waiting_scene", "stopped") else "running" if phase == "rollout" else "paused" if phase in ("paused", "hil", "terminal_pending") else "finalizing" if phase in ("recording_starting", "finalizing", "replay_committing") else "error" if phase == "fault" else "loading"
            if session and mapped in {"ready","paused","running"} and not saved.get("ready_confirmed"):
                saved["ready_confirmed"] = True
                saved["model_ready"] = True
                atomic_json(self.registry, saved)
            return {**saved, "phase": mapped, "session": session, "intervention_count": (session or {}).get("intervention_count", 0), "detail": (session or {}).get("fault_reason"), "log_tail": output}
        if model["kind"] == "vla":
            gate = read_json(self.directory / "vla-gate.json")
            ready = gate.get("ready") and gate.get("pid") == saved["pid"]
            phase = "paused" if ready and gate.get("paused", True) else "running" if ready else "loading"
            if ready and not saved.get("ready_confirmed"):
                saved["ready_confirmed"] = True
                saved["model_ready"] = True
                atomic_json(self.registry, saved)
            return {**saved, "phase": phase, "log_tail": output,
                    "intervention_count": gate.get("intervention_count", 0)}
        ready = "ready and PAUSED" in output or saved.get("ready_confirmed")
        if ready and not saved.get("ready_confirmed"):
            saved["ready_confirmed"] = True
            saved["model_ready"] = True
            atomic_json(self.registry, saved)
        gate = read_json(self.directory / "pi05-gate.json")
        phase = "paused" if ready and gate.get("paused", True) else "running" if ready else "loading"
        return {**saved, "phase": phase, "log_tail": output, "intervention_count": gate.get("intervention_count", 0)}

    def _session_action(self, path, body=None):
        status = self.status()
        session = status.get("session")
        if not session:
            raise DeploymentError("部署 Session 尚未就绪")
        request = {"episode_id": session["episode_id"], "generation": session["generation"], **(body or {})}
        response = self.backend.request("POST", path, request)
        if response.status >= 400:
            raise DeploymentError(str(response.payload.get("error") or response.payload.get("detail") or response.payload))
        return response.payload

    @serialized
    def select_use(self, use):
        state = self.status()
        if state.get("model", {}).get("kind") != "rlt":
            return
        session = state.get("session") or {}
        if not session.get("shared_model"):
            raise DeploymentError("请释放旧进程后重新加载模型，以启用共享 Session")
        if session.get("phase") not in BETWEEN_EPISODES:
            raise DeploymentError("请先结束当前 Episode，再切换采集或评测")
        if use == "evaluation" and state["model"].get("training_enabled"):
            raise DeploymentError("在线更新已启用；评测请选择同路径的冻结模型")
        atomic_json(self.directory / "session-use.json", {"use": use})

    @serialized
    def collection_action(self, path, body=None):
        state = self.status()
        session = state.get("session") or {}
        if state.get("model", {}).get("kind") != "rlt" or not session.get("shared_model"):
            raise DeploymentError("请先加载采集模型")
        if path in {"/api/session/arm", "/api/session/prepare", "/api/session/start", "/api/episode/next"}:
            self.select_use("collection")
        elif session.get("session_use") != "collection":
            raise DeploymentError("当前为评测轮次，请使用部署控制")
        return self.backend.request("POST", path, body)

    @serialized
    def action(self, operation):
        state = self.status()
        capability = "stop" if operation in {"success","failure","abort"} else operation
        if state.get("model", {}).get("capabilities", {}).get(capability) is False:
            raise DeploymentError("Adapter does not support " + capability)
        if state["phase"] in ("offline", "loading", "error", "process_running"):
            raise DeploymentError("模型尚未就绪：" + str(state.get("detail") or state["phase"]))
        if state["model"]["kind"] == "rlt":
            phase = state["session"]["phase"]
            if operation == "start":
                if phase in ("disarmed", "stopped"):
                    self._session_action("/api/session/prepare")
                return self._session_action("/api/episode/next" if phase == "waiting_scene" else "/api/session/start")
            if operation in ("success", "failure", "abort"):
                return self._session_action("/api/episode/" + operation, {"home_after_terminal": False})
            if operation == "pause" and phase != "rollout":
                return state
            return self._session_action("/api/session/" + operation)
        paused = operation != "start" and operation != "resume"
        import shlex
        setup = HOST_SETTINGS.get("ros_setup", "/opt/ros/noetic/setup.bash")
        command = ["bash", "-c", "source " + shlex.quote(setup) + " && exec /usr/bin/python3 \"$@\"", "deployment",
                   str(PLATFORM / "app/backend/cobot_console/deployment_ros.py"), "pause" if paused else "resume"]
        if state["model"]["kind"] == "vla" and not paused:
            command.append("--arm")
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise DeploymentError((result.stderr or result.stdout).strip()[-900:])
        return {"phase": "paused" if paused else "running"}

    @serialized
    def unload(self):
        saved = read_json(self.registry)
        if self._owned_members(saved):
            state = self.status()
            if state["phase"] not in ("loading", "error", "process_running") and saved["model"].get("capabilities", {}).get("pause", True):
                self.action("pause")
            if saved["model"]["kind"] == "rlt" and state.get("session"):
                try:
                    self._session_action("/api/session/stop")
                except (DeploymentError, RltBackendError):
                    pass  # Continue stopping this owned process even if Session is faulted.
            os.killpg(saved["pid"], signal.SIGINT)
            grace = min(45, max(8, float(saved["model"].get("shutdown_grace_seconds", 8))))
            deadline = time.monotonic() + grace
            while self._owned_members(saved) and time.monotonic() < deadline:
                if self.process:
                    self.process.poll()
                time.sleep(.1)
            if self._owned_members(saved):
                os.killpg(saved["pid"], signal.SIGTERM)
                deadline = time.monotonic() + 5
                while self._owned_members(saved) and time.monotonic() < deadline:
                    if self.process:
                        self.process.poll()
                    time.sleep(.1)
            if self._owned_members(saved):
                raise DeploymentError("模型进程仍在退出，请查看输出")
        if saved.get("model", {}).get("kind") == "rlt":
            result = subprocess.run([str(PLATFORM / "scripts/rlt_v3_down.sh")], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise DeploymentError(result.stderr or result.stdout)
        atomic_json(self.registry, {**saved, "phase": "offline", "detail": "模型已释放"})
        return {"phase": "offline"}


class DeploymentManager:
    def __init__(self, cameras, *, runtime=None, directory=RUNTIME, allowed_root=DATA, model_provider=catalog, busy=lambda: False):
        self.cameras = cameras
        self.runtime = runtime or ManagedRuntime(directory)
        self.directory = Path(directory)
        self.allowed_root = Path(allowed_root).resolve()
        self.model_provider = model_provider
        self.busy = busy
        self.lock = threading.RLock()
        self.settings = read_json(self.directory / "settings.json")
        self.active = read_json(self.directory / "active.json") or None
        self.cached = {"phase": "offline"}
        self.error = None
        self.operation = None
        self.models = []
        self.observed_at = None
        self.catalog_observed_at = 0.0
        self.last_status = {}
        self.collection_session = False

    def root(self, path=None):
        path = Path(migrated_data_path(path or self.settings.get("data_root") or self.allowed_root / "evaluations")).expanduser()
        if not path.is_absolute():
            raise DeploymentError("请选择绝对保存路径")
        resolved = path.resolve()
        if resolved != self.allowed_root and self.allowed_root not in resolved.parents:
            raise DeploymentError("保存位置必须位于 " + str(self.allowed_root))
        return resolved

    EVALUATION_GROUPS = {
        "plug-v3-warmup-5k": "plug_insertion/rl-platform/rlt/warmup_5000",
        "plug-v3-reference": "plug_insertion/rl-platform/rlt/reference_4999",
        "plug-v3-warmup-20k": "plug_insertion/rl-platform/rlt/warmup_20000",
        "plug_v3-stage1-reference": "plug_insertion/rl-platform/rlt/reference_4999",
        "plug_v3-frozen-latest": "plug_insertion/rl-platform/rlt/frozen_online",
        "pi05-in-the-pot": "in_the_pot/vla-platform/pi05/baseline_2000",
        "pi05-in-the-pot-dagger": "in_the_pot/vla-platform/pi05/dagger_2000plus3000",
    }

    def evaluation_root(self):
        root = self.root()
        base = self.allowed_root / "evaluations"
        for group in self.EVALUATION_GROUPS.values():
            standard = base / group
            if root == standard or standard in root.parents:
                return base
        return root

    def trial_root(self, model):
        root = self.evaluation_root()
        group = self.EVALUATION_GROUPS.get(model.get("id"))
        return root / group / time.strftime("%Y-%m-%d") if group else root

    def save_settings(self, data_root, *, reset_on_refresh=False):
        with self.lock:
            if self.active:
                raise DeploymentError("本轮结束后再更换保存位置")
            if reset_on_refresh and (self.operation or self.busy() or self.cached.get("phase") == "running"):
                raise DeploymentError("active_task_keeps_evaluation_directory")
            root = self.root(data_root)
            require_storage(root, write=True)
            root.mkdir(parents=True, exist_ok=True)
            previous = self.settings.get("recent_data_roots") or []
            self.settings["recent_data_roots"] = list(dict.fromkeys(
                [str(root), str(self.root())] + previous))[:12]
            self.settings["data_root"] = str(root)
            atomic_json(self.directory / "settings.json", self.settings)
        return {"data_root": str(root)}

    def refresh(self):
        try:
            state = self.runtime.status()
            with self.lock:
                self.cached = state
                if self.active and not self.active.get("intervened") and ((state.get("session") or {}).get("phase") == "hil" or state.get("intervention_count", 0) > self.active.get("intervention_baseline", 0)):
                    self.active["intervened"] = True
                    atomic_json(self.directory / "active.json", self.active)
        except Exception as error:
            with self.lock:
                self.cached = {"phase": "error", "detail": str(error)}
        finally:
            self.observed_at = time.time()

    def refresh_catalog(self):
        # Checkpoint files may live on a busy external disk. Never inspect them
        # while answering a status request or holding the lifecycle lock.
        models = self.model_provider()
        with self.lock:
            self.models = models
            self.catalog_observed_at = time.monotonic()

    def status(self):
        # In-memory only: a slow disk operation must not block the UI heartbeat.
        acquired = self.lock.acquire(blocking=False)
        try:
            if not acquired:
                return {**self.last_status, "phase": self.last_status.get("phase", "checking"), "status_stale": True}
            state = dict(self.cached)
            age = time.time() - self.observed_at if self.observed_at else None
            session = state.get("session") or {}
            models = [dict(model) for model in self.models]
            for model in models:
                if model.get("publication_tracked") and model.get("id") == (state.get("model") or {}).get("id"):
                    model["last_inference_actor_version"] = session.get("actor_version")
                    model["inference_episode_id"] = session.get("episode_id")
            collection_session = (session.get("session_use") == "collection" and session.get("phase") not in ("disarmed", "stopped")) if session else self.collection_session
            self.last_status = {**state, "session_active": collection_session, "operation": self.operation, "error": self.error,
                    "data_root": self.settings.get("data_root") or str(self.allowed_root / "evaluations"),
                    "default_data_root": str(self.allowed_root / "evaluations/test"),
                    "read_only": os.environ.get("COBOT_READ_ONLY") == "1",
                    "recent_data_roots": self.settings.get("recent_data_roots", []),
                    "active": dict(self.active) if self.active else None,
                    "selected_model": self.settings.get("model_id"), "models": models,
                    "observed_at": self.observed_at, "status_age_sec": age,
                    "status_stale": age is None or age > 5}
            return dict(self.last_status)
        finally:
            if acquired:
                self.lock.release()

    def submit(self, operation, model_id=None, trial_id=None):
        with self.lock:
            if self.operation:
                raise DeploymentError("上一操作正在执行：" + self.operation)
            if operation in ("success", "failure", "abort", "pause", "resume") and (not self.active or self.active["id"] != trial_id):
                raise DeploymentError("评估轮次已变化，请刷新状态")
            self.operation = operation
            self.error = None
        def run():
            try:
                self.perform(operation, model_id=model_id)
            except Exception as error:
                with self.lock:
                    self.error = str(error)
            finally:
                self.refresh()
                with self.lock:
                    self.operation = None
        threading.Thread(target=run, daemon=True, name="deployment-" + operation).start()
        return self.status()

    def frames(self, folder, prefix, required=True):
        try:
            for _ in range(3):
                state = self.cameras.state()
                if state.get("status") != "ready":
                    raise DeploymentError("三相机未就绪：" + str(state.get("status")))
                try:
                    images = [(key, self.cameras.image(key, generation=state["generation"])) for key in ("camera_left", "camera_high", "camera_right")]
                except RuntimeError:
                    continue
                folder.mkdir(parents=True, exist_ok=True)
                for key, data in images:
                    (folder / (prefix + "_" + key + ".jpg")).write_bytes(data)
                return {"generation": state["generation"], "skew_ms": state.get("skew_ms"), "files": [prefix + "_" + key + ".jpg" for key, _ in images]}
            raise DeploymentError("首尾帧同步切换超时")
        except Exception as error:
            if required:
                raise
            return {"files": [], "error": str(error)}

    def discard_active(self):
        """Remove this trial's frames/result; no aborted record or tombstone."""
        with self.lock:
            if not self.active:
                return
            record = self.active
            name = str(record["id"])
            root = self.root(record["data_root"])
            folder = root / name
            if not re.fullmatch(r"eval-\d{8}T\d{6}-[0-9a-f]{8}", name) or folder.is_symlink() or folder.resolve().parent != root:
                raise DeploymentError("评估目录不匹配，未清除")
            if folder.exists():
                shutil.rmtree(folder)
            # Remove the persisted active identity as well as the history entry.
            (self.directory / "active.json").unlink(missing_ok=True)
            self.active = None

    def perform(self, operation, model_id=None):
        if operation in {'load', 'start', 'collection_session_start'}:
            try:
                require_storage(self.allowed_root, write=True)
            except OSError as error:
                raise DeploymentError(str(error)) from error
        if operation in {"collection_session_start", "collection_session_stop"}:
            if self.busy() or self.active:
                raise DeploymentError("请先结束当前 Episode")
            state = self.runtime.status()
            stopping_fault = (operation == "collection_session_stop"
                              and state.get("model", {}).get("kind") == "rlt"
                              and state.get("session", {}).get("phase") == "fault"
                              and state.get("session", {}).get("policy_paused") is True)
            if state.get("phase") not in {"ready", "paused"} and not stopping_fault:
                raise DeploymentError("请先加载采集模型并结束当前 Episode")
            if state.get("model", {}).get("kind") == "rlt":
                starting = operation == "collection_session_start"
                if starting:
                    self.runtime.select_use("collection")
                self.runtime._session_action("/api/session/prepare" if starting else "/api/session/stop")
            else:
                self.runtime.action("pause")
            self.collection_session = operation == "collection_session_start"
            return
        if operation == "load":
            if self.busy() or self.active:
                raise DeploymentError("请先结束当前采集或评估轮次")
            model = next((m for m in self.model_provider() if m["id"] == model_id), None)
            if not model or not model["available"]:
                raise DeploymentError((model or {}).get("unavailable_reason") or "所选模型不可用")
            current = self.runtime.status()
            if (current.get("model", {}).get("id") == model_id
                    and current.get("phase") in {"loading", "ready", "paused"}):
                return  # Reuse the shared process; never reload the same weights.
            self.runtime.load(model)
            self.collection_session = False
            self.settings["model_id"] = model_id
            atomic_json(self.directory / "settings.json", self.settings)
            return
        if operation == "unload":
            if self.busy():
                raise DeploymentError("请先保存或放弃当前采集 Episode")
            self.collection_session = False
            if self.active:
                try:
                    self.perform("abort")
                except Exception as error:
                    # A faulted Session may reject terminal requests. Still allow
                    # the owned inference processes to be stopped before cleanup.
                    self.runtime.unload()
                    self.discard_active()
                    return
            return self.runtime.unload()
        if operation == "start":
            if self.active or self.busy():
                raise DeploymentError("已有采集或评估轮次")
            state = self.runtime.status()
            if state["phase"] not in ("ready", "paused"):
                raise DeploymentError("请先等待模型加载成功")
            if state.get("model", {}).get("training_enabled"):
                raise DeploymentError("在线更新已启用；评测请选择同路径的冻结模型")
            select_use = getattr(self.runtime, "select_use", None)
            if select_use:
                select_use("evaluation")
            self.collection_session = False
            folder = self.trial_root(state["model"]) / ("eval-" + time.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
            record = {"id": folder.name, "data_root": str(folder.parent), "model": state["model"],
                      "started_at": time.time(), "outcome": None, "intervened": False, "intervention_baseline": state.get("intervention_count", 0),
                      "start": self.frames(folder, "start")}
            atomic_json(folder / "result.json", record)
            with self.lock:
                self.active = record
                atomic_json(self.directory / "active.json", record)
            try:
                self.runtime.action("start")
            except Exception as start_error:
                try:
                    self.runtime.action("pause")
                except Exception as pause_error:
                    record["pause_error"] = str(pause_error)
                record.update(outcome="start_failed", ended_at=time.time())
                atomic_json(folder / "result.json", record)
                with self.lock:
                    self.active = None
                    atomic_json(self.directory / "active.json", {})
                raise start_error
            return
        if operation in ("pause", "resume"):
            if not self.active:
                raise DeploymentError("尚未开始评估")
            return self.runtime.action(operation)
        if operation in ("success", "failure", "abort"):
            if not self.active:
                raise DeploymentError("尚未开始评估")
            state = self.runtime.status()
            if state["phase"] == "offline" or (state["phase"] == "error" and not self.runtime._alive(state)):
                if operation != "abort":
                    raise DeploymentError("模型已退出，请放弃本轮后重新加载")
            else:
                self.runtime.action("pause")
            record = dict(self.active)
            folder = Path(record["data_root"]) / record["id"]
            if operation != "abort":
                record["end"] = self.frames(folder, "end", required=False)
            if state["phase"] not in ("offline", "error"):
                self.runtime.action(operation)
            if operation == "abort":
                self.discard_active()
                return
            record.update(outcome=operation, ended_at=time.time())
            atomic_json(folder / "result.json", record)
            with self.lock:
                self.active = None
                atomic_json(self.directory / "active.json", {})
            return
        raise DeploymentError("未知部署操作")

    def collection_action(self, path, body=None):
        with self.lock:
            if self.operation or self.active:
                raise DeploymentError("请先结束当前部署操作或评测轮次")
            self.operation = "collection"
            self.error = None
        try:
            response = self.runtime.collection_action(path, body)
            if response.status < 400:
                if path in {"/api/session/prepare", "/api/session/start", "/api/episode/next"}:
                    self.collection_session = True
                elif path == "/api/session/stop":
                    self.collection_session = False
            return response
        finally:
            self.refresh()
            with self.lock:
                self.operation = None

    def records(self, model_id=None):
        rows = []
        root = self.evaluation_root()
        for path in root.rglob("eval-*/result.json"):
            if root not in path.resolve().parents:
                continue
            item = read_json(path)
            if item:
                item["data_root"] = str(path.parent.parent)
            if item.get("outcome") == "abort":
                continue
            if item and (not model_id or item.get("model", {}).get("id") == model_id):
                for endpoint in ("start", "end"):
                    item.setdefault(endpoint, {})["urls"] = ["/api/deployment/frame?" + urlencode({"record_id": item["id"], "name": name, "data_root": str(path.parent.parent)}) for name in item.get(endpoint, {}).get("files", [])]
                rows.append(item)
        rows.sort(key=lambda item: (float(item.get("started_at") or 0), item.get("id", "")), reverse=True)
        decided = [r for r in rows if r.get("outcome") in ("success", "failure")]
        success = sum(r["outcome"] == "success" for r in decided)
        autonomous = [r for r in decided if not r.get("intervened")]
        return {"records": rows, "total": len(decided), "success": success, "failure": len(decided)-success,
                "rate": success / len(decided) if decided else None, "aborted": sum(r.get("outcome") == "abort" for r in rows),
                "autonomous_rate": sum(r["outcome"] == "success" for r in autonomous) / len(autonomous) if autonomous else None}


class DeploymentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    model_id: Optional[str] = None
    trial_id: Optional[str] = None


class DeploymentStorage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_root: str
    reset_on_refresh: bool = False


def install_routes(app, cameras, modes, devices):
    manager = DeploymentManager(cameras, busy=lambda: modes.snapshot().active_mode is not None)
    app.state.deployment_manager = manager
    from .collection_model import install_routes as install_collection_routes
    install_collection_routes(app, manager)
    stop = threading.Event()

    # The recorder already owns a custom lifespan. on_event handlers are not
    # invoked in that setup; compose with it so monitoring actually starts.
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        stop.clear()
        def monitor():
            while not stop.is_set():
                manager.refresh()
                stop.wait(.75)
        def catalogs():
            while not stop.is_set():
                try:
                    manager.refresh_catalog()
                except Exception:
                    pass  # Keep the last catalog; load revalidates the choice.
                stop.wait(30)
        async with original_lifespan(application):
            threading.Thread(target=monitor, daemon=True, name="deployment-status").start()
            threading.Thread(target=catalogs, daemon=True, name="deployment-catalog").start()
            try:
                yield
            finally:
                stop.set()  # Models are owned independently of the web service.

    app.router.lifespan_context = lifespan

    @app.get("/api/deployment/status")
    async def status():
        return manager.status()

    @app.post("/api/deployment/action")
    def action(request: DeploymentAction):
        if request.action not in {"load", "unload", "start", "pause", "resume", "success", "failure", "abort"}:
            raise HTTPException(422, "unknown_deployment_action")
        try:
            return manager.submit(request.action, request.model_id, request.trial_id)
        except DeploymentError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/deployment/storage")
    def storage(request: DeploymentStorage):
        try:
            return manager.save_settings(request.data_root, reset_on_refresh=request.reset_on_refresh)
        except (DeploymentError, OSError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/deployment/records")
    def records(model_id: str = ""):
        return manager.records(model_id or None)

    @app.get("/api/deployment/frame")
    def frame(record_id: str, name: str, data_root: str):
        if not record_id.startswith("eval-") or Path(record_id).name != record_id or name not in {
            prefix + "_" + key + ".jpg" for prefix in ("start", "end") for key in ("camera_left", "camera_high", "camera_right")
        }:
            raise HTTPException(400, "invalid_frame")
        try:
            path = manager.root(data_root) / record_id / name
            if manager.allowed_root not in path.resolve().parents or not path.is_file():
                raise HTTPException(404, "frame_not_found")
            return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400, immutable"})
        except DeploymentError as error:
            raise HTTPException(400, str(error)) from error

    from .task_outputs import install_output_routes
    install_output_routes(app, manager, devices)
    from .site_options import install_routes as install_site_routes
    install_site_routes(app, manager, devices)

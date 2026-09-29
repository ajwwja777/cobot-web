import os
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from cobot_console.deployment import DeploymentManager, DeploymentError, catalog
from cobot_console.evaluation_env import EvaluationEpisodeLifecycle, prepare_config


MODEL = dict(id="fixed", label="Fixed", available=True, kind="rlt", checkpoint="/fixed.pkl")

class Runtime:
    def __init__(self):
        self.phase="offline"
        self.calls=[]
        self.count=0
        self.fail=set()
    def status(self):
        return dict(phase=self.phase,model=MODEL,intervention_count=self.count)
    def load(self, model):
        self.calls.append("load")
        self.phase="ready"
    def action(self, action):
        self.calls.append(action)
        if action in self.fail:
            raise DeploymentError("injected "+action)
        self.phase="running" if action in ("start","resume") else "paused"
    def unload(self):
        self.calls.append("unload")
        self.phase="offline"
    def _alive(self, state):
        return self.phase!="offline"

class Cameras:
    def __init__(self):
        self.ready=True
    def state(self):
        return dict(status="ready" if self.ready else "unavailable",generation=12,skew_ms=8)
    def image(self, key, generation):
        assert generation==12
        return b"jpeg-test"

@pytest.fixture
def manager(tmp_path):
    return DeploymentManager(Cameras(),runtime=Runtime(),directory=tmp_path/"runtime",allowed_root=tmp_path/"data",model_provider=lambda:[MODEL])

def test_load_does_not_start_and_storage_roundtrip(manager):
    manager.perform("load","fixed")
    assert manager.runtime.calls==["load"]
    assert manager.active is None
    root=manager.allowed_root/"comparison"
    manager.save_settings(str(root))
    assert root.is_dir()
    restored=DeploymentManager(manager.cameras,runtime=manager.runtime,directory=manager.directory,allowed_root=manager.allowed_root,model_provider=lambda:[MODEL])
    assert restored.root()==root
    with pytest.raises(DeploymentError):
        manager.save_settings(str(manager.allowed_root.parent/"outside"))

def test_evaluate_only_frames_results_and_separate_assisted_rate(manager):
    manager.perform("load","fixed")
    manager.perform("start")
    assert manager.runtime.calls==["load","start"]
    first=manager.active["id"]
    manager.perform("success")
    assert manager.runtime.calls[-2:]==["pause","success"]
    manager.perform("start")
    manager.runtime.count+=1
    manager.refresh()
    manager.perform("failure")
    manager.perform("start")
    manager.perform("abort")
    result=manager.records("fixed")
    assert (result["total"],result["success"],result["failure"],result["aborted"])==(2,1,1,0)
    assert result["rate"]==.5 and result["autonomous_rate"]==1.0
    files=list(manager.root().rglob("*"))
    assert len(list(manager.root().rglob("*.jpg")))==12
    assert not [p for p in files if p.suffix in {".hdf5",".h5",".pkl",".mp4"}]
    assert manager.records("unknown")["total"]==0
    with pytest.raises(DeploymentError):
        manager.submit("success",trial_id=first)

def test_no_camera_no_start_and_missing_end_still_saves_result(manager):
    manager.perform("load","fixed")
    manager.cameras.ready=False
    with pytest.raises(DeploymentError): manager.perform("start")
    assert manager.runtime.calls==["load"]
    manager.cameras.ready=True
    manager.perform("start")
    manager.cameras.ready=False
    manager.perform("failure")
    result=manager.records()["records"][0]
    assert result["outcome"]=="failure" and result["end"]["error"]


def test_abort_does_not_capture_an_end_frame_or_keep_any_trial_files(manager):
    manager.perform("load", "fixed")
    manager.perform("start")
    trial = manager.active['id']
    def no_camera():
        raise AssertionError('abort must not capture any frames')
    manager.cameras.state = no_camera
    manager.perform("abort")
    assert manager.active is None
    assert not (manager.root()/trial).exists()
    assert not (manager.directory/'active.json').exists()
    assert manager.records()['records'] == []


def test_failed_abort_keeps_active_for_retry_instead_of_claiming_success(manager):
    manager.perform("load", "fixed")
    manager.perform("start")
    manager.runtime.fail = {'abort'}
    with pytest.raises(DeploymentError):
        manager.perform("abort")
    assert manager.active is not None
    manager.runtime.fail.clear()
    manager.perform("abort")
    assert manager.active is None
    assert manager.records()['records'] == []

def test_start_failure_attempts_pause_and_clears_active(manager):
    manager.perform("load","fixed")
    manager.runtime.fail={"start","pause"}
    with pytest.raises(DeploymentError,match="injected start"): manager.perform("start")
    assert manager.active is None
    assert manager.records()["records"][0]["outcome"]=="start_failed"

def test_busy_and_double_submit_rejected(manager):
    manager.busy=lambda:True
    with pytest.raises(DeploymentError): manager.perform("load","fixed")
    manager.busy=lambda:False
    manager.operation="load"
    with pytest.raises(DeploymentError): manager.submit("load","fixed")

def test_faulted_session_can_release_without_counting_failure(manager):
    manager.perform("load","fixed");manager.perform("start")
    manager.runtime.fail={"pause"}
    manager.perform("unload")
    assert manager.runtime.phase=="offline" and manager.active is None
    assert manager.records()["records"]==[]
    assert not list(manager.root().rglob("*.jpg"))
    assert not (manager.directory/"active.json").exists()
    assert manager.records()["total"]==0

def test_http_routes_and_outputs_preserve_previous_logs(manager,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cobot_console.deployment import install_routes
    log=tmp_path/"home-123.log";log.write_text("homed successfully")
    devices=SimpleNamespace(runtime=tmp_path,status=lambda:{"jobs":{"home":{"phase":"invalid"}}})
    app=FastAPI()
    install_routes(app,manager.cameras,SimpleNamespace(snapshot=lambda:SimpleNamespace(active_mode=None)),devices)
    # Closures reference the installed manager; adapt its runtime and paths.
    live=app.state.deployment_manager
    live.directory=manager.directory;live.allowed_root=manager.allowed_root;live.runtime=manager.runtime;live.model_provider=lambda:[MODEL]
    with TestClient(app) as client:
        assert client.get("/api/deployment/status").json()["phase"]=="offline"
        assert client.post("/api/deployment/action",json={"action":"shell"}).status_code==422
        assert client.post("/api/deployment/action",json={"action":"success","trial_id":"stale"}).status_code==409
        assert client.post("/api/deployment/storage",json={"data_root":str(tmp_path/"outside")}).status_code==409
        out=client.get("/api/console/outputs").json()
        home=next(j for j in out["tasks"] if j["component"]=="home")
        assert home["phase"]=="previous" and home["log_tail"]=="homed successfully"
        assert client.get("/api/deployment/frame",params={"record_id":"../bad","name":"start_camera_left.jpg","data_root":str(manager.allowed_root)}).status_code==400


def test_monitor_composes_with_existing_recorder_lifespan(manager, tmp_path):
    from contextlib import asynccontextmanager
    import time
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from cobot_console.deployment import install_routes
    events = []
    @asynccontextmanager
    async def recorder_lifespan(app):
        events.append("start")
        yield
        events.append("stop")
    app = FastAPI(lifespan=recorder_lifespan)
    install_routes(app, manager.cameras, SimpleNamespace(snapshot=lambda:SimpleNamespace(active_mode=None)),
                   SimpleNamespace(runtime=tmp_path,status=lambda:{"jobs":{}}))
    live = app.state.deployment_manager
    live.runtime = manager.runtime
    live.model_provider = lambda:[MODEL]
    with TestClient(app) as client:
        for phase in ("loading", "ready", "running", "paused"):
            manager.runtime.phase = phase
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = client.get("/api/deployment/status").json()
                if state["phase"] == phase and state["models"] == [MODEL]:
                    break
                time.sleep(.025)
            assert state["phase"] == phase
            assert not state["status_stale"]
        assert events == ["start"]
    assert events == ["start", "stop"]
    assert not manager.runtime.calls  # The monitor issues no robot/model actions.


def test_status_is_nonblocking_and_performs_no_filesystem_checks(manager, monkeypatch):
    import time
    manager.refresh_catalog()
    manager.refresh()
    def forbidden(*args, **kwargs):
        raise AssertionError("status must use memory only")
    monkeypatch.setattr(manager, "root", forbidden)
    monkeypatch.setattr(manager, "model_provider", forbidden)
    state = manager.status()
    assert state["models"] == [MODEL] and not state["status_stale"]
    locked, release = threading.Event(), threading.Event()
    def hold_lock():
        with manager.lock:
            locked.set()
            release.wait(2)
    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert locked.wait(1)
    try:
        start = time.monotonic()
        assert manager.status()["status_stale"] is True
        assert time.monotonic() - start < .1
    finally:
        release.set()
        thread.join()
    manager.observed_at -= 6
    assert manager.status()["status_stale"] is True

def mock_assets(monkeypatch, tmp_path):
    from cobot_console import deployment, paths
    root=tmp_path/"rlt";run=root/"outputs/rlt/plug_v3_yyshadow"
    actor=root/"models/rlt/plug_v3_yyshadow/warmup-5000/actor_snapshot/actor_snapshot.pkl"
    actor.parent.mkdir(parents=True);actor.write_bytes(b"test existence only")
    (actor.parent.parent/"action_norm_stats.json").write_text("{}")
    base=root/"base";(base/"params").mkdir(parents=True)
    manifest=root/"configs/rlt/plug_v3_yyshadow/manifest.json";manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"checkpoint":str(base)}))
    monkeypatch.setattr(deployment,"RLT",root);monkeypatch.setattr(deployment,"RUN",run)
    monkeypatch.setattr(paths,"RLT",root)
    from integrations.cobot_runtime import paths as rl_paths
    monkeypatch.setattr(rl_paths, "RLT", root)
    monkeypatch.setattr(rl_paths, "RLT_WARMUP", actor.parent.parent)
    monkeypatch.setattr(deployment,"RLT_WARMUP",actor.parent.parent)
    model_root = actor.parent.parent.parent
    monkeypatch.setattr(deployment,"RLT_MODELS",model_root)
    historical = model_root / "history/candidates/experts120_20k_20260925"
    (historical / "actor_snapshot").mkdir(parents=True)
    (historical / "actor_snapshot/actor_snapshot.pkl").write_bytes(b"fixture")
    (historical / "action_norm_stats.json").write_text("{}")
    for attr,name,step in [("LEGACY","pi05",2000),("DAGGER","pi05-dagger",3000)]:
        entry=tmp_path/name;(entry/("checkpoints/step_"+str(step))).mkdir(parents=True)
        (entry/"run_checkpoint_rtc_task2.sh").write_text("# fixture only")
        monkeypatch.setattr(deployment,attr,entry)
        checkpoint_attr = "PI05_DAGGER_CHECKPOINT" if attr == "DAGGER" else "PI05_CHECKPOINT"
        monkeypatch.setattr(deployment,checkpoint_attr,entry/("checkpoints/step_"+str(step)))
    config=root/"configs/rlt/plug_v3_yyshadow/online_rl_frozen.yaml";config.parent.mkdir(parents=True,exist_ok=True)
    import yaml
    config.write_text(yaml.safe_dump({"experiment":{"rl":{"warmup_q_weight":.1,"warmup_bc_weight":10}},
        "runtime":{key:{} for key in ["actor_service","learner_service","replay","env_driver","monitoring"]}}))

def test_fixed_catalog_dagger_lineage(monkeypatch, tmp_path):
    mock_assets(monkeypatch, tmp_path)
    models={m["id"]:m for m in catalog()}
    historical=models["plug-v3-warmup-20k"]
    assert historical["mode"] == "frozen" and historical["actor_version"] == 10000
    assert historical["adapter_id"] == "plug-v3-warmup-5k"
    assert historical["training_enabled"] is False
    dagger=models["pi05-in-the-pot-dagger"]
    assert dagger["checkpoint"].endswith("checkpoints/step_3000")
    assert dagger["base_checkpoint"].endswith("pi05/checkpoints/step_2000")
    assert all(models[key]["available"] for key in ["plug-v3-reference", "plug-v3-warmup-5k", "plug-v3-warmup-20k", "pi05-in-the-pot", "pi05-in-the-pot-dagger"])
    assert "models/rlt/plug_v3_yyshadow/warmup-5000/" in models["plug-v3-warmup-5k"]["checkpoint"]

def test_config_pins_snapshot_and_isolates_replay(tmp_path, monkeypatch):
    mock_assets(monkeypatch, tmp_path)
    import yaml
    models={m["id"]:m for m in catalog()}
    for mode,id in [("frozen","plug-v3-warmup-5k"),("reference","plug-v3-reference")]:
        target=tmp_path/(mode+".yaml")
        prepare_config(mode,models[id]["checkpoint"],target)
        cfg=yaml.safe_load(target.read_text())
        assert Path(cfg["runtime"]["actor_service"]["snapshot_path"]).is_file()
        assert cfg["runtime"]["env_driver"]["actor_deterministic"] is True
        assert "models/rlt/plug_v3_yyshadow/warmup-5000/action_norm_stats.json" in cfg["experiment"]["rl"]["action_norm_stats_path"]
        assert Path(cfg["runtime"]["replay"]["journal_path"]).parent==tmp_path
        assert cfg["experiment"]["rl"]["warmup_q_weight"]==.1
        assert cfg["experiment"]["rl"]["warmup_bc_weight"]==10

def _session_case():
    from methods.openpi_rlt.cobot_adapter.session import RltSessionController
    from methods.openpi_rlt.cobot_adapter.session_http import RltSessionApplication,SessionHooks
    from methods.openpi_rlt.cobot_adapter.task5_client import Task5EpisodeIdentity
    from methods.openpi_rlt.cobot_adapter.trace import EpisodeOutcome
    calls=[]
    lifecycle=EvaluationEpisodeLifecycle()
    identity=Task5EpisodeIdentity("eval","model","fixed","eval","/unused",100)
    app=RltSessionApplication(RltSessionController(),lifecycle,identity_factory=lambda _:identity,hooks=SessionHooks(is_policy_mode=lambda:True,set_policy_paused=lambda paused:calls.append(paused),submit_outcome=lambda x:None,signal_episode_ready=lambda:None,request_front_home=lambda:None,signal_policy_armed=lambda:None))
    def token():
        s=app.status()
        return dict(episode_id=s["episode_id"],generation=s["generation"])
    app.arm(**token());app.start(**token())
    assert lifecycle.active and calls[-1] is False
    app.pause(**token());app.update_takeover(left=False,right=True)
    assert app.status()["phase"]=="hil"
    app.update_takeover(left=False,right=False)
    assert app.status()["phase"]=="paused" # operator pause survives HIL
    app.terminal(EpisodeOutcome.SUCCESS,home_after_terminal=False,**token())
    app.mark_replay_finalized()
    assert app.status()["phase"]=="waiting_scene" and not lifecycle.active
    app.next_episode(**token())
    assert lifecycle.index==2

@pytest.mark.skipif(not os.environ.get("COBOT_RLT_TEST_PYTHON"), reason="Set COBOT_RLT_TEST_PYTHON for algorithm integration")
def test_memory_lifecycle_with_real_session():
    import inspect
    import subprocess
    code="from cobot_console.evaluation_env import EvaluationEpisodeLifecycle\n"+inspect.getsource(_session_case)+"\n_session_case()\n"
    result=subprocess.run([os.environ["COBOT_RLT_TEST_PYTHON"],"-c",code],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr

def test_release_owned_orphan_process_group(tmp_path):
    import subprocess,time,os,signal
    from cobot_console.deployment import ManagedRuntime,atomic_json,process_identity
    runtime=ManagedRuntime(tmp_path)
    # A disposable child, no ROS imports, services or hardware handles.
    process=subprocess.Popen([sys.executable,"-c","import subprocess,time;subprocess.Popen(['sleep','25']);time.sleep(.3)"],start_new_session=True)
    state={"pid":process.pid,"start_ticks":process_identity(process.pid),"model":{"kind":"pi05"},"phase":"loading","started_at":time.time()}
    atomic_json(runtime.registry,state)
    try:
        process.wait(timeout=3)
        assert not runtime._alive(state) and runtime._owned_members(state)
        runtime.unload()
        assert not runtime._owned_members(state)
        assert runtime.status()["phase"]=="offline"
    finally:
        if runtime._owned_members(state):os.killpg(process.pid,signal.SIGTERM)

def test_legacy_manual_pause_survives_hil(monkeypatch,tmp_path):
    import importlib.util
    class Gate:
        def __init__(self):self._lock=threading.RLock();self.paused=True
        def handle_set_paused(self,request):
            self.paused=request.data
            return SimpleNamespace(success=True,message="paused" if self.paused else "fresh resume")
    monkeypatch.setitem(sys.modules,"inference_pi05_rtc_task2",SimpleNamespace(Task2PauseGate=Gate))
    monkeypatch.setenv("COBOT_PI05_CLIENT_ROOT",str(tmp_path))
    monkeypatch.setenv("COBOT_PI05_GATE_STATE",str(tmp_path/"gate.json"))
    import cobot_console
    script=Path(cobot_console.__file__).parent/"deployment_pi05_client.py"
    spec=importlib.util.spec_from_file_location("gate_under_test",script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    gate=module.ConsolePauseGate()
    def send(value,caller):
        result = gate.handle_set_paused(SimpleNamespace(data=value,_connection_header={"callerid":caller}))
        assert result.success
        return gate.paused
    assert send(False,"/cobot_deployment_command_1") is False
    assert send(True,"/task2_teach_button_handover") is True
    assert send(False,"/task2_teach_button_handover") is False
    assert send(True,"/cobot_deployment_command_2") is True
    assert send(True,"/task2_teach_button_handover") is True
    assert send(False,"/task2_teach_button_handover") is True
    assert json.loads((tmp_path/"gate.json").read_text())["intervention_count"]==2


def test_registered_evaluations_are_grouped_and_media_uses_actual_parent(manager, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    monkeypatch.setitem(MODEL, "id", "plug-v3-warmup-5k")
    manager.perform("load", MODEL["id"])
    manager.perform("start")
    first = dict(manager.active)
    folder = Path(first["data_root"]) / first["id"]
    expected = manager.allowed_root / "evaluations/plug_insertion/rl-platform/rlt/warmup_5000"
    assert expected in folder.parents
    manager.perform("success")
    # Preserve historical provenance in a migrated file; the reader must use its new location.
    record = json.loads((folder / "result.json").read_text())
    record["data_root"] = "/retired/data/evaluations"
    (folder / "result.json").write_text(json.dumps(record))
    result = manager.records()
    assert result["total"] == 1 and result["success"] == 1
    item = result["records"][0]
    assert item["data_root"] == str(folder.parent)
    url = item["start"]["urls"][0]
    assert parse_qs(urlparse(url).query)["data_root"] == [str(folder.parent)]
    # A different model always gets its own group even after selecting an older model folder.
    manager.save_settings(str(folder.parent))
    other = manager.trial_root({"id": "plug-v3-reference"})
    assert manager.allowed_root / "evaluations/plug_insertion/rl-platform/rlt/reference_4999" in other.parents
    assert "warmup_5000" not in other.parts

def test_grouped_abort_removes_trial_without_leaving_result(manager, monkeypatch):
    monkeypatch.setitem(MODEL, "id", "plug-v3-warmup-5k")
    manager.perform("load", MODEL["id"])
    manager.perform("start")
    folder = Path(manager.active["data_root"]) / manager.active["id"]
    manager.perform("abort")
    assert not folder.exists()
    assert manager.records()["total"] == 0

def test_refresh_default_preserves_recent_paths_and_active_trial(manager):
    previous = manager.allowed_root / "evaluations/plug"
    test_root = manager.allowed_root / "evaluations/test"
    manager.save_settings(str(previous))
    manager.save_settings(str(test_root), reset_on_refresh=True)
    assert manager.root() == test_root
    assert str(previous) in manager.status()["recent_data_roots"]
    assert manager.status()["default_data_root"] == str(test_root)
    manager.save_settings(str(previous))
    manager.perform("load", "fixed")
    manager.perform("start")
    trial = dict(manager.active)
    with pytest.raises(DeploymentError):
        manager.save_settings(str(test_root), reset_on_refresh=True)
    assert manager.root() == previous and manager.active == trial


@pytest.mark.parametrize("reason", ["operation", "recording", "running"])
def test_refresh_never_changes_storage_during_another_operation(manager, reason):
    previous = manager.root()
    if reason == "operation":
        manager.operation = "start"
    elif reason == "recording":
        manager.busy = lambda: True
    else:
        manager.cached = {"phase": "running"}
    with pytest.raises(DeploymentError):
        manager.save_settings(str(manager.allowed_root / "evaluations/test"), reset_on_refresh=True)
    assert manager.root() == previous

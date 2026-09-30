from pathlib import Path
import pytest
from cobot_console.deployment import ManagedRuntime, DeploymentError, atomic_json
from tests.test_recorder_recovery import fixture

def test_dead_driver_reports_retained_stage1_not_loaded_runtime(tmp_path,monkeypatch):
    import cobot_console.deployment as module
    monkeypatch.setattr(module,"RLT",Path(__file__).resolve().parents[4]/"rl-platform")
    rt=ManagedRuntime(tmp_path)
    log=tmp_path/"failed.log"
    log.write_text("RTCActionStateError: actual delay exceeded predicted delay\n"
                   "RuntimeError: env_driver exited with code 1")
    atomic_json(rt.registry,{"pid":123,"start_ticks":1,"model":{"kind":"rlt"},"log_path":str(log)})
    monkeypatch.setattr(rt,"_alive",lambda state:False)
    monkeypatch.setattr(rt,"_owned_members",lambda state:[])
    monkeypatch.setattr(rt,"retained_stage1",lambda model:{"ready":True,"pid":999})
    state=rt.status()
    assert state["phase"]=="error" and state["stage1_ready"] and not state["model_ready"] and not state["process_started"]
    assert state["runtime_failure"]["code"]=="rtc_delay_exceeded"
    assert state["runtime_failure"]["recoverable"]

def test_restart_never_unloads_or_starts_policy(tmp_path,monkeypatch):
    rt=ManagedRuntime(tmp_path)
    atomic_json(rt.registry,{"pid":123,"start_ticks":1,"model":{"kind":"rlt","id":"mc30"}})
    monkeypatch.setattr(rt,"_alive",lambda state:False)
    monkeypatch.setattr(rt,"_owned_members",lambda state:[])
    monkeypatch.setattr(rt,"retained_stage1",lambda model:{"ready":True})
    calls=[]
    monkeypatch.setattr(rt,"load",lambda model:calls.append(model))
    monkeypatch.setattr(rt,"unload",lambda:pytest.fail("Must keep Stage1"))
    monkeypatch.setattr(rt,"action",lambda name:pytest.fail("Must not start policy"))
    rt.recover_runtime()
    assert calls==[{"kind":"rlt","id":"mc30","retain_stage1_required":True}]

@pytest.mark.parametrize("alive,members,retained",[(True,[],True),(False,[555],True),(False,[],False)])
def test_restart_rejects_existing_process_or_absent_model(tmp_path,monkeypatch,alive,members,retained):
    rt=ManagedRuntime(tmp_path)
    atomic_json(rt.registry,{"pid":123,"start_ticks":1,"model":{"kind":"rlt"}})
    monkeypatch.setattr(rt,"_alive",lambda state:alive)
    monkeypatch.setattr(rt,"_owned_members",lambda state:members)
    monkeypatch.setattr(rt,"retained_stage1",lambda model:{"ready":retained})
    monkeypatch.setattr(rt,"load",lambda model:pytest.fail("No restart"))
    with pytest.raises(DeploymentError):rt.recover_runtime()

def test_http_recovery_keeps_model_and_is_manual_start(tmp_path,monkeypatch):
    client,backend,recorder,manager=fixture(tmp_path,monkeypatch)
    manager.runtime.status=lambda:{"phase":"error","runtime_failure":{"recoverable":True}}
    calls=[]
    manager.runtime.recover_runtime=lambda:calls.append("recover") or {"phase":"loading"}
    result=client.post("/api/rlt/recover-runtime")
    assert result.status_code==200,result.text
    assert result.json()["model_retained"] and result.json()["manual_start_required"]
    assert calls==["recover"] and backend.calls==[]
    assert manager.operation is None

@pytest.mark.parametrize("state",["recording","starting","stopping","error"])
def test_http_does_not_restart_active_or_unfinished_writer(tmp_path,monkeypatch,state):
    client,backend,recorder,manager=fixture(tmp_path,monkeypatch)
    manager.runtime.status=lambda:{"phase":"error","runtime_failure":{"recoverable":True}}
    manager.runtime.recover_runtime=lambda:pytest.fail("No restart")
    recorder.state=state
    assert client.post("/api/rlt/recover-runtime").status_code==409
    assert manager.operation is None and backend.calls==[]

def test_http_live_runtime_not_restarted(tmp_path,monkeypatch):
    client,backend,recorder,manager=fixture(tmp_path,monkeypatch)
    manager.runtime.status=lambda:{"phase":"ready"}
    assert client.post("/api/rlt/recover-runtime").status_code==409
    assert manager.operation is None


@pytest.mark.parametrize("committed",[False,True])
def test_orphan_writer_released_only_after_file_commit(tmp_path,committed):
    from fastapi.testclient import TestClient
    from capture_core.api import create_app
    from capture_core.labels import LabelStore
    from cobot_console.mode import RecorderModeCoordinator
    from tests.test_console_api import FakeRecorder
    modes=RecorderModeCoordinator(initial_mode="rlt")
    recorder=FakeRecorder()
    recorder.check_ready=lambda root:Path(root)
    app=create_app(recorder=recorder,label_store=LabelStore(tmp_path),
                   writer_coordinator=modes,require_previous_labels=False)
    client=TestClient(app)
    response=client.post("/api/episodes/start",json=dict(
        data_root=str(tmp_path),task_id="test",model_id="rlt",
        checkpoint_id="4999",dataset_round="online",storage_layout="flat"))
    assert response.status_code==200,response.text
    assert modes.snapshot().active_mode=="rlt"
    recorder.state="stopped"
    recorder.status=lambda:{"state":"stopped","publication_status":"committed" if committed else None}
    if committed:
        app.state.release_completed_writer()
        assert modes.snapshot().active_mode is None
    else:
        with pytest.raises(RuntimeError,match="pending_episode_finalization"):
            app.state.release_completed_writer()
        assert modes.snapshot().active_mode=="rlt"


def test_ui_shutdown_allows_only_completed_orphan_and_verifies_processes(monkeypatch):
    from cobot_console.ui_shutdown import completed_orphan
    from cobot_console.deployment import ManagedRuntime
    console={"active_mode":"rlt"}
    model={"phase":"error","model":{"kind":"rlt"},"pid":123,"start_ticks":1}
    recorder={"state":"stopped","publication_status":"committed","completion_state":"complete"}
    monkeypatch.setattr(ManagedRuntime,"_alive",lambda self,state:False)
    monkeypatch.setattr(ManagedRuntime,"_owned_members",lambda self,state:[])
    assert completed_orphan(console,model,recorder)
    for values in [{"state":"recording"},{"publication_status":"writing"},
                   {"completion_state":"pending"},{"writer_thread_alive":True},
                   {"acquisition_active":True}]:
        assert not completed_orphan(console,model,{**recorder,**values})
    assert not completed_orphan({"active_mode":"normal"},model,recorder)
    assert not completed_orphan(console,{**model,"status_stale":True},recorder)
    monkeypatch.setattr(ManagedRuntime,"_owned_members",lambda self,state:[555])
    assert not completed_orphan(console,model,recorder)

def test_explicit_general_recovery_stops_only_runtime_before_restart(tmp_path, monkeypatch):
    client, backend, recorder, manager = fixture(tmp_path, monkeypatch)
    manager.runtime.status = lambda: {"phase": "paused", "model": {"kind": "rlt"},
                                     "runtime_recovery_available": True}
    calls = []
    manager.runtime.stop_runtime_keep_model = lambda: calls.append("stop-owned-runtime")
    manager.runtime.recover_runtime = lambda: calls.append("restart") or {"phase": "loading"}
    response = client.post("/api/rlt/recover-runtime",
                           json={"restart_running": True, "finalize_pending": True})
    assert response.status_code == 200, response.text
    assert calls == ["stop-owned-runtime", "restart"]
    assert response.json()["manual_start_required"]
    assert backend.calls == []

@pytest.mark.parametrize("stuck", [False, True])
def test_recovery_finalizes_owned_recording_without_label_or_delete(tmp_path, stuck):
    from fastapi.testclient import TestClient
    from capture_core.api import create_app
    from capture_core.labels import LabelStore
    from cobot_console.mode import RecorderModeCoordinator
    from tests.test_console_api import FakeRecorder
    modes = RecorderModeCoordinator(initial_mode="rlt")
    recorder = FakeRecorder()
    recorder.check_ready = lambda root: Path(root)
    committed = []
    recorder.status = lambda: {"state": recorder.state,
        "publication_status": "committed" if committed else "open",
        "writer_thread_alive": stuck and bool(committed), "completion_state": "complete"}
    def stop():
        recorder.state = "stopped"
        committed.append(True)
    recorder.stop = stop
    app = create_app(recorder=recorder, label_store=LabelStore(tmp_path),
                     writer_coordinator=modes, require_previous_labels=False)
    client = TestClient(app)
    r = client.post("/api/episodes/start", json=dict(
        data_root=str(tmp_path), task_id="test", model_id="rlt",
        checkpoint_id="4999", dataset_round="online", storage_layout="flat"))
    assert r.status_code == 200, r.text
    if stuck:
        with pytest.raises(RuntimeError, match="recorder_worker_still_active"):
            app.state.finalize_for_runtime_recovery()
        assert modes.snapshot().active_mode == "rlt"
    else:
        result = app.state.finalize_for_runtime_recovery()
        assert result["recording_retained"]["training_label_added"] is False
        assert result["recording_retained"]["episode_uuid"]
        assert modes.snapshot().active_mode is None
    # This fixture never writes a file; recovery must not create labels/files.
    assert not list(tmp_path.rglob("*.labels.json"))

def test_general_recovery_never_releases_stage1_or_unrelated_processes(tmp_path, monkeypatch):
    import cobot_console.deployment as module
    rt = ManagedRuntime(tmp_path)
    atomic_json(rt.registry, {"pid": 123, "start_ticks": 1, "model": {"kind": "rlt"}})
    members = [123, 124]
    monkeypatch.setattr(rt, "_owned_members", lambda state: list(members))
    monkeypatch.setattr(rt, "retained_stage1", lambda model: {"ready": True, "pid": 999})
    monkeypatch.setattr(module.os, "getpgid", lambda pid: 999)
    calls = []
    def signal_group(pid, sig):
        calls.append((pid, sig))
        members.clear()
    monkeypatch.setattr(module.os, "killpg", signal_group)
    from cobot_console.rlt_proxy import RltBackendError
    class HungSession:
        def request(self, *args): raise RltBackendError("not responding")
    rt.backend = HungSession()
    monkeypatch.setattr(rt, "unload", lambda: pytest.fail("Must retain Stage1"))
    assert rt.stop_runtime_keep_model()["runtime_stopped"]
    assert len(calls) == 1 and calls[0][0] == 123

def test_incorrect_stage1_group_blocks_signals(tmp_path, monkeypatch):
    import cobot_console.deployment as module
    rt = ManagedRuntime(tmp_path)
    atomic_json(rt.registry, {"pid": 123, "start_ticks": 1, "model": {"kind": "rlt"}})
    monkeypatch.setattr(rt, "_owned_members", lambda state: [123])
    monkeypatch.setattr(rt, "retained_stage1", lambda model: {"ready": True, "pid": 999})
    monkeypatch.setattr(module.os, "getpgid", lambda pid: 123)
    monkeypatch.setattr(module.os, "killpg", lambda *args: pytest.fail("Must not signal shared Stage1 group"))
    from cobot_console.rlt_proxy import RltBackendError
    rt.backend = type("Hung", (), {"request": lambda *args: (_ for _ in ()).throw(RltBackendError("hung"))})()
    with pytest.raises(DeploymentError, match="not_separate"):
        rt.stop_runtime_keep_model()

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

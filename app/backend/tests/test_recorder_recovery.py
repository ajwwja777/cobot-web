"""Recovery must retain the model and never replay a motion/recording request."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from cobot_console.api import create_app
from cobot_console.rlt_proxy import BackendResponse
from capture_core.validation import PreflightError
from tests.test_console_api import FakeBridge, FakeRecorder, FakeSegmentedService, FakeRegistry, _fresh_cache

class Session:
    def __init__(self):
        self.value = dict(phase="fault", policy_paused=True, episode_id=2, generation=8,
                          task5_episode_uuid=None, fault_reason="task5_start_failed: recorder_not_ready")
        self.calls=[]
    def request(self, method, path, body=None):
        self.calls.append((method,path,body))
        if method=="POST":
            assert path=="/api/session/stop"
            assert body=={"episode_id":2,"generation":8}
            self.value.update(phase="stopped",generation=9)
        return BackendResponse(200,dict(self.value))

def fixture(tmp_path,monkeypatch):
    backend=Session();recorder=FakeRecorder()
    recorder.check_ready=lambda root:Path(root)
    recorder.recover_error=lambda:{"state":"idle"}
    app=create_app(cache=_fresh_cache(),bridge=FakeBridge(),recorder=recorder,
        segmented_service=FakeSegmentedService(),backend_client=backend,
        lifecycle_registry=FakeRegistry(),allowed_data_root=tmp_path,rlt_data_root=tmp_path,
        monotonic=lambda:10.)
    manager=app.state.deployment_manager
    manager.runtime.directory=tmp_path/"runtime"
    monkeypatch.setattr(manager,"refresh",lambda:None)
    return TestClient(app),backend,recorder,manager

def test_fault_reset_keeps_loaded_model_and_never_starts_policy(tmp_path,monkeypatch):
    client,backend,recorder,manager=fixture(tmp_path,monkeypatch)
    response=client.post("/api/rlt/recover-recorder",json={"reset_fault_session":True})
    assert response.status_code==200,response.text
    assert response.json()["model_retained"] and response.json()["preflight"]["status"]=="ok"
    assert [p for m,p,b in backend.calls if m=="POST"]==["/api/session/stop"]
    assert manager.operation is None and recorder.state=="idle"

@pytest.mark.parametrize("phase,paused,uuid",[
    ("rollout",False,None),("paused",True,"episode"),("finalizing",True,"episode"),
    ("fault",True,"episode"),("fault",False,None)])
def test_recovery_does_not_discard_pending_episode_or_interrupt_motion(tmp_path,monkeypatch,phase,paused,uuid):
    client,backend,_,manager=fixture(tmp_path,monkeypatch)
    backend.value.update(phase=phase,policy_paused=paused,task5_episode_uuid=uuid)
    r=client.post("/api/rlt/recover-recorder",json={"reset_fault_session":True})
    assert r.status_code==409
    assert not [p for m,p,b in backend.calls if m=="POST"]
    assert manager.operation is None

def test_no_fault_reset_without_explicit_option(tmp_path,monkeypatch):
    client,backend,_,_=fixture(tmp_path,monkeypatch)
    assert client.post("/api/rlt/recover-recorder",json={}).status_code==409
    assert not [p for m,p,b in backend.calls if m=="POST"]

def test_busy_writer_and_model_operation_are_not_reset(tmp_path,monkeypatch):
    client,backend,recorder,manager=fixture(tmp_path,monkeypatch)
    manager.operation="load"
    assert client.post("/api/rlt/recover-recorder",json={"reset_fault_session":True}).status_code==409
    assert manager.operation=="load"
    manager.operation=None
    recorder.state="recording"
    assert client.post("/api/rlt/recover-recorder",json={"reset_fault_session":True}).status_code==409
    assert not [p for m,p,b in backend.calls if m=="POST"]

def test_preflight_reports_storage_reason_without_start(tmp_path,monkeypatch):
    client,backend,recorder,_=fixture(tmp_path,monkeypatch)
    def fail(root):raise PreflightError("disk_space_low","free=10, required=20")
    recorder.check_ready=fail
    r=client.post("/api/rlt/recorder-check",json={"data_root":str(tmp_path)})
    assert r.status_code==200 and r.json()["preflight"]["error_code"]=="disk_space_low"
    assert recorder.state=="idle" and backend.calls==[]
    r=client.post("/api/rlt/recorder-check",json={"data_root":str(tmp_path.parent)})
    assert r.status_code==422 and recorder.state=="idle"

def test_preflight_http_retains_stream_names(tmp_path):
    from capture_core.api import create_app as recorder_app
    from capture_core.labels import LabelStore
    def failed(_request):raise PreflightError("streams_not_ready","front_left")
    recorder=FakeRecorder();recorder.start=failed
    client=TestClient(recorder_app(recorder=recorder,label_store=LabelStore(tmp_path)))
    r=client.post("/api/episodes/start",json=dict(task_id="test",model_id="model",checkpoint_id="step1",dataset_round="test"))
    assert r.status_code==503 and r.json()["detail"]=="recorder_not_ready: streams_not_ready: front_left"

def test_live_cache_clock_is_sampled_inside_lock_without_masking_real_future():
    from capture_core.ros_cache import LatestMessageCache
    cache=LatestMessageCache()
    # Callback arrives after the caller sampled t=10 but before snapshot lock.
    cache.put("front_left",{"position":[0]*7},10.001,10.001)
    assert cache.snapshot(10.0).is_fresh("front_left") is False
    def clock():
        assert cache._lock.locked()
        return 10.002
    assert cache.snapshot(clock).is_fresh("front_left") is True
    cache.put("front_left",{"position":[0]*7},1000,1000)
    assert cache.snapshot(clock).is_fresh("front_left") is False


def test_short_input_gap_waits_before_writer_and_does_not_retry_start(tmp_path):
    from capture_core.api import create_app as recorder_app
    from capture_core.labels import LabelStore
    calls=[]
    def readiness():
        calls.append("readiness")
        return {"status":"not_ready","error_code":"camera_stale","stale_keys":["camera_left"]} if len(calls)<3 else {"status":"ok"}
    recorder=FakeRecorder()
    original=recorder.start
    starts=[]
    def start(request):starts.append(request);return original(request)
    recorder.start=start
    client=TestClient(recorder_app(recorder=recorder,label_store=LabelStore(tmp_path),readiness_provider=readiness))
    r=client.post("/api/episodes/start",json=dict(task_id="test",model_id="model",checkpoint_id="step1",dataset_round="test"))
    assert r.status_code==200 and len(starts)==1
    assert len(calls)==3

def test_faulted_session_can_end_without_unloading(tmp_path):
    from cobot_console.deployment import DeploymentManager
    calls=[]
    class Runtime:
        def status(self):return {"phase":"error","model":{"kind":"rlt"},"session":{"phase":"fault","policy_paused":True}}
        def _session_action(self,path):calls.append(path)
    manager=DeploymentManager(None,runtime=Runtime(),directory=tmp_path,allowed_root=tmp_path,model_provider=lambda:[])
    manager.perform("collection_session_stop")
    assert calls==["/api/session/stop"] and manager.collection_session is False

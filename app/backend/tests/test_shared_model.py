"""Shared model ownership and per-episode recording isolation; no robot required."""
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cobot_console.deployment import DeploymentError, ManagedRuntime, atomic_json
from cobot_console.shared_model_env import SharedEpisodeLifecycle, CollectionTrace, session_use
from tests.test_deployment_evaluation import manager


@dataclass(frozen=True)
class Identity:
    data_root: str


class Recorder:
    def __init__(self):
        self.calls = []
    def start_episode(self, identity):
        self.calls.append(("start", identity.data_root))
        return SimpleNamespace(identity=identity)
    def finish_episode(self, ref, outcome):
        self.calls.append(("finish", ref.identity.data_root, outcome))
        return ref
    def status(self):
        return {"state": "recording"}
    def set_capture_enabled(self, value):
        self.calls.append(("capture", value))
    def record_marker(self, kind):
        self.calls.append(("marker", kind))


def test_collection_latches_directory_and_use_until_replay_finishes(tmp_path):
    choice = {"use": "collection", "root": tmp_path / "first"}
    recorder = Recorder()
    lifecycle = SharedEpisodeLifecycle(recorder, read_use=lambda: choice["use"],
                                       root_for_phase=lambda phase: choice["root"])
    ref = lifecycle.start_episode(Identity("/unused"))
    choice.update(use="evaluation", root=tmp_path / "second")
    lifecycle.record_marker("pause")
    lifecycle.set_capture_enabled(False)
    lifecycle.finish_episode(ref, "failure")
    assert recorder.calls == [("start", str(tmp_path/"first")), ("marker", "pause"),
                              ("capture", False), ("finish", str(tmp_path/"first"), "failure")]
    assert lifecycle.collecting  # The replay callback still belongs to this episode.
    assert not (tmp_path/"second").exists()


def test_evaluation_never_calls_recorder_or_trace_after_collection(tmp_path):
    mode = ["collection"]
    recorder = Recorder()
    lifecycle = SharedEpisodeLifecycle(recorder, read_use=lambda: mode[0])
    lifecycle.evaluation = Recorder()  # An in-memory lifecycle with the same protocol.
    writer = SimpleNamespace(start_episode=lambda: events.append("start"),
                             append=lambda record: events.append(record),
                             discard=lambda: events.append("discard"))
    events = []
    trace = CollectionTrace(writer, lifecycle)
    first = lifecycle.start_episode(Identity(str(tmp_path/"first")))
    trace.start_episode(); trace.append("collection")
    lifecycle.finish_episode(first, "success")
    before = list(recorder.calls)
    mode[0] = "evaluation"
    second = lifecycle.start_episode(Identity("/unused"))
    trace.start_episode(); trace.append("evaluation"); trace.discard()
    lifecycle.set_capture_enabled(False); lifecycle.record_marker("pause")
    lifecycle.finish_episode(second, "failure")
    assert not lifecycle.collecting and recorder.calls == before
    assert events == ["start", "collection"]


def test_session_use_defaults_to_evaluation_and_rejects_unknown(tmp_path):
    path=tmp_path/"use.json"
    assert session_use(path) == "evaluation"
    path.write_text('{"use":"invalid"}')
    with pytest.raises(ValueError): session_use(path)


def test_load_same_model_twice_keeps_process_and_does_not_start(manager):
    manager.perform("load", "fixed")
    manager.perform("load", "fixed")
    assert manager.runtime.calls == ["load"]
    assert manager.active is None


def test_cross_page_switch_only_between_episodes_and_online_is_not_fixed_evaluation(tmp_path):
    runtime = ManagedRuntime(tmp_path)
    state = {"phase": "ready", "model": {"kind": "rlt"},
             "session": {"shared_model": True, "phase": "waiting_scene"}}
    runtime.status = lambda: state
    runtime.select_use("collection")
    assert session_use(tmp_path/"session-use.json") == "collection"
    state["session"]["phase"] = "paused"
    with pytest.raises(DeploymentError): runtime.select_use("evaluation")
    assert session_use(tmp_path/"session-use.json") == "collection"
    state["session"]["phase"] = "waiting_scene"
    state["model"]["training_enabled"] = True
    with pytest.raises(DeploymentError): runtime.select_use("evaluation")
    state["model"]["training_enabled"] = False
    runtime.select_use("evaluation")
    assert session_use(tmp_path/"session-use.json") == "evaluation"


def test_collection_cannot_take_over_active_evaluation_or_load(manager):
    manager.active = {"id": "existing"}
    with pytest.raises(DeploymentError): manager.collection_action("/api/session/start")
    manager.active = None; manager.operation = "load"
    with pytest.raises(DeploymentError): manager.collection_action("/api/session/start")
    assert manager.runtime.calls == []


def test_collection_exception_releases_operation_guard(manager):
    def failure(path, body):
        raise RuntimeError("recorder failure")
    manager.runtime.collection_action = failure
    with pytest.raises(RuntimeError): manager.collection_action("/api/episode/failure")
    assert manager.operation is None


def test_collection_session_prepare_keeps_weights_and_sends_no_start(manager):
    manager.perform("load", "fixed")
    calls=[]
    manager.runtime.select_use=lambda use:calls.append(("use",use))
    manager.runtime._session_action=lambda path:calls.append(("request",path))
    manager.perform("collection_session_start")
    assert manager.collection_session
    manager.perform("collection_session_stop")
    assert not manager.collection_session
    assert manager.runtime.calls == ["load"]
    assert calls == [("use","collection"),("request","/api/session/prepare"),("request","/api/session/stop")]


def test_directory_selection_remembers_new_root_and_rejects_escape(tmp_path, monkeypatch):
    from cobot_console import profile_storage as storage
    allowed=tmp_path/"new"; legacy=tmp_path/"old"
    monkeypatch.setattr(storage,"ALLOWED",allowed)
    monkeypatch.setattr(storage,"LEGACY_BASE",legacy)
    monkeypatch.setattr(storage,"SETTINGS",tmp_path/"settings.json")
    atomic_json(storage.SETTINGS, {"selected":str(allowed/"chosen"),"current":{"warmup":str(legacy)}})
    assert storage.selected_root("warmup") == allowed/"chosen"
    assert storage.selected_root("online") == allowed/"chosen"
    with pytest.raises(ValueError): storage.validate_root(str(tmp_path/"outside"))
    allowed.mkdir()
    (allowed/"escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError): storage.validate_root(str(allowed/"escape"/"outside"))


@pytest.mark.parametrize("backend_phase", ["offline", "loading", "paused"])
def test_directory_http_does_not_wait_for_model_and_only_stages_next_episode(tmp_path, monkeypatch, backend_phase):
    from fastapi.testclient import TestClient
    from cobot_console import api, profile_storage as storage
    from cobot_console.rlt_proxy import BackendResponse, RltBackendError
    from tests.test_console_api import FakeBridge, FakeRecorder, FakeSegmentedService, FakeRegistry, _fresh_cache
    monkeypatch.setenv("COBOT_DATA_PROFILE", "plug_v3_yyshadow")
    monkeypatch.setenv("COBOT_PROFILE_RLT_ENABLED", "1")
    monkeypatch.setattr(api, "RUNTIME_ROOT", tmp_path/"runtime")
    monkeypatch.setattr(storage, "ALLOWED", tmp_path/"data")
    monkeypatch.setattr(storage, "BASE", tmp_path/"data/rlt")
    monkeypatch.setattr(storage, "LEGACY_BASE", tmp_path/"old")
    monkeypatch.setattr(storage, "LEGACY_SETTINGS", tmp_path/"absent.json")
    monkeypatch.setattr(storage, "SETTINGS", tmp_path/"settings.json")
    class Backend:
        def request(self, method, path, body=None):
            assert method == "GET"  # Changing a directory sends no model action.
            if backend_phase == "offline":
                raise RltBackendError("not loaded")
            return BackendResponse(200, {"phase":backend_phase, "shared_model":True,
                                         "recording_data_root":str(tmp_path/"current")})
    recorder = FakeRecorder()
    app = api.create_app(cache=_fresh_cache(), bridge=FakeBridge(), recorder=recorder,
        segmented_service=FakeSegmentedService(), backend_client=Backend(),
        lifecycle_registry=FakeRegistry("loading_actor"), allowed_data_root=tmp_path/"data",
        rlt_data_root=tmp_path/"data", monotonic=lambda:10.)
    with TestClient(app) as client:
        state=client.get("/api/rlt/storage")
        assert state.status_code == 200 and state.json()["editable"]
        target=tmp_path/"data/next"
        reply=client.post("/api/rlt/storage", json={"data_root":str(target)})
        assert reply.status_code == 200,reply.text
        result=client.get("/api/rlt/storage").json()
        assert result["data_root"] == str(target) and result["applies_to"] == "next_episode"
        if backend_phase == "paused":
            assert result["recording_data_root"] == str(tmp_path/"current")
    assert recorder.state == "idle" and recorder.request is None

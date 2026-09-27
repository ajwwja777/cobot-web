"""Optional tests against the preserved native RLT adapter, without ROS or models."""
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

CONTRACT_ROOT = os.environ.get("COBOT_RLT_CONTRACT_ROOT")
pytestmark = pytest.mark.skipif(not CONTRACT_ROOT, reason="Native RLT adapter snapshot not configured")


def native():
    if CONTRACT_ROOT not in sys.path:
        sys.path.insert(0, CONTRACT_ROOT)
    from methods.openpi_rlt.cobot_adapter.session import RltSessionController
    from methods.openpi_rlt.cobot_adapter.session_http import RltSessionApplication, SessionHooks
    from methods.openpi_rlt.cobot_adapter.task5_client import Task5Client, Task5EpisodeIdentity, Task5EpisodeRef
    from methods.openpi_rlt.cobot_adapter.trace import EpisodeOutcome
    return RltSessionController, RltSessionApplication, SessionHooks, Task5Client, Task5EpisodeIdentity, Task5EpisodeRef, EpisodeOutcome


@pytest.mark.parametrize("outcome", ["success", "failure", "saved"])
def test_native_terminal_with_marker_updates_real_web_labels(tmp_path, outcome):
    from fastapi.testclient import TestClient
    from capture_core.api import create_app
    from capture_core.labels import LabelStore
    from tests.test_api import _write_episode, FakeRecorder
    from cobot_console.shared_model_env import SharedEpisodeLifecycle
    Controller, Application, Hooks, Client, Identity, Ref, Outcome = native()
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    recorder = FakeRecorder()
    recorder.state = "stopped"
    web = TestClient(create_app(recorder=recorder, label_store=LabelStore(tmp_path)))
    task5 = Client("http://unused")
    requests = []
    def request(method, path, body=None):
        requests.append((method,path))
        response = web.request(method,path,json=body)
        assert response.status_code < 400, response.text
        return response.json()
    task5._request = request
    identity = Identity("in_the_pot","pi05","step_2000","round_001",str(tmp_path),100)
    task5.start_episode = lambda value: Ref(value,0,str(episode_uuid))
    task5.set_capture_enabled = lambda enabled: {"state":"recording","capture_enabled":enabled}
    lifecycle = SharedEpisodeLifecycle(task5,read_use=lambda:"collection")
    movements = []
    app = Application(Controller(),lifecycle,identity_factory=lambda _:identity,hooks=Hooks(
        is_policy_mode=lambda:True, set_policy_paused=lambda value:movements.append(value),
        submit_outcome=lambda value:None,signal_episode_ready=lambda:None,
        request_front_home=lambda:None,signal_policy_armed=lambda:None))
    def token():
        s=app.status()
        return {"episode_id":s["episode_id"],"generation":s["generation"]}
    app.prepare(**token())
    assert movements == [True]  # Preparing a Session cannot start inference.
    app.start(**token()); app.pause(**token())
    app.terminal(Outcome(outcome),home_after_terminal=False,**token())
    if outcome != "saved":
        app.mark_replay_finalized()
    assert app.status()["phase"] == "waiting_scene"
    labels = LabelStore(tmp_path).get_labels(episode_uuid)
    assert labels["episode_outcome"] == ("unknown" if outcome=="saved" else outcome)
    assert labels["operator_nodes"] == [{"frame_index":0,"node_kind":"pause"}]
    assert any(path.endswith("/labels?data_root="+str(tmp_path).replace("/","%2F")) for _,path in requests)


def test_real_session_can_switch_from_collection_to_evaluation_without_recorder_writes(tmp_path):
    from cobot_console.shared_model_env import SharedEpisodeLifecycle
    from tests.test_shared_model import Recorder
    Controller, Application, Hooks, Client, Identity, Ref, Outcome = native()
    mode=["collection"]; recorder=Recorder()
    recorder.start_episode=lambda identity: Ref(identity,0,"test")
    lifecycle=SharedEpisodeLifecycle(recorder,read_use=lambda:mode[0])
    identity=Identity("task","model","fixed","round",str(tmp_path),100)
    app=Application(Controller(),lifecycle,identity_factory=lambda _:identity,hooks=Hooks(
        is_policy_mode=lambda:True,set_policy_paused=lambda value:None,
        submit_outcome=lambda value:None,signal_episode_ready=lambda:None,
        request_front_home=lambda:None,signal_policy_armed=lambda:None))
    def token():
        s=app.status()
        return {"episode_id":s["episode_id"],"generation":s["generation"]}
    app.prepare(**token());app.start(**token())
    app.terminal(Outcome.SUCCESS,home_after_terminal=False,**token());app.mark_replay_finalized()
    previous=list(recorder.calls);mode[0]="evaluation"
    app.next_episode(**token());app.pause(**token())
    app.update_takeover(left=False,right=True);app.update_takeover(left=False,right=False)
    assert app.status()["phase"] == "paused"
    app.terminal(Outcome.FAILURE,home_after_terminal=False,**token());app.mark_replay_finalized()
    assert app.status()["phase"] == "waiting_scene"
    assert recorder.calls==previous and not lifecycle.collecting

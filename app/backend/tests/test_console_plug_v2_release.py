from pathlib import Path
import json
from fastapi.testclient import TestClient
from cobot_console.api import create_app
from tests.test_console_api import _fresh_cache, FakeBridge, FakeRecorder, FakeSegmentedService, FakeBackend, FakeRegistry

def test_new_cohort_requires_release_and_rechecks_before_motion(monkeypatch, tmp_path):
    manifest=tmp_path/"manifest.json"
    monkeypatch.setenv("COBOT_DATA_PROFILE","plug_v2")
    monkeypatch.setenv("COBOT_PROFILE_RLT_ENABLED","1")
    monkeypatch.setenv("COBOT_RLT_MODEL_MANIFEST",str(manifest))
    backend=FakeBackend()
    app=create_app(cache=_fresh_cache(),bridge=FakeBridge(),recorder=FakeRecorder(),
                  segmented_service=FakeSegmentedService(),backend_client=backend,
                  lifecycle_registry=FakeRegistry("ready_disarmed"),allowed_data_root=tmp_path,
                  rlt_data_root=tmp_path,monotonic=lambda:10.)
    with TestClient(app) as client:
        config=client.get("/api/console/config").json()
        assert config["rlt_model"]["status"]=="awaiting_stage1_deployment"
        assert config["rlt_enabled"] is False
        assert client.post("/api/console/mode",json={"mode":"rlt"}).status_code==409
        manifest.write_text(json.dumps({"cohort":"plug","status":"offline_validated"}))
        assert client.get("/api/console/config").json()["rlt_enabled"] is False
        manifest.write_text(json.dumps({"cohort":"plug_v2","status":"offline_validated","checkpoint_updates":4000}))
        assert client.get("/api/console/config").json()["rlt_enabled"] is True
        assert client.post("/api/console/mode",json={"mode":"rlt"}).status_code==200
        manifest.write_text("corrupted json")
        assert client.post("/api/rlt/session/arm",json={}).status_code==409
        assert backend.calls==[]
        assert client.post("/api/rlt/session/pause",json={}).status_code==200
        assert backend.calls[-1][1]=="/api/session/pause"

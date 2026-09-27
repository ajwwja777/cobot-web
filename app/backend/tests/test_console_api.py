"""Composition tests for the single-port Task5 data console."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from cobot_console.api import create_app
from cobot_console.rlt_proxy import BackendResponse, RltLifecycleState
from capture_core.ros_cache import LatestMessageCache


class FakeBridge:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def shutdown(self):
        self.stopped = True

    def status(self):
        return {"state": "ready", "error_code": None}


class FakeRecorder:
    def __init__(self):
        self.state = "idle"
        self.request = None

    def status(self):
        return {
            "state": self.state,
            "frames_sampled": 0,
            "frames_written": 0,
            "completion_state": None,
            "publication_status": None,
            "path": None,
            "error": None,
        }

    def start(self, request):
        self.request = request
        self.state = "recording"
        return self.status()

    def stop(self):
        self.state = "stopped"
        return None


class FakeSegmentedService:
    def status(self):
        return {
            "episode_uuid": None,
            "capture_state": "idle",
            "generation": 0,
            "node_count": 0,
            "nodes": [],
            "training_frame_count": 0,
        }

    def list_episodes(self, *, data_root=None):
        return []


class FakeBackend:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        return BackendResponse(
            200,
            {
                "phase": "ready",
                "episode_id": 0,
                "generation": 3,
                "policy_paused": True,
            },
        )


class FakeRegistry:
    def __init__(self, phase="offline"):
        self.phase = phase

    def read(self):
        return RltLifecycleState(
            "gen-1" if self.phase != "offline" else None,
            self.phase,
            123 if self.phase != "offline" else None,
            4000 if self.phase != "offline" else None,
            "eval" if self.phase != "offline" else None,
            "2026-09-16T10:00:00Z" if self.phase != "offline" else None,
            None,
            {},
        )


def _fresh_cache(now=10.0):
    cache = LatestMessageCache()
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    for key in ("camera_high", "camera_left", "camera_right"):
        cache.put(key, image, source_stamp=now, arrival_stamp=now)
    cache.put("handover_mode", "policy", source_stamp=now, arrival_stamp=now)
    cache.put("teach_left", False, source_stamp=now, arrival_stamp=now)
    cache.put("teach_right", False, source_stamp=now, arrival_stamp=now)
    return cache


def test_console_owns_one_dependency_set_and_stays_available_when_rlt_offline(tmp_path: Path):
    bridge = FakeBridge()
    recorder = FakeRecorder()
    app = create_app(
        cache=_fresh_cache(),
        bridge=bridge,
        recorder=recorder,
        segmented_service=FakeSegmentedService(),
        backend_client=FakeBackend(),
        lifecycle_registry=FakeRegistry("offline"),
        allowed_data_root=tmp_path,
        rlt_data_root=tmp_path,
        monotonic=lambda: 10.0,
    )

    with TestClient(app) as client:
        identity = client.get("/api/console/identity")
        status = client.get("/api/console/status")
        page = client.get("/")

    assert identity.status_code == 200
    assert identity.json()["service"] == "cobot-data-console-v1"
    assert status.json()["rlt_backend_phase"] == "offline"
    assert status.json()["ros_readiness"]["status"] == "ok"
    assert page.status_code == 200
    assert app.state.shared_recorder is recorder
    assert bridge.started and bridge.stopped


def test_mode_selection_and_backend_proxy_use_one_public_origin(tmp_path: Path):
    backend = FakeBackend()
    app = create_app(
        cache=_fresh_cache(),
        bridge=FakeBridge(),
        recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(),
        backend_client=backend,
        lifecycle_registry=FakeRegistry("ready_disarmed"),
        allowed_data_root=tmp_path,
        rlt_data_root=tmp_path,
        monotonic=lambda: 10.0,
    )

    with TestClient(app) as client:
        selected = client.post("/api/console/mode", json={"mode": "rlt"})
        proxied = client.get("/api/rlt/session")
        schema = client.get("/api/rlt-recorder/openapi.json")

    assert selected.status_code == 200
    assert selected.json()["selected_mode"] == "rlt"
    assert proxied.status_code == 200
    assert backend.calls == [("GET", "/api/session", None)]
    assert schema.status_code == 200
    assert "/api/storage/prepare" in schema.json()["paths"]


def test_unknown_mode_is_rejected_without_changing_selection(tmp_path: Path):
    app = create_app(
        cache=_fresh_cache(), bridge=FakeBridge(), recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(), backend_client=FakeBackend(),
        lifecycle_registry=FakeRegistry(), allowed_data_root=tmp_path,
        rlt_data_root=tmp_path, monotonic=lambda: 10.0,
    )
    with TestClient(app) as client:
        response = client.post("/api/console/mode", json={"mode": "other"})
        state = client.get("/api/console/status").json()
    assert response.status_code == 422
    assert state["selected_mode"] == "normal"

def test_rlt_review_uses_same_origin_and_prefixed_assets(tmp_path):
    app = create_app(cache=_fresh_cache(),bridge=FakeBridge(),recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(),backend_client=FakeBackend(),
        lifecycle_registry=FakeRegistry(),allowed_data_root=tmp_path,rlt_data_root=tmp_path,
        monotonic=lambda:10.0)
    with TestClient(app) as client:
        page=client.get("/rlt-review/")
        js=client.get("/rlt-review/app.js")
    assert page.status_code==200
    assert 'src="/rlt-review/app.js"' in page.text
    assert 'href="/rlt-review/styles.css"' in page.text
    assert "/api/rlt-recorder" in js.text
    assert "REVIEW_ONLY || state.recorderActive" in js.text


def test_capture_profile_exposes_new_root_and_blocks_legacy_rlt(monkeypatch, tmp_path):
    import cobot_console.api as console_api
    monkeypatch.setenv("COBOT_DATA_PROFILE", "left-camera-v2")
    monkeypatch.setenv("COBOT_PROFILE_RLT_ENABLED", "0")
    monkeypatch.setenv("COBOT_DATASET_ROUND", "left_camera_v2")
    monkeypatch.setattr(console_api, "DEFAULT_DATA_ROOT", tmp_path / "demonstrations" / "raw")
    backend = FakeBackend()
    app = create_app(cache=_fresh_cache(), bridge=FakeBridge(), recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(), backend_client=backend,
        lifecycle_registry=FakeRegistry(), allowed_data_root=tmp_path,
        rlt_data_root=tmp_path / "online" / "raw", monotonic=lambda: 10.0)
    with TestClient(app) as client:
        config = client.get("/api/console/config").json()
        response = client.post("/api/console/mode", json={"mode": "rlt"})
        state = client.get("/api/console/status").json()
    assert config["normal_data_root"] == str(tmp_path / "demonstrations" / "raw")
    assert config["dataset_round"] == "left_camera_v2"
    assert config["rlt_enabled"] is False
    assert response.status_code == 409
    assert state["selected_mode"] == "normal"
    assert backend.calls == []


def test_legacy_profile_keeps_rlt_available(monkeypatch, tmp_path):
    monkeypatch.setenv("COBOT_DATA_PROFILE", "legacy-camera-v1")
    monkeypatch.setenv("COBOT_PROFILE_RLT_ENABLED", "1")
    app = create_app(cache=_fresh_cache(), bridge=FakeBridge(), recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(), backend_client=FakeBackend(),
        lifecycle_registry=FakeRegistry(), allowed_data_root=tmp_path,
        rlt_data_root=tmp_path, monotonic=lambda: 10.0)
    with TestClient(app) as client:
        assert client.get("/api/console/config").json()["rlt_enabled"] is True
        assert client.post("/api/console/mode", json={"mode": "rlt"}).status_code == 200

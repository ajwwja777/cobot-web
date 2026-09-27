from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from segmented_capture.api import create_app


class _InputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs = {}
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.add(attributes["id"])
        if tag == "input" and "name" in attributes:
            self.inputs[attributes["name"]] = attributes


class FakeBridge:
    def start(self):
        return None

    def shutdown(self):
        return None

    def status(self):
        return {"state": "ready", "error_code": None}


class FakeService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.uuid = str(uuid4())
        self.generation = 0
        self.capture_state = "idle"
        self.nodes = []
        self.finalized = False

    def status(self):
        return {
            "episode_uuid": self.uuid if self.generation else None,
            "capture_state": self.capture_state,
            "generation": self.generation,
            "node_count": len(self.nodes),
            "nodes": list(self.nodes),
            "training_frame_count": 0,
        }

    @property
    def active(self):
        return self.capture_state in {'recording','paused','finalizing'}

    def start(self, identity, *, data_root):
        self.identity = identity
        self.data_root = data_root
        self.generation = 1
        self.capture_state = "paused"
        self.nodes = [{"node_id": 1, "kind": "start"}]
        return self.status()

    def _move(self, state, kind):
        self.generation += 1
        self.capture_state = state
        self.nodes.append({"node_id": len(self.nodes) + 1, "kind": kind})
        return self.status()

    def pause(self):
        return self._move("paused", "transition")

    def resume(self):
        return self._move("recording", "transition")

    def marker(self):
        return self._move(self.capture_state, "marker")

    def stop(self):
        self._move("committed", "end")
        self.finalized = True
        return type("Result", (), {"sidecar": self.root / "sidecar.json"})()

    def finalized_successfully(self):
        return self.finalized

    def label_outcome(self, outcome):
        self.outcome = outcome
        return {'episode_outcome': outcome}

    def read_episode(self, episode_uuid, *, data_root=None):
        self.history_data_root = data_root
        if episode_uuid != self.uuid:
            raise FileNotFoundError(episode_uuid)
        return self.status()

    def keyframe_path(self, episode_uuid, node_id, camera_key, *, data_root=None):
        self.history_data_root = data_root
        if episode_uuid != self.uuid:
            raise FileNotFoundError(episode_uuid)
        path = self.root / f"{node_id}-{camera_key}.jpg"
        path.write_bytes(b"jpeg")
        return path

    def list_episodes(self, *, data_root=None):
        self.history_data_root = data_root
        return [
            {
                "episode_uuid": self.uuid,
                "capture_state": self.capture_state,
                "node_count": len(self.nodes),
            }
        ]

    def load_review(self, episode_uuid, *, data_root=None):
        self.history_data_root = data_root
        if episode_uuid != self.uuid:
            raise FileNotFoundError(episode_uuid)
        return {
            "schema_version": "task5-segment-review-v1",
            "episode_uuid": self.uuid,
            "review_revision": 0,
            "selected_interval_ids": [],
            "intervals": [{"interval_id": 1, "start_node_id": 1, "end_node_id": 2}],
        }

    def save_review(self, episode_uuid, *, expected_revision, selected_interval_ids, note, data_root=None):
        result = self.load_review(episode_uuid, data_root=data_root)
        result["review_revision"] = expected_revision + 1
        result["selected_interval_ids"] = selected_interval_ids
        result["note"] = note
        return result

    def delete_episode(self, episode_uuid, *, data_root):
        self.history_data_root = data_root
        if episode_uuid != self.uuid:
            raise FileNotFoundError(episode_uuid)
        return {
            "episode_uuid": episode_uuid,
            "episode_index": 1,
            "deleted_at": "2026-09-08T00:00:00Z",
        }


def start_payload(root: Path):
    return {
        "data_root": str(root),
        "task_id": "plug_cycle",
        "model_id": "expert",
        "checkpoint_id": "manual",
        "dataset_round": "expert80",
    }


def version(service: FakeService):
    return {"episode_uuid": service.uuid, "generation": service.generation}


class CommitThenRaiseService(FakeService):
    def stop(self):
        super().stop()
        raise RuntimeError("reply_or_lease_cleanup_failed")


class SealWithoutFinalizeService(FakeService):
    def stop(self):
        self._move("committed", "end")
        raise RuntimeError("writer_failed")


def test_stop_returns_verified_commit_when_post_finalize_step_raises(project_tmp) -> None:
    service = CommitThenRaiseService(project_tmp)
    client = TestClient(create_app(service=service, bridge=FakeBridge()))
    client.post("/api/segmented-teach/start", json=start_payload(project_tmp))
    original = version(service)

    stopped = client.post("/api/segmented-teach/stop", json=original)
    assert stopped.status_code == 200
    assert stopped.json()["capture_state"] == "committed"

    repeated = client.post("/api/segmented-teach/stop", json=original)
    assert repeated.status_code == 200


def test_stop_does_not_mask_unfinalized_writer_failure(project_tmp) -> None:
    service = SealWithoutFinalizeService(project_tmp)
    client = TestClient(create_app(service=service, bridge=FakeBridge()))
    client.post("/api/segmented-teach/start", json=start_payload(project_tmp))

    stopped = client.post("/api/segmented-teach/stop", json=version(service))
    assert stopped.status_code == 500
    assert stopped.json()["detail"] == "segmented_stop_failed"


def test_start_and_generation_guarded_controls(project_tmp) -> None:
    service = FakeService(project_tmp)
    client = TestClient(create_app(service=service, bridge=FakeBridge()))

    started = client.post("/api/segmented-teach/start", json=start_payload(project_tmp))
    assert started.status_code == 200
    assert started.json()["capture_state"] == "paused"

    stale = client.post(
        "/api/segmented-teach/resume",
        json={"episode_uuid": service.uuid, "generation": 0},
    )
    assert stale.status_code == 409
    resumed = client.post("/api/segmented-teach/resume", json=version(service))
    assert resumed.status_code == 200
    assert resumed.json()["capture_state"] == "recording"


def test_button_matrix_and_marker_contract(project_tmp) -> None:
    service = FakeService(project_tmp)
    client = TestClient(create_app(service=service, bridge=FakeBridge()))
    client.post("/api/segmented-teach/start", json=start_payload(project_tmp))

    status = client.get("/api/segmented-teach/status").json()
    assert status["buttons"] == {
        "pause": False,
        "resume": True,
        "marker": False,
        "stop": True,
        "discard": True,
    }
    client.post("/api/segmented-teach/resume", json=version(service))
    marked = client.post("/api/segmented-teach/marker", json=version(service))
    assert marked.status_code == 200
    assert marked.json()["nodes"][-1]["kind"] == "marker"


def test_episode_and_keyframe_are_read_only(project_tmp) -> None:
    service = FakeService(project_tmp)
    client = TestClient(create_app(service=service, bridge=FakeBridge()))
    client.post("/api/segmented-teach/start", json=start_payload(project_tmp))

    assert client.get(f"/api/segmented-teach/episodes/{service.uuid}").status_code == 200
    response = client.get(
        f"/api/segmented-teach/episodes/{service.uuid}/nodes/1/camera_high.jpg"
    )
    assert response.status_code == 200
    assert response.content == b"jpeg"
    assert client.get("/api/segmented-teach/episodes").json()[0]["episode_uuid"] == service.uuid


def test_completed_episode_review_api_records_selected_intervals(project_tmp) -> None:
    service = FakeService(project_tmp)
    client = TestClient(create_app(service=service, bridge=FakeBridge()))

    initial = client.get(f"/api/segmented-teach/episodes/{service.uuid}/review")
    assert initial.status_code == 200
    saved = client.put(
        f"/api/segmented-teach/episodes/{service.uuid}/review",
        json={"expected_revision": 0, "selected_interval_ids": [1], "note": "插入"},
    )
    assert saved.status_code == 200
    assert saved.json()["selected_interval_ids"] == [1]


def test_storage_prepare_creates_selected_root_and_allocates_without_reuse(project_tmp) -> None:
    allowed = project_tmp / "allowed"
    selected = allowed / "task5" / "pilot-v1" / "raw"
    series = selected / "plug_insertion" / "expert" / "node_pilot_v1"
    series.mkdir(parents=True)
    ledger = series / ".task5" / "deletions.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(
        '{"episode_uuid":"00000000-0000-0000-0000-000000000004",'
        '"episode_index":4,"deleted_at":"2026-09-08T00:00:00Z"}\n',
        encoding="utf-8",
    )
    service = FakeService(project_tmp)
    client = TestClient(
        create_app(service=service, bridge=FakeBridge(), allowed_data_root=allowed)
    )

    payload = start_payload(selected)
    payload.update(task_id="plug_insertion", dataset_round="node_pilot_v1")
    response = client.post(
        "/api/segmented-teach/storage/prepare",
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["data_root"] == str(selected.resolve())
    assert response.json()["episode_directory"] == str(series.resolve())
    assert response.json()["next_episode_index"] == 5


def test_storage_prepare_rejects_root_outside_allowed_base(project_tmp) -> None:
    allowed = project_tmp / "allowed"
    allowed.mkdir()
    outside = project_tmp / "outside" / "raw"
    service = FakeService(project_tmp)
    client = TestClient(
        create_app(service=service, bridge=FakeBridge(), allowed_data_root=allowed)
    )

    response = client.post(
        "/api/segmented-teach/storage/prepare", json=start_payload(outside)
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "data_root_not_allowed"
    assert not outside.exists()


def test_history_and_delete_are_scoped_to_selected_root(project_tmp) -> None:
    allowed = project_tmp / "allowed"
    selected = allowed / "pilot" / "raw"
    selected.mkdir(parents=True)
    service = FakeService(project_tmp)
    client = TestClient(
        create_app(service=service, bridge=FakeBridge(), allowed_data_root=allowed)
    )
    query = {"data_root": str(selected)}

    assert client.get("/api/segmented-teach/episodes", params=query).status_code == 200
    deleted = client.delete(
        f"/api/segmented-teach/episodes/{service.uuid}", params=query
    )

    assert deleted.status_code == 200
    assert deleted.json()["episode_index"] == 1
    assert service.history_data_root == selected.resolve()


def test_frontend_allows_storage_selection_and_episode_deletion(project_tmp) -> None:
    client = TestClient(create_app(service=FakeService(project_tmp), bridge=FakeBridge()))

    response = client.get("/")
    parser = _InputParser()
    parser.feed(response.text)

    data_root = parser.inputs["data_root"]
    assert "readonly" not in data_root
    assert set(parser.inputs) == {"data_root"}
    assert "list" not in data_root
    assert data_root["value"] == (
        "/media/agilex/Getea1/jiaan/data/test"
    )
    assert {
        "prepare-storage", "episode-directory", "next-episode", "delete-history",
        "capture-home-target", "capture-home-pose", "stop-home",
    }.issubset(parser.ids)

"""HTTP contract tests for the read-only Task5 companion API."""

from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID, uuid4

import h5py
import numpy as np
import pytest
from fastapi.testclient import TestClient

from capture_core.api import create_app
from capture_core.labels import LabelStore
from capture_core.recorder import RecorderError, RecorderStartRequest
from capture_core.ros_cache import LatestMessageCache
from capture_core.topics import REQUIRED_TOPICS
from segmented_capture.ports import CaptureGate

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python before 3.11
    import tomli as tomllib


def _write_episode(root: Path, episode_uuid: UUID) -> Path:
    parent = root / "in_the_pot" / "pi05" / "round_001"
    parent.mkdir(parents=True)
    path = parent / "episode_000000.hdf5"
    with h5py.File(path, "w") as episode:
        episode.attrs.update(
            project_id="task5_jiaan_hil_realworld_rl",
            collector_version="v1",
            rollout_schema_version=1,
            fps=30.0,
            DT=1.0 / 30.0,
            completion_state="complete",
            task_id="in_the_pot",
            model_id="pi05",
            checkpoint_id="step_2000",
            dataset_round="round_001",
            episode_index=0,
            episode_uuid=str(episode_uuid),
            start_timestamp=1.0,
            end_timestamp=1.0,
            termination_reason="operator_stop",
        )
        observations = episode.create_group("observations")
        images = observations.create_group("images")
        for camera in ("cam_high", "cam_left_wrist", "cam_right_wrist"):
            images.create_dataset(camera, data=np.zeros((1, 1, 1, 3), dtype=np.uint8))
        for field in ("qpos", "qvel", "effort"):
            observations.create_dataset(field, data=np.zeros((1, 14), dtype=np.float32))
        episode.create_dataset("action", data=np.zeros((1, 14), dtype=np.float32))
        episode.create_dataset("base_action", data=np.zeros((1, 2), dtype=np.float32))
        rollout = episode.create_group("rollout")
        for field in (
            "policy_command_submitted",
            "coordinator_command",
            "front_observation",
            "rear_observation",
        ):
            rollout.create_dataset(field, data=np.zeros((1, 14), dtype=np.float32))
        rollout.create_dataset("teach_active_left", data=np.array([False]))
        rollout.create_dataset("teach_active_right", data=np.array([False]))
        rollout.create_dataset(
            "control_source_left", data=np.array([2], dtype=np.uint8)
        )
        rollout.create_dataset(
            "control_source_right", data=np.array([3], dtype=np.uint8)
        )
        rollout.create_dataset(
            "intervention_id_left", data=np.array([1], dtype=np.uint64)
        )
        rollout.create_dataset(
            "intervention_id_right", data=np.array([0], dtype=np.uint64)
        )
        rollout.create_dataset("is_intervention_left", data=np.array([True]))
        rollout.create_dataset("is_intervention_right", data=np.array([False]))
        rollout.create_dataset("frame_index", data=np.array([0], dtype=np.uint64))
        rollout.create_dataset(
            "sample_timestamp", data=np.array([1.0], dtype=np.float64)
        )
        rollout.create_dataset(
            "handover_mode",
            data=np.array(["manual:left"], dtype=object),
            dtype=h5py.string_dtype(encoding="utf-8"),
        )
        rollout.create_dataset(
            "handover_fault",
            data=np.array([""], dtype=object),
            dtype=h5py.string_dtype(encoding="utf-8"),
        )
        topic_timestamps = rollout.create_group("topic_timestamp")
        arrival_timestamps = rollout.create_group("arrival_timestamp")
        valid_masks = rollout.create_group("valid_mask")
        validity_keys = set(REQUIRED_TOPICS) | {
            "qpos",
            "qvel",
            "effort",
            "front_observation",
            "rear_observation",
            "policy_command_submitted",
            "coordinator_command",
            "action",
            "teach_active_left",
            "teach_active_right",
        }
        for key in REQUIRED_TOPICS:
            topic_timestamps.create_dataset(key, data=np.ones(1, dtype=np.float64))
            arrival_timestamps.create_dataset(key, data=np.ones(1, dtype=np.float64))
        for key in validity_keys:
            valid_masks.create_dataset(key, data=np.ones(1, dtype=np.bool_))
    return path


class FakeRecorder:
    def __init__(self) -> None:
        self.state = "idle"
        self.request: RecorderStartRequest | None = None

    def status(self):
        return {"state": self.state, "path": "/private/active.hdf5"}

    def start(self, request: RecorderStartRequest):
        if self.state not in {"idle", "stopped"}:
            raise RecorderError("recorder already started")
        self.request = request
        self.state = "recording"
        return self.status()

    def stop(self):
        if self.state != "recording":
            raise RecorderError("no active recording")
        self.state = "stopped"
        return Path("episode_000000.hdf5")


class FakePreviewManager:
    def __init__(self, preview_path: Path | None = None) -> None:
        self.preview_path_value = preview_path
        self.states: dict[str, dict[str, object]] = {}
        self.busy: set[str] = set()
        self.forgotten: list[str] = []

    def queue(self, artifacts):
        key = str(artifacts.episode_uuid)
        state = {
            "episode_uuid": key,
            "state": "queued",
            "completed_frames": 0,
            "total_frames": artifacts.frame_count,
            "error_code": None,
        }
        self.states[key] = state
        return dict(state)

    def status(self, episode_uuid: str):
        return dict(
            self.states.get(
                episode_uuid,
                {
                    "episode_uuid": episode_uuid,
                    "state": "missing",
                    "completed_frames": 0,
                    "total_frames": 0,
                    "error_code": None,
                },
            )
        )

    def is_busy(self, episode_uuid: str) -> bool:
        return episode_uuid in self.busy

    def preview_path(self, _artifacts):
        if self.preview_path_value is None:
            from capture_core.preview_jobs import PreviewNotReady

            raise PreviewNotReady("not ready")
        return self.preview_path_value

    def forget(self, episode_uuid: str) -> None:
        self.forgotten.append(episode_uuid)

    def shutdown(self) -> None:
        return None


def _start_payload() -> dict[str, object]:
    return {
        "task_id": "in_the_pot",
        "model_id": "pi05",
        "checkpoint_id": "step_2000",
        "dataset_round": "round_001",
        "episode_index": 4,
        "max_timesteps": 30,
    }


def _series_payload(root: Path) -> dict[str, object]:
    return {
        "data_root": str(root),
        "task_id": "in_the_pot",
        "model_id": "pi05",
        "checkpoint_id": "step_2000",
        "dataset_round": "round_001",
    }


def test_rlt_capture_gate_pause_resume_does_not_stop_episode(tmp_path: Path) -> None:
    recorder = FakeRecorder()
    gate = CaptureGate(enabled=False)
    client = TestClient(create_app(
        recorder=recorder,
        label_store=LabelStore(tmp_path),
        capture_gate=gate,
    ))
    assert client.post("/api/episodes/capture/pause").status_code == 409
    assert client.post("/api/episodes/start", json=_start_payload()).status_code == 200
    assert gate.is_open()

    paused = client.post("/api/episodes/capture/pause")
    assert paused.status_code == 200
    assert paused.json()["state"] == "recording"
    assert paused.json()["capture_enabled"] is False
    assert not gate.is_open()

    resumed = client.post("/api/episodes/capture/resume")
    assert resumed.status_code == 200
    assert resumed.json()["capture_enabled"] is True
    assert gate.is_open()


def test_health_lifecycle_and_exact_route_surface(tmp_path: Path):
    """Catches missing routes, duplicate lifecycle acceptance, or control endpoints."""
    recorder = FakeRecorder()
    client = TestClient(create_app(recorder=recorder, label_store=LabelStore(tmp_path)))

    assert client.get("/healthz").json() == {"status": "ok"}
    response = client.post("/api/episodes/start", json=_start_payload())
    assert response.status_code == 200
    assert recorder.request is not None
    assert recorder.request.identity.episode_index == 4
    assert client.post("/api/episodes/start", json=_start_payload()).status_code == 409
    assert client.post("/api/episodes/stop").status_code == 200
    assert client.post("/api/episodes/stop").status_code == 409

    second = {**_start_payload(), "episode_index": 5}
    assert client.post("/api/episodes/start", json=second).status_code == 200
    assert recorder.request is not None
    assert recorder.request.identity.episode_index == 5
    assert client.post("/api/episodes/stop").status_code == 200

    paths = set(client.app.openapi()["paths"])
    assert paths == {
        "/healthz",
        "/api/status",
        "/api/episodes/start",
        "/api/episodes/stop",
        "/api/episodes/discard",
        "/api/episodes/capture/pause",
        "/api/episodes/capture/resume",
        "/api/episodes",
        "/api/episodes/{episode_uuid}/labels",
        "/api/cameras/{camera_key}.mjpeg",
        "/api/storage/prepare",
        "/api/episodes/{episode_uuid}/outcome",
        "/api/episodes/{episode_uuid}",
        "/api/episodes/{episode_uuid}/preview",
        "/api/episodes/{episode_uuid}/preview/status",
        "/api/episodes/{episode_uuid}/preview.mp4",
        "/api/episodes/{episode_uuid}/frames/{frame_index}/{camera_key}.jpg",
        "/api/episodes/{episode_uuid}/preview/{artifact_name}",
    }


def test_outcome_route_maps_success_and_rejects_uuid_mismatch(tmp_path: Path):
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    client = TestClient(
        create_app(recorder=FakeRecorder(), label_store=LabelStore(tmp_path))
    )

    success = client.post(
        f"/api/episodes/{episode_uuid}/outcome",
        params={"data_root": str(tmp_path)},
        json={"episode_uuid": str(episode_uuid), "outcome": "success"},
    )
    mismatch = client.post(
        f"/api/episodes/{episode_uuid}/outcome",
        params={"data_root": str(tmp_path)},
        json={"episode_uuid": str(uuid4()), "outcome": "failure"},
    )
    aborted = client.post(
        f"/api/episodes/{episode_uuid}/outcome",
        params={"data_root": str(tmp_path)},
        json={"episode_uuid": str(episode_uuid), "outcome": "aborted"},
    )

    assert success.status_code == 200
    assert success.json()["episode_outcome"] == "success"
    assert success.json()["keep_for_training"] == "true"
    assert mismatch.status_code == 409
    assert mismatch.json() == {"detail": "episode_uuid_mismatch"}
    assert aborted.status_code == 422


def test_delete_episode_is_permanent_and_preserves_next_index(tmp_path: Path):
    episode_uuid = uuid4()
    path = _write_episode(tmp_path, episode_uuid)
    client = TestClient(
        create_app(recorder=FakeRecorder(), label_store=LabelStore(tmp_path))
    )

    deleted = client.request(
        "DELETE",
        f"/api/episodes/{episode_uuid}",
        params={"data_root": str(tmp_path)},
        json={"episode_uuid": str(episode_uuid)},
    )
    prepared = client.post(
        "/api/storage/prepare", json=_series_payload(tmp_path)
    )

    assert deleted.status_code == 200
    assert deleted.json() == {
        "deleted": True,
        "episode_uuid": str(episode_uuid),
        "episode_index": 0,
    }
    assert not path.exists()
    assert prepared.status_code == 200
    assert prepared.json()["existing_episode_indices"] == []
    assert prepared.json()["next_episode_index"] == 1
    assert prepared.json()["label_blocked"] is False


def test_delete_episode_rejects_active_recorder_and_uuid_mismatch(tmp_path: Path):
    episode_uuid = uuid4()
    path = _write_episode(tmp_path, episode_uuid)
    recorder = FakeRecorder()
    recorder.state = "recording"
    client = TestClient(
        create_app(recorder=recorder, label_store=LabelStore(tmp_path))
    )

    active = client.request(
        "DELETE",
        f"/api/episodes/{episode_uuid}",
        params={"data_root": str(tmp_path)},
        json={"episode_uuid": str(episode_uuid)},
    )
    recorder.state = "idle"
    mismatch = client.request(
        "DELETE",
        f"/api/episodes/{episode_uuid}",
        params={"data_root": str(tmp_path)},
        json={"episode_uuid": str(uuid4())},
    )

    assert active.status_code == 409
    assert active.json() == {"detail": "recorder_active"}
    assert mismatch.status_code == 409
    assert mismatch.json() == {"detail": "episode_uuid_mismatch"}
    assert path.is_file()


def test_preview_routes_queue_report_and_stream_cached_video(tmp_path: Path):
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    video = tmp_path / "preview.mp4"
    video.write_bytes(b"small-video")
    previews = FakePreviewManager(video)
    client = TestClient(
        create_app(
            recorder=FakeRecorder(),
            label_store=LabelStore(tmp_path),
            preview_manager=previews,
        )
    )

    queued = client.post(
        f"/api/episodes/{episode_uuid}/preview",
        params={"data_root": str(tmp_path)},
    )
    status = client.get(
        f"/api/episodes/{episode_uuid}/preview/status",
        params={"data_root": str(tmp_path)},
    )
    streamed = client.get(
        f"/api/episodes/{episode_uuid}/preview.mp4",
        params={"data_root": str(tmp_path)},
    )

    assert queued.status_code == 200
    assert queued.json()["state"] == "queued"
    assert status.status_code == 200
    assert status.json()["episode_uuid"] == str(episode_uuid)
    assert streamed.status_code == 200
    assert streamed.content == b"small-video"
    assert streamed.headers["content-type"] == "video/mp4"
    assert streamed.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_episode_frame_route_returns_jpeg_and_checks_bounds(tmp_path: Path):
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    client = TestClient(
        create_app(recorder=FakeRecorder(), label_store=LabelStore(tmp_path))
    )

    frame = client.get(
        f"/api/episodes/{episode_uuid}/frames/0/camera_high.jpg",
        params={"data_root": str(tmp_path)},
    )
    missing = client.get(
        f"/api/episodes/{episode_uuid}/frames/1/camera_high.jpg",
        params={"data_root": str(tmp_path)},
    )

    assert frame.status_code == 200
    assert frame.headers["content-type"] == "image/jpeg"
    assert frame.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert frame.content.startswith(b"\xff\xd8")
    assert missing.status_code == 422


def test_delete_refuses_preview_race_and_forgets_cache_after_success(tmp_path: Path):
    episode_uuid = uuid4()
    path = _write_episode(tmp_path, episode_uuid)
    previews = FakePreviewManager()
    previews.busy.add(str(episode_uuid))
    client = TestClient(
        create_app(
            recorder=FakeRecorder(),
            label_store=LabelStore(tmp_path),
            preview_manager=previews,
        )
    )

    busy = client.request(
        "DELETE",
        f"/api/episodes/{episode_uuid}",
        params={"data_root": str(tmp_path)},
        json={"episode_uuid": str(episode_uuid)},
    )
    previews.busy.clear()
    deleted = client.request(
        "DELETE",
        f"/api/episodes/{episode_uuid}",
        params={"data_root": str(tmp_path)},
        json={"episode_uuid": str(episode_uuid)},
    )

    assert busy.status_code == 409
    assert busy.json() == {"detail": "preview_active"}
    assert path.is_file() is False
    assert deleted.status_code == 200
    assert previews.forgotten == [str(episode_uuid)]


def test_camera_preview_route_streams_fresh_jpeg_and_rejects_missing_stream(
    tmp_path: Path,
):
    cache = LatestMessageCache()
    now = time.monotonic()
    cache.put(
        "camera_high",
        np.zeros((8, 12, 3), dtype=np.uint8),
        source_stamp=now,
        arrival_stamp=now,
    )
    client = TestClient(
        create_app(
            recorder=FakeRecorder(),
            label_store=LabelStore(tmp_path),
            cache=cache,
        )
    )

    response = client.get("/api/cameras/camera_high.mjpeg")
    missing = client.get("/api/cameras/camera_left.mjpeg")
    unknown = client.get("/api/cameras/not_a_camera.mjpeg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "multipart/x-mixed-replace; boundary=frame"
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"--frame\r\n")
    assert missing.status_code == 503
    assert missing.json() == {"detail": "camera_preview_unavailable"}
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "camera_not_found"}


def test_prepare_creates_series_and_start_allocates_next_index(tmp_path: Path):
    recorder = FakeRecorder()
    client = TestClient(
        create_app(recorder=recorder, label_store=LabelStore(tmp_path))
    )
    selected = tmp_path / "selected"

    prepared = client.post(
        "/api/storage/prepare", json=_series_payload(selected)
    )
    started = client.post(
        "/api/episodes/start", json=_series_payload(selected)
    )

    assert prepared.status_code == 200
    assert prepared.json() == {
        "data_root": str(selected.resolve()),
        "episode_directory": str(
            selected.resolve() / "in_the_pot" / "pi05" / "round_001"
        ),
        "existing_episode_indices": [],
        "next_episode_index": 1,
        "episode_count": 0,
        "latest_episode_uuid": None,
        "latest_episode_index": None,
        "latest_labels_complete": True,
        "label_blocked": False,
    }
    assert started.status_code == 200
    assert recorder.request is not None
    assert recorder.request.identity.episode_index == 1
    assert recorder.request.data_root == selected.resolve()


def test_direct_start_cannot_bypass_latest_label_gate(tmp_path: Path):
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    recorder = FakeRecorder()
    client = TestClient(
        create_app(recorder=recorder, label_store=LabelStore(tmp_path))
    )

    response = client.post(
        "/api/episodes/start", json=_series_payload(tmp_path)
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "latest_episode_labels_required"}
    assert recorder.request is None


def test_episode_and_label_routes_use_explicit_data_root(tmp_path: Path):
    selected = tmp_path / "selected"
    episode_uuid = uuid4()
    _write_episode(selected, episode_uuid)
    client = TestClient(
        create_app(
            recorder=FakeRecorder(),
            label_store=LabelStore(tmp_path / "default"),
        )
    )

    episodes = client.get(
        "/api/episodes", params={"data_root": str(selected)}
    )
    labels = client.get(
        f"/api/episodes/{episode_uuid}/labels",
        params={"data_root": str(selected)},
    )
    saved = client.put(
        f"/api/episodes/{episode_uuid}/labels",
        params={"data_root": str(selected)},
        json={
            "episode_uuid": str(episode_uuid),
            "episode_outcome": "success",
            "episode_quality": "good",
            "keep_for_training": "true",
        },
    )

    assert episodes.status_code == 200
    assert episodes.json()[0]["episode_uuid"] == str(episode_uuid)
    assert labels.status_code == 200
    assert labels.json()["intervention_phases"][0] == {
        "start_frame": 0,
        "end_frame": 0,
        "handover_mode": "manual:left",
        "human_sides": ["left"],
    }
    assert saved.status_code == 200
    assert saved.json()["episode_outcome"] == "success"


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "../escape"},
        {"rollout_mode": "robot_control"},
        {"episode_index": -1},
        {"episode_index": True},
        {"max_timesteps": 0},
        {"max_timesteps": True},
    ],
)
def test_start_request_is_validated_before_recorder_call(tmp_path: Path, change):
    """Catches unvalidated identifiers, modes, indices, and recording bounds."""
    recorder = FakeRecorder()
    client = TestClient(create_app(recorder=recorder, label_store=LabelStore(tmp_path)))
    payload = {**_start_payload(), **change}

    response = client.post("/api/episodes/start", json=payload)

    assert response.status_code == 422
    assert recorder.request is None


def test_label_api_maps_validation_and_uuid_conflicts(tmp_path: Path):
    """Allows simple failure labels and rejects a cross-episode body UUID."""
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    client = TestClient(
        create_app(recorder=FakeRecorder(), label_store=LabelStore(tmp_path))
    )

    assert client.get("/api/episodes").json()[0]["episode_uuid"] == str(episode_uuid)
    assert client.get(f"/api/episodes/{episode_uuid}/labels").json()[
        "episode_uuid"
    ] == str(episode_uuid)
    failure = client.put(
        f"/api/episodes/{episode_uuid}/labels",
        json={"episode_uuid": str(episode_uuid), "episode_outcome": "failure"},
    )
    mismatch = client.put(
        f"/api/episodes/{episode_uuid}/labels",
        json={"episode_uuid": str(uuid4()), "episode_outcome": "success"},
    )

    assert failure.status_code == 200
    assert failure.json()["episode_outcome"] == "failure"
    assert mismatch.status_code == 409
    assert mismatch.json() == {"detail": "episode_uuid_mismatch"}


@pytest.mark.parametrize("invalid_id", [True, 1.0, "1"])
def test_intervention_identity_is_strictly_integer(tmp_path: Path, invalid_id: object):
    """Catches Pydantic coercion bypassing immutable interval identity checks."""
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    client = TestClient(
        create_app(recorder=FakeRecorder(), label_store=LabelStore(tmp_path))
    )

    response = client.put(
        f"/api/episodes/{episode_uuid}/labels",
        json={
            "episode_uuid": str(episode_uuid),
            "interventions": [
                {"side": "left", "intervention_id": invalid_id, "quality": "good"}
            ],
        },
    )

    assert response.status_code == 422


def test_label_write_failure_keeps_episode_and_hides_internal_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches cleanup deleting a rollout or exception details leaking host paths."""
    episode_uuid = uuid4()
    episode_path = _write_episode(tmp_path, episode_uuid)
    store = LabelStore(tmp_path)
    client = TestClient(create_app(recorder=FakeRecorder(), label_store=store))

    def fail_update(*_args, **_kwargs):
        raise OSError(f"disk failure at {tmp_path}/secret")

    monkeypatch.setattr(store, "update_labels", fail_update)
    response = client.put(
        f"/api/episodes/{episode_uuid}/labels",
        json={"episode_uuid": str(episode_uuid), "operator_note": "keep episode"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "label_write_failed"}
    assert episode_path.exists()
    assert str(tmp_path) not in response.text


def test_unexpected_recorder_exception_has_stable_status_without_path(tmp_path: Path):
    """Catches unstable 500 responses exposing server filesystem details."""
    recorder = FakeRecorder()

    def fail_start(_request):
        raise OSError(f"cannot create {tmp_path}/secret/episode.hdf5")

    recorder.start = fail_start  # type: ignore[method-assign]
    client = TestClient(create_app(recorder=recorder, label_store=LabelStore(tmp_path)))

    response = client.post("/api/episodes/start", json=_start_payload())

    assert response.status_code == 500
    assert response.json() == {"detail": "recorder_start_failed"}
    assert str(tmp_path) not in response.text


def test_public_status_never_exposes_paths_or_raw_errors(tmp_path: Path):
    """Catches successful polling bypassing exception-response sanitization."""
    recorder = FakeRecorder()

    def unsafe_status():
        return {
            "state": "error",
            "path": f"{tmp_path}/private/episode_000001.hdf5.incomplete",
            "error": f"writer failed at {tmp_path}/private",
            "last_error": f"secret {tmp_path}",
            "last_start_error": "camera driver details",
            "frames_sampled": 3,
            "frames_written": 2,
            "completion_state": "error",
            "publication_status": "failed",
        }

    recorder.status = unsafe_status  # type: ignore[method-assign]
    client = TestClient(create_app(recorder=recorder, label_store=LabelStore(tmp_path)))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "state": "error",
        "active": False,
        "completion_state": "error",
        "frames_sampled": 3,
        "frames_written": 2,
        "publication_status": "failed",
        "episode_file": "episode_000001.hdf5.incomplete",
        "error_code": "recorder_error",
        "ros_state": "stopped",
        "ros_error_code": None,
        "handover_mode": "unknown",
        "control_source_left": "unknown",
        "control_source_right": "unknown",
    }
    assert str(tmp_path) not in response.text
    assert "camera driver" not in response.text


def test_status_reports_received_latched_handover_mode(tmp_path: Path):
    """Catches the UI showing unknown after Task2's latched state stops changing."""
    cache = LatestMessageCache()
    cache.put("handover_mode", "policy", source_stamp=1.0, arrival_stamp=1.0)
    client = TestClient(
        create_app(
            recorder=FakeRecorder(),
            label_store=LabelStore(tmp_path),
            cache=cache,
        )
    )

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["handover_mode"] == "policy"
    assert response.json()["control_source_left"] == "policy"
    assert response.json()["control_source_right"] == "policy"


@pytest.mark.parametrize(
    "payload",
    [
        {"label_schema_version": True},
        {"label_schema_version": 2},
        {"label_updated_at": "2026-08-11T00:00:00Z"},
    ],
)
def test_label_update_rejects_non_strict_or_server_owned_fields(
    tmp_path: Path, payload: dict[str, object]
):
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    client = TestClient(
        create_app(recorder=FakeRecorder(), label_store=LabelStore(tmp_path))
    )

    response = client.put(
        f"/api/episodes/{episode_uuid}/labels",
        json={"episode_uuid": str(episode_uuid), **payload},
    )

    assert response.status_code == 422


def test_operational_stop_failure_is_not_reported_as_duplicate_stop(tmp_path: Path):
    """Catches writer/shutdown failures being mislabeled as a lifecycle conflict."""
    recorder = FakeRecorder()
    recorder.state = "recording"

    def fail_stop():
        recorder.state = "error"
        raise RecorderError(f"writer_error: {tmp_path}/private")

    recorder.stop = fail_stop  # type: ignore[method-assign]
    client = TestClient(create_app(recorder=recorder, label_store=LabelStore(tmp_path)))

    response = client.post("/api/episodes/stop")

    assert response.status_code == 500
    assert response.json() == {"detail": "recorder_stop_failed"}
    assert str(tmp_path) not in response.text


def test_runtime_and_http_test_dependencies_are_declared():
    """Catches clean installations missing modules imported by the API and tests."""
    pyproject = tomllib.loads(
        (Path(__file__).parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = pyproject["project"]["dependencies"]
    names = {
        item.split("<", 1)[0].split(">", 1)[0].split("=", 1)[0] for item in dependencies
    }
    test_dependencies = pyproject["dependency-groups"]["dev"]
    test_names = {
        item.split("<", 1)[0].split(">", 1)[0].split("=", 1)[0]
        for item in test_dependencies
    }

    assert names >= {
        "fastapi",
        "h5py",
        "numpy",
        "pydantic",
        "uvicorn",
    }
    assert {"pytest", "tomli"} <= test_names
    assert "httpx" in names | test_names


def test_lifespan_starts_bridge_with_the_recorder_cache_and_unregisters(tmp_path: Path):
    """Catches production subscriptions filling a cache different from the recorder's."""
    cache = LatestMessageCache()

    class CacheAwareRecorder(FakeRecorder):
        def start(self, request):
            assert cache.snapshot(now=5.0).get("handover_mode") == "policy"
            return super().start(request)

    class FillingBridge:
        def __init__(self, received_cache):
            assert received_cache is cache
            self.stopped = False

        def start(self):
            image = np.zeros((2, 2, 3), dtype=np.uint8)
            for key in ("camera_high", "camera_left", "camera_right"):
                cache.put(key, image, source_stamp=5.0, arrival_stamp=5.0)
            cache.put("handover_mode", "policy", source_stamp=5.0, arrival_stamp=5.0)
            cache.put("teach_left", False, source_stamp=5.0, arrival_stamp=5.0)
            cache.put("teach_right", False, source_stamp=5.0, arrival_stamp=5.0)

        def shutdown(self):
            self.stopped = True

        def status(self):
            return {"state": "ready", "error_code": None}

    created: list[FillingBridge] = []

    def bridge_factory(received_cache):
        bridge = FillingBridge(received_cache)
        created.append(bridge)
        return bridge

    application = create_app(
        recorder=CacheAwareRecorder(),
        label_store=LabelStore(tmp_path),
        cache=cache,
        ros_bridge_factory=bridge_factory,
        monotonic=lambda: 5.0,
    )

    with TestClient(application) as client:
        assert (
            client.post("/api/episodes/start", json=_start_payload()).status_code == 200
        )
    assert created[0].stopped is True


def test_ros_bridge_failure_degrades_health_and_blocks_start_without_crashing(
    tmp_path: Path,
):
    """Catches unavailable ROS appearing healthy or raising through HTTP requests."""

    class FailedBridge:
        def __init__(self, _cache):
            pass

        def start(self):
            pass

        def shutdown(self):
            pass

        def status(self):
            return {"state": "not_ready", "error_code": "ros_unavailable"}

    application = create_app(
        recorder=FakeRecorder(),
        label_store=LabelStore(tmp_path),
        cache=LatestMessageCache(),
        ros_bridge_factory=FailedBridge,
    )

    with TestClient(application) as client:
        assert client.get("/healthz").json() == {
            "status": "not_ready",
            "error_code": "ros_unavailable",
        }
        response = client.post("/api/episodes/start", json=_start_payload())
        assert response.status_code == 503
        assert response.json() == {"detail": "recorder_not_ready"}


def test_start_value_error_reports_safe_error_type_without_path(tmp_path: Path) -> None:
    recorder = FakeRecorder()

    def fail_start(_request):
        raise ValueError(f"bad parameter at {tmp_path}/private")

    recorder.start = fail_start  # type: ignore[method-assign]
    client = TestClient(create_app(recorder=recorder, label_store=LabelStore(tmp_path)))

    response = client.post("/api/episodes/start", json=_start_payload())

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_start_request: ValueError"}
    assert str(tmp_path) not in response.text


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_terminal_outcome_then_operator_nodes_matches_rlt_finalization(tmp_path, outcome):
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    client = TestClient(create_app(recorder=FakeRecorder(), label_store=LabelStore(tmp_path)))
    base = "/api/episodes/" + str(episode_uuid)
    assert client.post(base + "/outcome", json={"episode_uuid": str(episode_uuid), "outcome": outcome}).status_code == 200
    nodes = [{"frame_index": 0, "node_kind": "pause"}]
    reply = client.put(base + "/labels", json={"episode_uuid": str(episode_uuid), "operator_nodes": nodes})
    assert reply.status_code == 200, reply.text
    saved = client.get(base + "/labels").json()
    assert saved["episode_outcome"] == outcome
    assert saved["operator_nodes"] == nodes


def test_save_without_result_accepts_operator_save_and_rejects_malformed_nodes(tmp_path):
    episode_uuid = uuid4()
    _write_episode(tmp_path, episode_uuid)
    client = TestClient(create_app(recorder=FakeRecorder(), label_store=LabelStore(tmp_path)))
    url = "/api/episodes/" + str(episode_uuid) + "/labels"
    body = {"episode_uuid": str(episode_uuid), "episode_outcome": "unknown",
            "episode_quality": "uncertain", "termination_reason": "operator_save", "keep_for_training": "false"}
    reply = client.put(url, json=body)
    assert reply.status_code == 200, reply.text
    for node in [{"frame_index": True, "node_kind": "pause"}, {"frame_index": 99, "node_kind": "pause"},
                 {"frame_index": 0, "node_kind": "unsafe"}]:
        assert client.put(url, json={"episode_uuid": str(episode_uuid), "operator_nodes": [node]}).status_code == 422

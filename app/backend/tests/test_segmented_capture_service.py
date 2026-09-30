from __future__ import annotations

import json
import time
from types import SimpleNamespace
from collections import namedtuple
from pathlib import Path
from threading import Event, RLock, Thread

import pytest

import cv2
import h5py
import numpy as np

from capture_core.config import RecorderConfig
from capture_core.recorder import RolloutRecorder
from capture_core.ros_cache import LatestMessageCache
from capture_core.schema import EpisodeIdentity
from segmented_capture.capture_service import SegmentedCaptureError, SegmentedCaptureService
import segmented_capture.capture_service as capture_service
from segmented_capture.state import CaptureState
from segmented_capture.ports import CaptureGate

DiskUsage = namedtuple("DiskUsage", "total used free")
GIB = 1024**3


def test_delete_completed_prior_episode_while_new_episode_records(tmp_path, monkeypatch):
    active_uuid = "active-uuid"
    old_uuid = "old-uuid"
    service = object.__new__(SegmentedCaptureService)
    service._lock = RLock()
    service._reducer = SimpleNamespace(snapshot=lambda: SimpleNamespace(capture_state=CaptureState.RECORDING))
    service._identity = SimpleNamespace(episode_uuid=active_uuid)
    service.sidecar_root = tmp_path / ".segments"
    sidecar = service.sidecar_root / "plug_cycle/expert/expert80" / old_uuid
    service._episode_root = lambda episode_uuid, *, data_root: sidecar
    service.read_episode = lambda episode_uuid, *, data_root: {
        "commit_state": "complete", "task_id": "plug_cycle", "model_id": "expert",
        "dataset_round": "expert80", "episode_index": 1,
        "source_hdf5_relative": "plug_cycle/expert/expert80/episode_000001.hdf5",
        "training_frame_count": 10,
    }
    monkeypatch.setattr(capture_service, "permanently_delete_episode", lambda *_args, **_kwargs:
                        SimpleNamespace(episode_uuid=old_uuid, episode_index=1, deleted_at="now"))
    assert service.delete_episode(old_uuid, data_root=tmp_path)["episode_uuid"] == old_uuid
    with pytest.raises(SegmentedCaptureError, match="episode_active"):
        service.delete_episode(active_uuid, data_root=tmp_path)


class FakeRecorder:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.frames_sampled = 0
        self.frames_written = 0

    def start(self, request):
        self.request = request
        self.started = True
        return self.status()

    def stop(self):
        self.stopped = True
        return Path("episode_000001.hdf5")

    def status(self):
        return {
            "state": "recording" if self.started and not self.stopped else "stopped",
            "frames_sampled": self.frames_sampled,
            "frames_written": self.frames_written,
        }


def ready_cache(mode: str) -> LatestMessageCache:
    cache = LatestMessageCache()
    now = 10.0
    for key, value in {
        "camera_high": np.full((4, 5, 3), 10, dtype=np.uint8),
        "camera_left": np.full((4, 5, 3), 20, dtype=np.uint8),
        "camera_right": np.full((4, 5, 3), 30, dtype=np.uint8),
        "handover_mode": mode,
    }.items():
        cache.put(key, value, source_stamp=now, arrival_stamp=now)
    return cache


def identity() -> EpisodeIdentity:
    return EpisodeIdentity(
        "plug_cycle", "expert", "manual", "expert80", episode_index=1
    )


def test_paused_gap_writes_keyframe_but_no_training_frame(project_tmp) -> None:
    recorder = FakeRecorder()
    service = SegmentedCaptureService(
        recorder=recorder,
        cache=ready_cache("manual:left"),
        gate=CaptureGate(enabled=False),
        sidecar_root=project_tmp,
        clock=lambda: 10.0,
        monitor=False,
    )
    service.start(identity(), data_root=project_tmp)
    recorder.frames_sampled = recorder.frames_written = 1

    paused = service.observe_mode("policy")
    paused_again = service.observe_mode("policy")
    result = service.stop()

    payload = json.loads(result.sidecar.read_text())
    assert paused["capture_state"] == "paused"
    assert paused_again["node_count"] == paused["node_count"]
    assert payload["training_frame_count"] == 1
    assert [node["kind"] for node in payload["nodes"]] == ["start", "end"]
    assert len(list(result.episode_root.glob("nodes/node*/*.jpg"))) == 6


def test_first_resume_replaces_start_keyframe_with_post_teach_image(project_tmp) -> None:
    recorder = FakeRecorder()
    cache = ready_cache("policy")
    service = SegmentedCaptureService(
        recorder=recorder,
        cache=cache,
        gate=CaptureGate(enabled=False),
        sidecar_root=project_tmp,
        clock=lambda: 10.0,
        monitor=False,
    )
    service.start(identity(), data_root=project_tmp)
    episode_root = next(project_tmp.glob(f"*/*/*/{service.status()['episode_uuid']}"))
    before = cv2.imread(str(episode_root / "nodes/node0001/camera_high.jpg"))
    cache.put("camera_high", np.full((4, 5, 3), 77, dtype=np.uint8), source_stamp=10.0, arrival_stamp=10.0)

    resumed = service.observe_mode("manual:left")

    after = cv2.imread(str(episode_root / "nodes/node0001/camera_high.jpg"))
    assert resumed["node_count"] == 1
    assert resumed["nodes"][0]["capture_state"] == "recording"
    assert int(before.mean()) == 10
    assert int(after.mean()) == 77


def test_middle_resume_reuses_pause_boundary_and_pause_keyframe(project_tmp) -> None:
    recorder = FakeRecorder()
    cache = ready_cache("manual:left")
    service = SegmentedCaptureService(
        recorder=recorder,
        cache=cache,
        gate=CaptureGate(enabled=False),
        sidecar_root=project_tmp,
        clock=lambda: 10.0,
        monitor=False,
    )
    service.start(identity(), data_root=project_tmp)
    cache.put("camera_high", np.full((4, 5, 3), 55, dtype=np.uint8), source_stamp=10.0, arrival_stamp=10.0)
    service.observe_mode("policy")
    cache.put("camera_high", np.full((4, 5, 3), 99, dtype=np.uint8), source_stamp=10.0, arrival_stamp=10.0)

    resumed = service.observe_mode("manual:left")

    episode_root = next(project_tmp.glob(f"*/*/*/{resumed['episode_uuid']}"))
    boundary = cv2.imread(str(episode_root / "nodes/node0002/camera_high.jpg"))
    assert resumed["node_count"] == 2
    assert resumed["nodes"][-1]["capture_state"] == "recording"
    assert int(boundary.mean()) == 55
    assert not (episode_root / "nodes/node0003").exists()


def test_two_exit_edges_merge_into_one_pause_node(project_tmp) -> None:
    recorder = FakeRecorder()
    service = SegmentedCaptureService(
        recorder=recorder,
        cache=ready_cache("manual:left+right"),
        gate=CaptureGate(enabled=False),
        sidecar_root=project_tmp,
        clock=lambda: 10.0,
        monitor=False,
    )
    service.start(identity(), data_root=project_tmp)

    service.observe_mode("manual:left")
    snapshot = service.observe_mode("policy")

    assert snapshot["capture_state"] == "paused"
    assert snapshot["node_count"] == 2
    assert snapshot["nodes"][-1]["merged_sides"] == ["right", "left"]


def test_ui_resume_then_teach_enter_merge_cross_source(project_tmp) -> None:
    recorder = FakeRecorder()
    service = SegmentedCaptureService(
        recorder=recorder,
        cache=ready_cache("policy"),
        gate=CaptureGate(enabled=False),
        sidecar_root=project_tmp,
        clock=lambda: 10.0,
        monitor=False,
    )
    service.start(identity(), data_root=project_tmp)

    service.resume()
    snapshot = service.observe_mode("manual:left")

    assert snapshot["node_count"] == 1
    assert snapshot["nodes"][-1]["primary_trigger"] == "start"
    assert snapshot["nodes"][-1]["merged_sides"] == ["left"]


def test_marker_keeps_recording_and_is_visible_in_status(project_tmp) -> None:
    recorder = FakeRecorder()
    service = SegmentedCaptureService(
        recorder=recorder,
        cache=ready_cache("manual:left"),
        gate=CaptureGate(enabled=False),
        sidecar_root=project_tmp,
        clock=lambda: 10.0,
        monitor=False,
    )
    service.start(identity(), data_root=project_tmp)

    snapshot = service.marker()

    assert snapshot["capture_state"] == "recording"
    assert snapshot["nodes"][-1]["kind"] == "marker"
    assert service.gate.is_open()


def _full_ready_cache(mode: str) -> LatestMessageCache:
    cache = LatestMessageCache()
    now = time.monotonic()
    for key, value in {
        "camera_high": np.full((4, 5, 3), 10, dtype=np.uint8),
        "camera_left": np.full((4, 5, 3), 20, dtype=np.uint8),
        "camera_right": np.full((4, 5, 3), 30, dtype=np.uint8),
        "handover_mode": mode,
    }.items():
        cache.put(key, value, source_stamp=now, arrival_stamp=now)
    joints = {
        "position": np.arange(7, dtype=np.float32),
        "velocity": np.arange(7, dtype=np.float32),
        "effort": np.arange(7, dtype=np.float32),
    }
    for key in ("front_left", "front_right", "rear_left", "rear_right"):
        cache.put(key, joints, source_stamp=now, arrival_stamp=now)
    for key in ("policy_left", "policy_right", "coordinator_left", "coordinator_right"):
        cache.put(key, joints, source_stamp=now, arrival_stamp=now)
    cache.put("handover_fault", "", source_stamp=now, arrival_stamp=now)
    cache.put("teach_left", True, source_stamp=now, arrival_stamp=now)
    cache.put("teach_right", False, source_stamp=now, arrival_stamp=now)
    return cache


def _wait_written(recorder: RolloutRecorder, count: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if int(recorder.status()["frames_written"]) >= count:
            return
        time.sleep(0.005)
    raise AssertionError(recorder.status())


def _keep_cache_fresh(cache: LatestMessageCache):
    stop = Event()

    def refresh() -> None:
        while not stop.wait(0.01):
            now = time.monotonic()
            snapshot = cache.snapshot(now)
            for key, entry in snapshot.entries.items():
                cache.put(key, entry.value, source_stamp=now, arrival_stamp=now)

    thread = Thread(target=refresh, daemon=True)
    thread.start()
    return stop, thread


def test_real_writer_pauses_and_resumes_one_contiguous_hdf5(project_tmp) -> None:
    cache = _full_ready_cache("manual:left")
    refresh_stop, refresh_thread = _keep_cache_fresh(cache)
    gate = CaptureGate(enabled=False)
    data_root = project_tmp / "raw"
    config = RecorderConfig(
        data_root=data_root,
        sample_rate_hz=30,
        max_duration_seconds=2,
        min_free_disk_bytes=1,
        stop_free_disk_bytes=1,
    )
    recorder = RolloutRecorder(
        config,
        cache,
        capture_gate=gate,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    service = SegmentedCaptureService(
        recorder=recorder,
        cache=cache,
        gate=gate,
        sidecar_root=project_tmp / "segments",
        monitor=False,
    )
    service.start(identity(), data_root=data_root)
    _wait_written(recorder, 3)
    now = time.monotonic()
    for key, value in {
        "camera_high": 10,
        "camera_left": 20,
        "camera_right": 30,
    }.items():
        cache.put(
            key,
            np.full((4, 5, 3), value, dtype=np.uint8),
            source_stamp=now,
            arrival_stamp=now,
        )
    service.observe_mode("policy")
    frozen = int(recorder.status()["frames_written"])
    time.sleep(0.08)
    assert recorder.status()["frames_written"] == frozen

    now = time.monotonic()
    for key, value in {
        "camera_high": 10,
        "camera_left": 20,
        "camera_right": 30,
    }.items():
        cache.put(
            key,
            np.full((4, 5, 3), value, dtype=np.uint8),
            source_stamp=now,
            arrival_stamp=now,
        )
    service.observe_mode("manual:left")
    _wait_written(recorder, frozen + 3)
    now = time.monotonic()
    for key, value in {
        "camera_high": 10,
        "camera_left": 20,
        "camera_right": 30,
    }.items():
        cache.put(
            key,
            np.full((4, 5, 3), value, dtype=np.uint8),
            source_stamp=now,
            arrival_stamp=now,
        )
    result = service.stop()
    refresh_stop.set()
    refresh_thread.join(timeout=1)

    hdf5_path = Path(recorder.status()["path"])
    with h5py.File(hdf5_path, "r") as episode:
        assert episode["action"].shape[0] == frozen + 3
        assert np.array_equal(
            episode["rollout/frame_index"][:], np.arange(frozen + 3)
        )
    sidecar = json.loads(result.sidecar.read_text())
    assert len(sidecar["active_segments"]) == 2
    assert sidecar["training_frame_count"] == frozen + 3
    assert sidecar["episode_index"] == 1
    assert sidecar["source_hdf5_relative"] == (
        "plug_cycle/expert/expert80/episode_000001.hdf5"
    )
    assert service.list_episodes()[0]["episode_index"] == 1

    deleted = service.delete_episode(sidecar["episode_uuid"], data_root=data_root)

    assert deleted["episode_index"] == 1
    assert not hdf5_path.exists()
    assert not result.episode_root.exists()


@pytest.mark.parametrize('stuck',[False,True])
def test_defer_closes_writer_despite_failed_sidecar_but_refuses_live_worker(project_tmp,monkeypatch,stuck):
    recorder=FakeRecorder()
    service=SegmentedCaptureService(recorder=recorder,cache=ready_cache('manual:left'),
        gate=CaptureGate(enabled=False),sidecar_root=project_tmp,clock=lambda:10.,monitor=False)
    service.start(identity(),data_root=project_tmp)
    snapshot=service.status()
    def failed_stop():raise OSError('sidecar interruption')
    monkeypatch.setattr(service,'stop',failed_stop)
    status=recorder.status
    recorder.status=lambda:{**status(),'writer_thread_alive':stuck}
    if stuck:
        with pytest.raises(SegmentedCaptureError,match='still_active'):service.defer()
        assert service.status()['episode_uuid']==snapshot['episode_uuid']
    else:
        result=service.defer()
        assert result['deferred'] and result['recording_retained']['episode_uuid']==snapshot['episode_uuid']
        assert recorder.stopped and not service.active
        assert not service.gate.is_open()

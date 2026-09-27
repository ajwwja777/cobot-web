from __future__ import annotations

import time
from collections import namedtuple
from pathlib import Path

import numpy as np

from capture_core.config import RecorderConfig
from capture_core.recorder import RecorderStartRequest, RolloutRecorder
from capture_core.ros_cache import LatestMessageCache
from capture_core.schema import EpisodeIdentity
from segmented_capture.ports import CaptureGate

DiskUsage = namedtuple("DiskUsage", "total used free")
GIB = 1024**3


def ready_cache() -> LatestMessageCache:
    now = time.monotonic()
    cache = LatestMessageCache()
    for key in ("camera_high", "camera_left", "camera_right"):
        cache.put(key, np.zeros((2, 3, 3), dtype=np.uint8), source_stamp=now, arrival_stamp=now)
    joints = {
        "position": np.arange(7, dtype=np.float32),
        "velocity": np.arange(7, dtype=np.float32),
        "effort": np.arange(7, dtype=np.float32),
    }
    for key in ("front_left", "front_right", "rear_left", "rear_right"):
        cache.put(key, joints, source_stamp=now, arrival_stamp=now)
    for key in ("policy_left", "policy_right", "coordinator_left", "coordinator_right"):
        cache.put(key, joints, source_stamp=now, arrival_stamp=now)
    cache.put("handover_mode", "policy", source_stamp=now, arrival_stamp=now)
    cache.put("handover_fault", "", source_stamp=now, arrival_stamp=now)
    cache.put("teach_left", False, source_stamp=now, arrival_stamp=now)
    cache.put("teach_right", False, source_stamp=now, arrival_stamp=now)
    return cache


def identity() -> EpisodeIdentity:
    return EpisodeIdentity("plug_cycle", "expert", "manual", "expert80", episode_index=1)


def wait_frames(recorder: RolloutRecorder, count: int, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if int(recorder.status()["frames_written"]) >= count:
            return
        time.sleep(0.005)
    raise AssertionError(recorder.status())


def test_closed_gate_records_no_frames_until_opened(project_tmp: Path) -> None:
    gate = CaptureGate(enabled=False)
    config = RecorderConfig(
        data_root=project_tmp,
        sample_rate_hz=100,
        max_duration_seconds=2,
        min_free_disk_bytes=1,
        stop_free_disk_bytes=1,
    )
    recorder = RolloutRecorder(
        config,
        ready_cache(),
        capture_gate=gate,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(identity(), max_timesteps=2))
    time.sleep(0.05)
    assert recorder.status()["frames_written"] == 0

    gate.open()
    wait_frames(recorder, 2)
    path = recorder.stop()

    assert path is not None and path.exists()
    assert recorder.status()["frames_written"] == 2


def test_gate_can_pause_without_stopping_recorder(project_tmp: Path) -> None:
    gate = CaptureGate(enabled=True)
    config = RecorderConfig(
        data_root=project_tmp,
        sample_rate_hz=20,
        max_duration_seconds=2,
        min_free_disk_bytes=1,
        stop_free_disk_bytes=1,
    )
    recorder = RolloutRecorder(
        config,
        ready_cache(),
        capture_gate=gate,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(identity(), max_timesteps=3))
    wait_frames(recorder, 1)
    gate.close()
    frozen = int(recorder.status()["frames_sampled"])
    time.sleep(0.12)

    assert recorder.status()["state"] == "recording"
    # A pre-close frame may still drain to the writer; no post-close sampling is allowed.
    assert recorder.status()["frames_sampled"] == frozen

    gate.open()
    wait_frames(recorder, 3)
    recorder.stop()

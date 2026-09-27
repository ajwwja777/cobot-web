"""Behavioral tests for recorder preflight and threaded acquisition."""

from __future__ import annotations

import os
import time
from collections import namedtuple
from pathlib import Path
from threading import Event, Thread
from typing import ClassVar

import h5py
import numpy as np
import pytest

from capture_core.config import RecorderConfig
from capture_core.hdf5_writer import Hdf5EpisodeWriter
from capture_core.recorder import (
    RecorderError,
    RecorderStartRequest,
    RolloutRecorder,
)
from capture_core.ros_cache import LatestMessageCache
from capture_core.sampler import FrameSampler
from capture_core.schema import EpisodeIdentity
from capture_core.validation import PreflightError, validate_preflight

DiskUsage = namedtuple("DiskUsage", "total used free")
GIB = 1024**3


def _identity(index: int = 0) -> EpisodeIdentity:
    return EpisodeIdentity(
        task_id="in_the_pot",
        model_id="pi05",
        checkpoint_id="step_2000",
        dataset_round="round_001",
        episode_index=index,
    )


def _ready_cache(
    now: float | None = None, *, commands: bool = True, handover_mode: bool = True
) -> LatestMessageCache:
    now = time.monotonic() if now is None else now
    cache = LatestMessageCache()
    for key in ("camera_high", "camera_left", "camera_right"):
        cache.put(
            key,
            np.zeros((2, 3, 3), dtype=np.uint8),
            source_stamp=now,
            arrival_stamp=now,
        )
    joints = {
        "position": np.arange(7, dtype=np.float32),
        "velocity": np.arange(7, dtype=np.float32),
        "effort": np.arange(7, dtype=np.float32),
    }
    for key in ("front_left", "front_right", "rear_left", "rear_right"):
        cache.put(key, joints, source_stamp=now, arrival_stamp=now)
    if handover_mode:
        cache.put("handover_mode", "policy", source_stamp=now, arrival_stamp=now)
    cache.put("handover_fault", "", source_stamp=now, arrival_stamp=now)
    cache.put("teach_left", False, source_stamp=now, arrival_stamp=now)
    cache.put("teach_right", False, source_stamp=now, arrival_stamp=now)
    if commands:
        for key in (
            "policy_left",
            "policy_right",
            "coordinator_left",
            "coordinator_right",
        ):
            cache.put(
                key,
                np.arange(7, dtype=np.float32),
                source_stamp=now,
                arrival_stamp=now,
            )
    return cache


def _wait_for_state(recorder: RolloutRecorder, state: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = recorder.status()
        if status["state"] == state:
            return status
        time.sleep(0.005)
    raise AssertionError(f"recorder did not reach {state}: {recorder.status()}")


def _config(tmp_path: Path, *, capacity: int = 8) -> RecorderConfig:
    return RecorderConfig(
        data_root=tmp_path,
        sample_rate_hz=200.0,
        max_duration_seconds=1.0,
        min_free_disk_bytes=20 * GIB,
        stop_free_disk_bytes=10 * GIB,
        writer_queue_capacity=capacity,
    )


def test_preflight_requires_disk_writable_directory_and_only_startup_streams(
    tmp_path: Path,
):
    """Catches unsafe starts and policy/coordinator being incorrectly startup-gated."""
    snapshot = _ready_cache(now=10.0, commands=False).snapshot(now=10.0)
    result = validate_preflight(
        tmp_path / "new_data",
        snapshot,
        min_free_disk_bytes=20 * GIB,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    assert result.is_dir()

    with pytest.raises(PreflightError, match="disk_space_low"):
        validate_preflight(
            tmp_path / "low_disk",
            snapshot,
            min_free_disk_bytes=20 * GIB,
            disk_usage=lambda _path: DiskUsage(100 * GIB, 81 * GIB, 19 * GIB),
        )

    missing_mode = _ready_cache(now=10.0, handover_mode=False)
    with pytest.raises(PreflightError, match="handover_mode"):
        validate_preflight(
            tmp_path / "missing_mode",
            missing_mode.snapshot(now=10.0),
            min_free_disk_bytes=1,
            disk_usage=lambda _path: DiskUsage(100, 50, 50),
        )

    not_a_directory = tmp_path / "regular_file"
    not_a_directory.write_text("occupied")
    with pytest.raises(PreflightError, match="not_writable"):
        validate_preflight(
            not_a_directory,
            snapshot,
            min_free_disk_bytes=1,
            disk_usage=lambda _path: DiskUsage(100, 50, 50),
        )


def test_preflight_accepts_received_latched_handover_mode(tmp_path: Path):
    """Catches treating Task2's latched mode topic as a one-second heartbeat."""
    cache = _ready_cache(now=10.0)
    cache.put("handover_mode", "policy", source_stamp=8.0, arrival_stamp=8.0)

    result = validate_preflight(
        tmp_path / "latched_mode",
        cache.snapshot(now=10.0),
        min_free_disk_bytes=1,
        disk_usage=lambda _path: DiskUsage(100, 50, 50),
    )

    assert result.is_dir()


def test_recorder_streams_on_writer_thread_and_finalizes_idempotently(tmp_path: Path):
    """Catches synchronous callback writes or duplicate finalization during shutdown."""
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(_identity(), max_timesteps=2))
    status = _wait_for_state(recorder, "stopped")
    path = Path(status["path"])

    assert status["frames_written"] == 2
    assert recorder.stop() == path
    assert recorder.stop() == path
    with h5py.File(path, "r") as episode:
        assert episode["action"].shape == (2, 14)
        assert episode.attrs["completion_state"] == "complete"


def test_recorder_uses_request_data_root_for_preflight_writer_and_runtime_disk(
    tmp_path: Path,
):
    configured = tmp_path / "configured"
    selected = tmp_path / "selected"
    recorder = RolloutRecorder(
        _config(configured),
        _ready_cache(),
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )

    recorder.start(
        RecorderStartRequest(
            _identity(12), max_timesteps=1, data_root=selected
        )
    )
    _wait_for_state(recorder, "stopped")
    path = recorder.stop()

    assert path is not None
    path.resolve().relative_to(selected.resolve())
    assert not configured.exists()


def test_policy_and_coordinator_may_be_invalid_at_start_and_are_saved_as_such(
    tmp_path: Path,
):
    """Catches startup gaps being rejected or stale commands becoming valid labels."""
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(commands=False),
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(_identity(6), max_timesteps=1))
    _wait_for_state(recorder, "stopped")
    path = recorder.stop()

    with h5py.File(path, "r") as episode:
        assert np.isnan(episode["action"][0]).all()
        assert np.isnan(episode["rollout/policy_command_submitted"][0]).all()
        assert not bool(episode["rollout/valid_mask/coordinator_command"][0])
        assert not bool(episode["rollout/valid_mask/policy_command_submitted"][0])


def test_immediate_stop_never_publishes_a_zero_frame_episode(tmp_path: Path):
    """Catches shutdown racing acquisition and finalizing an empty dataset."""
    release_sample = Event()

    class BlockingSampler:
        def sample(self, *_args):
            release_sample.wait(timeout=2.0)
            raise RuntimeError("sampling stopped before first frame")

    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        sampler=BlockingSampler(),
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(_identity(7), max_timesteps=2))
    release_sample.set()
    with pytest.raises(RecorderError, match="acquisition_error"):
        recorder.stop()
    status = recorder.status()
    assert status["state"] == "error"
    assert Path(status["path"]).suffix == ".incomplete"


class _LifecycleWriter:
    instances: ClassVar[list[_LifecycleWriter]] = []

    def __init__(self, data_root, identity, max_timesteps, **_kwargs):
        del max_timesteps
        parent = (
            Path(data_root)
            / identity.task_id
            / identity.model_id
            / identity.dataset_round
        )
        parent.mkdir(parents=True, exist_ok=True)
        self.incomplete_path = (
            parent / f"episode_{identity.episode_index:06d}.hdf5.incomplete"
        )
        self.final_path = parent / f"episode_{identity.episode_index:06d}.hdf5"
        self.incomplete_path.touch()
        self.finalized = Event()
        self.appended = Event()
        self.append_count = 0
        self.abort_reason: str | None = None
        self.__class__.instances.append(self)

    def append(self, _frame):
        self.append_count += 1
        self.appended.set()

    def abort(self, reason):
        self.abort_reason = reason
        return self.incomplete_path

    def finalize(self, _summary, **_kwargs):
        self.incomplete_path.replace(self.final_path)
        self.finalized.set()
        return self.final_path


def test_transient_start_failure_returns_idle_and_can_be_retried(tmp_path: Path):
    """Catches a constructor failure permanently poisoning an unused recorder."""
    attempts = 0

    def fail_once_factory(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("transient writer construction failure")
        return _LifecycleWriter(*args, **kwargs)

    _LifecycleWriter.instances.clear()
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        writer_factory=fail_once_factory,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    request = RecorderStartRequest(_identity(36), max_timesteps=1)

    with pytest.raises(OSError, match="transient writer construction failure"):
        recorder.start(request)

    failed_status = recorder.status()
    assert failed_status["state"] == "idle"
    assert failed_status["error"] is None
    assert "transient writer construction failure" in failed_status["last_error"]
    assert "transient writer construction failure" in failed_status["last_start_error"]
    assert failed_status["writer_thread_alive"] is False
    assert failed_status["path"] is None

    recorder.start(request)
    completed_status = _wait_for_state(recorder, "stopped")
    assert attempts == 2
    assert completed_status["error"] is None
    assert completed_status["completion_state"] == "complete"
    assert (
        "transient writer construction failure"
        in (completed_status["last_start_error"])
    )
    assert Path(completed_status["path"]).exists()


def test_stop_during_starting_cancels_before_any_worker_can_start(tmp_path: Path):
    """Catches start clearing a concurrent stop and launching workers afterward."""
    factory_entered = Event()
    release_factory = Event()

    def blocked_factory(*args, **kwargs):
        factory_entered.set()
        assert release_factory.wait(timeout=2.0)
        return _LifecycleWriter(*args, **kwargs)

    _LifecycleWriter.instances.clear()
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        writer_factory=blocked_factory,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    start_results: list[dict[str, object] | Exception] = []
    stop_results: list[Path | None | Exception] = []

    def start_recorder():
        try:
            start_results.append(
                recorder.start(RecorderStartRequest(_identity(32), max_timesteps=2))
            )
        except Exception as error:  # noqa: BLE001 - capture concurrent result
            start_results.append(error)

    def stop_recorder():
        try:
            stop_results.append(recorder.stop())
        except Exception as error:  # noqa: BLE001 - capture concurrent result
            stop_results.append(error)

    starter = Thread(target=start_recorder)
    stopper = Thread(target=stop_recorder)
    starter.start()
    assert factory_entered.wait(timeout=1.0)
    stopper.start()
    time.sleep(0.05)
    assert stopper.is_alive(), "stop must wait for an in-progress start to resolve"

    release_factory.set()
    starter.join(timeout=1.0)
    stopper.join(timeout=1.0)

    assert not starter.is_alive()
    assert not stopper.is_alive()
    assert len(_LifecycleWriter.instances) == 1
    writer = _LifecycleWriter.instances[0]
    assert len(start_results) == 1
    assert isinstance(start_results[0], RecorderError)
    assert "start_cancelled" in str(start_results[0])
    assert stop_results == [writer.incomplete_path]
    assert writer.abort_reason == "start_cancelled"
    assert not writer.final_path.exists()
    status = recorder.status()
    assert status["state"] == "stopped"
    assert status["completion_state"] == "cancelled"
    assert status["frames_sampled"] == status["frames_written"] == 0
    assert status["writer_thread_alive"] is False
    recorder.start(RecorderStartRequest(_identity(33), max_timesteps=1))
    retry_status = _wait_for_state(recorder, "stopped")
    assert retry_status["completion_state"] == "complete"
    assert len(_LifecycleWriter.instances) == 2


def test_stop_during_failing_start_succeeds_without_output_path(tmp_path: Path):
    """Catches stop raising merely because cancelled startup produced no writer."""
    factory_entered = Event()
    release_factory = Event()

    def failing_factory(*_args, **_kwargs):
        factory_entered.set()
        assert release_factory.wait(timeout=2.0)
        raise OSError("injected factory failure")

    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        writer_factory=failing_factory,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    start_results: list[dict[str, object] | Exception] = []
    stop_results: list[Path | None | Exception] = []

    def start_recorder():
        try:
            start_results.append(
                recorder.start(RecorderStartRequest(_identity(34), max_timesteps=2))
            )
        except Exception as error:  # noqa: BLE001 - capture concurrent result
            start_results.append(error)

    def stop_recorder():
        try:
            stop_results.append(recorder.stop())
        except Exception as error:  # noqa: BLE001 - capture concurrent result
            stop_results.append(error)

    starter = Thread(target=start_recorder)
    stopper = Thread(target=stop_recorder)
    starter.start()
    assert factory_entered.wait(timeout=1.0)
    stopper.start()
    time.sleep(0.05)
    assert stopper.is_alive(), "stop must wait for an in-progress start to resolve"

    release_factory.set()
    starter.join(timeout=1.0)
    stopper.join(timeout=1.0)

    assert not starter.is_alive()
    assert not stopper.is_alive()
    assert len(start_results) == 1
    assert isinstance(start_results[0], OSError)
    assert stop_results == [None]
    status = recorder.status()
    assert status["state"] == "stopped"
    assert status["completion_state"] == "cancelled"
    assert status["path"] is None
    assert status["writer_thread_alive"] is False


def test_stop_waits_for_inflight_sample_before_writer_can_finish(tmp_path: Path):
    """Catches writer finalization while acquisition still owns an in-flight sample."""
    entered = Event()
    release = Event()
    real_sampler = FrameSampler()

    class BlockingSampler:
        def sample(self, *args):
            entered.set()
            release.wait(timeout=2.0)
            return real_sampler.sample(*args)

    _LifecycleWriter.instances.clear()
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        sampler=BlockingSampler(),
        writer_factory=_LifecycleWriter,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(_identity(8), max_timesteps=2))
    assert entered.wait(timeout=1.0)
    stop_results: list[Path | Exception] = []

    def stop_recorder():
        try:
            stop_results.append(recorder.stop())
        except Exception as error:  # noqa: BLE001 - capture worker result
            stop_results.append(error)

    stopper = Thread(target=stop_recorder)
    stopper.start()
    try:
        finalized_before_release = _LifecycleWriter.instances[-1].finalized.wait(0.1)
    finally:
        release.set()
        stopper.join(timeout=1.0)

    writer = _LifecycleWriter.instances[-1]
    status = recorder.status()
    assert finalized_before_release is False
    assert not stopper.is_alive()
    assert writer.abort_reason == "empty_episode"
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    assert status["state"] == "stopped"
    assert status["completion_state"] == "aborted"
    assert status["frames_sampled"] == status["frames_written"] == 0
    assert stop_results == [writer.incomplete_path]


def test_acquisition_exception_can_never_race_writer_into_finalize(tmp_path: Path):
    """Catches an acquisition failure publishing after writer observes stop first."""
    second_entered = Event()
    release_failure = Event()
    real_sampler = FrameSampler()
    calls = 0

    class FailingSecondSampler:
        def sample(self, *args):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_sampler.sample(*args)
            second_entered.set()
            release_failure.wait(timeout=2.0)
            raise RuntimeError("injected acquisition failure")

    _LifecycleWriter.instances.clear()
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        sampler=FailingSecondSampler(),
        writer_factory=_LifecycleWriter,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(_identity(9), max_timesteps=5))
    writer = _LifecycleWriter.instances[-1]
    assert writer.appended.wait(timeout=1.0)
    assert second_entered.wait(timeout=1.0)
    release_failure.set()
    _wait_for_state(recorder, "error")
    with pytest.raises(RecorderError, match="injected acquisition failure"):
        recorder.stop()

    assert writer.abort_reason is not None
    assert writer.finalized.is_set() is False
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    status = recorder.status()
    assert status["frames_sampled"] == status["frames_written"] == 1


class _SlowWriter:
    instances: ClassVar[list[_SlowWriter]] = []

    def __init__(self, data_root, identity, max_timesteps, **_kwargs):
        del max_timesteps
        parent = (
            Path(data_root)
            / identity.task_id
            / identity.model_id
            / identity.dataset_round
        )
        parent.mkdir(parents=True, exist_ok=True)
        self.incomplete_path = (
            parent / f"episode_{identity.episode_index:06d}.hdf5.incomplete"
        )
        self.final_path = parent / f"episode_{identity.episode_index:06d}.hdf5"
        self.incomplete_path.touch()
        self.entered = Event()
        self.release = Event()
        self.appended = 0
        self.abort_reason: str | None = None
        self.__class__.instances.append(self)

    def append(self, _frame):
        self.entered.set()
        self.release.wait(timeout=2.0)
        self.appended += 1

    def abort(self, reason):
        self.abort_reason = reason
        return self.incomplete_path

    def finalize(self, _summary, **_kwargs):
        self.incomplete_path.replace(self.final_path)
        return self.final_path


def test_bounded_queue_overflow_is_fatal_visible_and_never_published(tmp_path: Path):
    """Catches silent acquisition drops when a writer cannot keep up."""
    _SlowWriter.instances.clear()
    recorder = RolloutRecorder(
        _config(tmp_path, capacity=1),
        _ready_cache(),
        writer_factory=_SlowWriter,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(_identity(1), max_timesteps=50))
    writer = _SlowWriter.instances[-1]
    assert writer.entered.wait(timeout=1.0)
    status = _wait_for_state(recorder, "error")
    writer.release.set()
    with pytest.raises(RecorderError, match="writer_queue_overflow"):
        recorder.stop()

    assert status["error"] == "writer_queue_overflow"
    assert status["acquisition_active"] is False
    assert writer.abort_reason == "writer_queue_overflow"
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()


class _FailingWriter(_SlowWriter):
    def append(self, _frame):
        raise OSError("injected write failure")


def test_writer_exception_propagates_and_leaves_incomplete(tmp_path: Path):
    """Catches background writer exceptions being swallowed by the recorder."""
    _FailingWriter.instances.clear()
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        writer_factory=_FailingWriter,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    recorder.start(RecorderStartRequest(_identity(2), max_timesteps=5))
    status = _wait_for_state(recorder, "error")
    with pytest.raises(RecorderError, match="injected write failure"):
        recorder.stop()

    assert "writer_error" in status["error"]
    assert Path(status["path"]).suffix == ".incomplete"


def test_runtime_disk_low_finalizes_a_truthful_aborted_episode(tmp_path: Path):
    """Catches acquisition at the 10 GiB stop gate or mislabeling it complete."""
    calls = 0

    def disk_usage(_path):
        nonlocal calls
        calls += 1
        free = 50 * GIB if calls <= 2 else 10 * GIB
        return DiskUsage(100 * GIB, 100 * GIB - free, free)

    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        disk_usage=disk_usage,
    )
    recorder.start(RecorderStartRequest(_identity(3), max_timesteps=20))
    status = _wait_for_state(recorder, "stopped")
    path = recorder.stop()

    assert status["error"] is None
    with h5py.File(path, "r") as episode:
        assert episode.attrs["completion_state"] == "aborted"
        assert episode.attrs["termination_reason"] == "disk_low"
        assert 1 <= episode["action"].shape[0] < 20


def test_start_rejects_invalid_max_timesteps_and_double_start(tmp_path: Path):
    """Catches unbounded allocation and multiple producers sharing one writer."""
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )
    with pytest.raises(ValueError, match="max_timesteps"):
        recorder.start(RecorderStartRequest(_identity(4), max_timesteps=201))
    recorder.start(RecorderStartRequest(_identity(4), max_timesteps=2))
    with pytest.raises(RecorderError, match="already"):
        recorder.start(RecorderStartRequest(_identity(5), max_timesteps=2))
    _wait_for_state(recorder, "stopped")
    recorder.stop()


def test_stop_is_bounded_while_atomic_publication_is_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches stop blocking on the writer's publication lock during replace."""
    entered_replace = Event()
    release_replace = Event()
    stop_returned = Event()
    real_replace = os.replace

    def blocked_replace(source: Path, destination: Path) -> None:
        entered_replace.set()
        assert release_replace.wait(timeout=2.0)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", blocked_replace)
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
        shutdown_timeout_seconds=0.03,
    )
    recorder.start(RecorderStartRequest(_identity(35), max_timesteps=1))
    assert entered_replace.wait(timeout=1.0)
    stop_results: list[Path | None | Exception] = []
    stop_elapsed: list[float] = []

    def stop_recorder() -> None:
        started = time.monotonic()
        try:
            stop_results.append(recorder.stop())
        except Exception as error:  # noqa: BLE001 - capture concurrent result
            stop_results.append(error)
        finally:
            stop_elapsed.append(time.monotonic() - started)
            stop_returned.set()

    stopper = Thread(target=stop_recorder)
    stopper.start()
    try:
        returned_with_replace_blocked = stop_returned.wait(timeout=0.15)
        blocked_status = recorder.status()
    finally:
        release_replace.set()
        stopper.join(timeout=1.0)

    assert returned_with_replace_blocked is True
    assert stop_elapsed[0] < 0.15
    assert stop_results == [None]
    assert blocked_status["state"] == "stopping"
    assert blocked_status["publication_status"] == "commit_in_progress"
    assert blocked_status["error"] is None

    final_status = _wait_for_state(recorder, "stopped")
    final_path = Path(final_status["path"])
    assert final_path.suffix == ".hdf5"
    assert final_path.exists()
    assert final_status["publication_status"] == "committed"
    assert final_status["error"] is None


def test_stop_is_bounded_while_large_hdf5_is_finalizing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A slow resize/fsync is normal saving work and must not be cancelled."""
    entered_flush = Event()
    release_flush = Event()
    real_flush = Hdf5EpisodeWriter._flush_and_fsync

    def blocked_flush(writer: Hdf5EpisodeWriter) -> None:
        if writer._count == 0:
            real_flush(writer)
            return
        entered_flush.set()
        assert release_flush.wait(timeout=2.0)
        real_flush(writer)

    monkeypatch.setattr(Hdf5EpisodeWriter, "_flush_and_fsync", blocked_flush)
    recorder = RolloutRecorder(
        _config(tmp_path),
        _ready_cache(),
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
        shutdown_timeout_seconds=0.03,
    )
    recorder.start(RecorderStartRequest(_identity(37), max_timesteps=1))
    assert entered_flush.wait(timeout=1.0)

    try:
        started = time.monotonic()
        result = recorder.stop()
        elapsed = time.monotonic() - started
        blocked_status = recorder.status()
    finally:
        release_flush.set()

    assert result is None
    assert elapsed < 0.15
    assert blocked_status["state"] == "stopping"
    assert blocked_status["publication_status"] == "finalizing"
    assert blocked_status["error"] is None

    final_status = _wait_for_state(recorder, "stopped")
    assert Path(final_status["path"]).exists()
    assert final_status["publication_status"] == "committed"
    assert final_status["error"] is None


class _CancellableWriter(_LifecycleWriter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = Event()
        self.cancel_reason: str | None = None

    def append(self, _frame):
        self.appended.set()
        self.release.wait()
        self.append_count += 1

    def request_cancel(self, reason):
        self.cancel_reason = reason
        self.release.set()


def test_shutdown_timeout_cooperatively_cancels_writer_and_joins_thread(
    tmp_path: Path,
):
    """Catches stop returning while a cancellable writer thread still owns a file."""
    _CancellableWriter.instances.clear()
    recorder = RolloutRecorder(
        _config(tmp_path, capacity=100),
        _ready_cache(),
        writer_factory=_CancellableWriter,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
        shutdown_timeout_seconds=0.03,
    )
    recorder.start(RecorderStartRequest(_identity(30), max_timesteps=100))
    writer = _CancellableWriter.instances[-1]
    assert writer.appended.wait(timeout=1.0)

    with pytest.raises(RecorderError, match="shutdown_timeout.*writer"):
        recorder.stop()

    status = recorder.status()
    assert writer.cancel_reason is not None
    assert writer.abort_reason is not None
    assert status["writer_thread_alive"] is False
    assert status["publication_allowed"] is False
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()


class _UncancellableWriter(_CancellableWriter):
    def request_cancel(self, reason):
        self.cancel_reason = reason


def test_uncancellable_writer_is_fail_closed_and_can_never_publish_later(
    tmp_path: Path,
):
    """Catches a permanently blocked dependency retaining finalize authority."""
    _UncancellableWriter.instances.clear()
    recorder = RolloutRecorder(
        _config(tmp_path, capacity=100),
        _ready_cache(),
        writer_factory=_UncancellableWriter,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
        shutdown_timeout_seconds=0.02,
    )
    recorder.start(RecorderStartRequest(_identity(31), max_timesteps=100))
    writer = _UncancellableWriter.instances[-1]
    assert writer.appended.wait(timeout=1.0)

    with pytest.raises(RecorderError, match="shutdown_timeout.*writer"):
        recorder.stop()
    status = recorder.status()
    assert status["state"] == "fatal"
    assert status["writer_thread_alive"] is True
    assert status["publication_allowed"] is False
    assert not writer.final_path.exists()

    # Test cleanup also proves a late return follows the revoked gate into abort.
    writer.release.set()
    deadline = time.monotonic() + 1.0
    while recorder.status()["writer_thread_alive"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert recorder.status()["writer_thread_alive"] is False
    assert writer.abort_reason is not None
    assert writer.finalized.is_set() is False
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()


def test_recorder_can_record_multiple_episodes_and_resets_intervention_ids(
    tmp_path: Path,
):
    """Catches a one-shot server or intervention IDs leaking across episodes."""
    cache = _ready_cache()
    now = time.monotonic()
    cache.put("handover_mode", "manual:left", source_stamp=now, arrival_stamp=now)
    recorder = RolloutRecorder(
        _config(tmp_path),
        cache,
        disk_usage=lambda _path: DiskUsage(100 * GIB, 50 * GIB, 50 * GIB),
    )

    recorder.start(RecorderStartRequest(_identity(40), max_timesteps=1))
    first = Path(_wait_for_state(recorder, "stopped")["path"])
    # Model the continuously arriving ROS callbacks between episodes.  The
    # real preflight must reject stale arm state, so this lifecycle test must
    # not depend on whether HDF5 finalization beats the 100 ms freshness TTL.
    now = time.monotonic()
    snapshot = cache.snapshot(now)
    for key, entry in snapshot.entries.items():
        cache.put(key, entry.value, source_stamp=now, arrival_stamp=now)
    recorder.start(RecorderStartRequest(_identity(41), max_timesteps=1))
    second = Path(_wait_for_state(recorder, "stopped")["path"])

    assert first != second
    for path in (first, second):
        with h5py.File(path, "r") as episode:
            assert episode["rollout/intervention_id_left"][...].tolist() == [1]
            assert episode["rollout/intervention_id_right"][...].tolist() == [0]

"""Behavioral tests for the streaming and immutable HDF5 boundary."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event, Thread

import h5py
import numpy as np
import pytest

from capture_core.hdf5_writer import Hdf5EpisodeWriter
from capture_core.labels import LabelStore
from capture_core.schema import ControlSource, EpisodeIdentity, FrameSample
from capture_core.topics import REQUIRED_TOPICS


def _identity(index: int = 3) -> EpisodeIdentity:
    return EpisodeIdentity(
        task_id="in_the_pot",
        model_id="pi05",
        checkpoint_id="step_2000",
        dataset_round="round_001",
        episode_index=index,
    )


def _frame(
    identity: EpisodeIdentity, index: int, *, coordinator_valid: bool = True
) -> FrameSample:
    coordinator = np.arange(14, dtype=np.float32) + index
    timestamps = {key: 100.0 + index for key in REQUIRED_TOPICS}
    validity = {key: True for key in REQUIRED_TOPICS}
    validity.update(
        {
            "qpos": True,
            "qvel": True,
            "effort": True,
            "front_observation": True,
            "rear_observation": True,
            "policy_command_submitted": True,
            "coordinator_command": True,
            "action": True,
            "teach_active_left": True,
            "teach_active_right": True,
        }
    )
    validity["coordinator_command"] = coordinator_valid
    validity["action"] = coordinator_valid
    image = np.full((3, 4, 3), index, dtype=np.uint8)
    return FrameSample(
        identity=identity,
        frame_index=index,
        sample_timestamp=100.0 + index,
        camera_high_rgb=image,
        camera_left_rgb=image + 1,
        camera_right_rgb=image + 2,
        qpos=np.arange(14, dtype=np.float32),
        qvel=np.arange(14, dtype=np.float32) + 10,
        effort=np.arange(14, dtype=np.float32) + 20,
        action=coordinator.copy(),
        base_action=np.array([0.25, -0.25], dtype=np.float32),
        policy_command_submitted=np.arange(14, dtype=np.float32) + 30,
        coordinator_command=coordinator,
        front_observation=np.arange(14, dtype=np.float32) + 40,
        rear_observation=np.arange(14, dtype=np.float32) + 50,
        teach_active_left=True,
        teach_active_right=False,
        handover_mode="manual:left",
        handover_fault="left servo overcurrent",
        control_source_left=ControlSource.HUMAN,
        control_source_right=ControlSource.HOLD,
        is_intervention_left=True,
        is_intervention_right=False,
        intervention_id_left=1,
        intervention_id_right=0,
        source_timestamps=timestamps,
        arrival_timestamps={key: value + 0.01 for key, value in timestamps.items()},
        valid_mask=validity,
    )


def test_active_file_is_incomplete_then_atomically_published_after_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches publishing a file before h5py has closed and persisted it."""
    identity = _identity()
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=4, fps=30.0)
    assert writer.incomplete_path.name == "episode_000003.hdf5.incomplete"
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    writer.append(_frame(identity, 0))

    real_replace = os.replace
    replace_observations: list[tuple[bool, bool]] = []

    def inspect_then_replace(source: Path, destination: Path) -> None:
        # Reopening with write access proves the original h5py handle is closed.
        with h5py.File(source, "r+") as episode:
            replace_observations.append((bool(episode.id.valid), destination.exists()))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", inspect_then_replace)
    result = writer.finalize(end_timestamp=101.0)

    assert result == writer.final_path
    assert replace_observations == [(True, False)]
    assert result.exists()
    assert not writer.incomplete_path.exists()
    assert writer.finalize() == result


def test_finalize_resizes_every_temporal_dataset_and_preserves_full_schema(
    tmp_path: Path,
):
    """Catches padded tails, omitted telemetry, or incompatible standard shapes."""
    identity = _identity()
    writer = Hdf5EpisodeWriter(
        tmp_path,
        identity,
        max_timesteps=5,
        fps=30.0,
        wall_clock=lambda: 100.0,
    )
    writer.append(_frame(identity, 0))
    writer.append(_frame(identity, 1))
    path = writer.finalize(end_timestamp=102.0)

    with h5py.File(path, "r") as episode:
        temporal_shapes: dict[str, tuple[int, ...]] = {}

        def capture(name: str, value: h5py.Dataset | h5py.Group) -> None:
            if isinstance(value, h5py.Dataset):
                temporal_shapes[name] = value.shape

        episode.visititems(capture)
        assert all(shape[0] == 2 for shape in temporal_shapes.values())
        assert episode["observations/qpos"].shape == (2, 14)
        assert episode["observations/qvel"].shape == (2, 14)
        assert episode["observations/effort"].shape == (2, 14)
        assert episode["action"].shape == (2, 14)
        assert episode["base_action"].shape == (2, 2)
        assert episode["observations/images/cam_high"].shape == (2, 3, 4, 3)
        assert episode["observations/images/cam_left_wrist"].dtype == np.uint8
        assert episode["rollout/front_observation"].shape == (2, 14)
        assert episode["rollout/rear_observation"].shape == (2, 14)
        assert episode["rollout/policy_command_submitted"].shape == (2, 14)
        assert episode["rollout/coordinator_command"].shape == (2, 14)
        assert episode["rollout/control_source_left"][:].tolist() == [2, 2]
        assert episode["rollout/intervention_id_left"][:].tolist() == [1, 1]
        assert episode["rollout/handover_mode"].asstr()[:].tolist() == [
            "manual:left",
            "manual:left",
        ]
        assert episode["rollout/handover_fault"].asstr()[:].tolist() == [
            "left servo overcurrent",
            "left servo overcurrent",
        ]
        assert episode["rollout/topic_timestamp/camera_high"][:].tolist() == [
            100.0,
            101.0,
        ]
        assert episode["rollout/arrival_timestamp/camera_high"][:].tolist() == [
            100.01,
            101.01,
        ]
        assert episode["rollout/valid_mask/coordinator_command"][:].tolist() == [
            True,
            True,
        ]
        valid = episode["rollout/valid_mask/coordinator_command"][:]
        np.testing.assert_array_equal(
            episode["action"][:][valid],
            episode["rollout/coordinator_command"][:][valid],
        )
        assert episode.attrs["rollout_schema_version"] == 1
        assert episode.attrs["fps"] == 30.0
        assert episode.attrs["DT"] == pytest.approx(1 / 30)
        assert episode.attrs["task_id"] == "in_the_pot"
        assert episode.attrs["collector_version"] == "v1"
        assert episode.attrs["completion_state"] == "complete"
        assert episode.attrs["start_timestamp"] == 100.0
        assert episode.attrs["end_timestamp"] == 102.0

    label_defaults = LabelStore(tmp_path).get_labels(identity.episode_uuid)
    assert label_defaults["interventions"][0]["handover_mode"] == "manual:left"
    assert label_defaults["interventions"][0]["start_frame"] == 0
    assert label_defaults["interventions"][0]["end_frame"] == 1


def test_injected_write_failure_can_never_create_finalized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches a partially written episode being renamed to immutable truth."""
    identity = _identity()
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=2)

    def fail_write(frame: FrameSample) -> None:
        raise OSError("injected disk write failure")

    monkeypatch.setattr(writer, "_write_frame", fail_write)
    with pytest.raises(OSError, match="injected disk write failure"):
        writer.append(_frame(identity, 0))
    with pytest.raises(RuntimeError, match="failed"):
        writer.finalize()

    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()


def test_atomic_replace_failure_retains_only_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches publish failures losing the recoverable staging episode."""
    identity = _identity()
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=2)
    writer.append(_frame(identity, 0))
    precommit_syncs = 0
    real_directory_fsync = writer._fsync_parent_directory

    def record_precommit_sync() -> None:
        nonlocal precommit_syncs
        precommit_syncs += 1
        real_directory_fsync()

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(writer, "_fsync_parent_directory", record_precommit_sync)
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        writer.finalize()

    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    assert precommit_syncs == 1
    with h5py.File(writer.incomplete_path, "r") as episode:
        assert episode.attrs["completion_state"] == "error"
        assert "injected replace failure" in episode.attrs["failure_reason"]


def test_precommit_directory_fsync_failure_never_attempts_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches a precommit durability failure crossing the visibility point."""
    identity = _identity(8)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)
    writer.append(_frame(identity, 0))
    replace_calls = 0

    def fail_precommit_fsync() -> None:
        raise OSError("injected precommit directory fsync failure")

    def record_replace(_source: Path, _destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1

    monkeypatch.setattr(writer, "_fsync_parent_directory", fail_precommit_fsync)
    monkeypatch.setattr(os, "replace", record_replace)
    with pytest.raises(OSError, match="injected precommit directory fsync failure"):
        writer.finalize()

    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    assert replace_calls == 0
    with h5py.File(writer.incomplete_path, "r") as episode:
        assert episode.attrs["completion_state"] == "error"
        assert "precommit directory fsync failure" in episode.attrs["failure_reason"]


def test_root_start_and_end_timestamps_use_the_same_wall_clock(tmp_path: Path):
    """Catches mixing monotonic sampler seconds with Unix episode timestamps."""
    identity = _identity(9)
    wall_times = iter((1_700_000_000.0, 1_700_000_002.0))
    writer = Hdf5EpisodeWriter(
        tmp_path, identity, max_timesteps=1, wall_clock=lambda: next(wall_times)
    )
    writer.append(_frame(identity, 0))
    path = writer.finalize()

    with h5py.File(path, "r") as episode:
        assert episode.attrs["start_timestamp"] == 1_700_000_000.0
        assert episode.attrs["end_timestamp"] == 1_700_000_002.0


@pytest.mark.parametrize(
    "reserved_key",
    [
        "project_id",
        "task_id",
        "model_id",
        "checkpoint_id",
        "dataset_round",
        "episode_index",
        "episode_uuid",
        "rollout_schema_version",
        "fps",
        "DT",
        "start_timestamp",
        "end_timestamp",
        "collector_version",
        "completion_state",
        "failure_reason",
        "path",
    ],
)
def test_finalize_summary_rejects_reserved_or_unknown_attributes(
    tmp_path: Path, reserved_key: str
):
    """Catches caller-controlled metadata overwriting immutable episode identity."""
    identity = _identity(20)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)
    writer.append(_frame(identity, 0))

    with pytest.raises(ValueError, match="summary attribute"):
        writer.finalize({reserved_key: "attacker-controlled"})

    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    writer.abort("test_cleanup")


def test_finalize_accepts_only_allowlisted_summary_and_explicit_lifecycle_fields(
    tmp_path: Path,
):
    """Catches allowlist validation blocking the intended termination summary."""
    identity = _identity(21)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)
    writer.append(_frame(identity, 0))

    path = writer.finalize(
        {"termination_reason": "disk_low"},
        completion_state="aborted",
        end_timestamp=1_700_000_010.0,
    )

    with h5py.File(path, "r") as episode:
        assert episode.attrs["completion_state"] == "aborted"
        assert episode.attrs["end_timestamp"] == 1_700_000_010.0
        assert episode.attrs["termination_reason"] == "disk_low"


def test_finalize_rejects_invalid_allowlisted_summary_value(tmp_path: Path):
    """Catches h5py coercing an allowlisted attribute with an unsafe value type."""
    identity = _identity(28)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)
    writer.append(_frame(identity, 0))

    with pytest.raises(TypeError, match="termination_reason"):
        writer.finalize({"termination_reason": ["disk_low"]})

    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    writer.abort("test_cleanup")


def test_writer_rejects_frame_identity_shape_and_maximum_length(tmp_path: Path):
    """Catches cross-episode frames, changing cameras, and preallocation overrun."""
    identity = _identity()
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)
    writer.append(_frame(identity, 0))
    with pytest.raises(ValueError, match="max_timesteps"):
        writer.append(_frame(identity, 1))
    writer.abort("test_cleanup")

    other_writer = Hdf5EpisodeWriter(tmp_path, _identity(4), max_timesteps=2)
    with pytest.raises(ValueError, match="identity"):
        other_writer.append(_frame(identity, 0))
    assert other_writer.abort("test_cleanup").suffix == ".incomplete"


def test_writer_rejects_changed_camera_shape_and_action_mismatch(tmp_path: Path):
    """Catches corrupt fixed-shape images and commands mislabeled as action."""
    identity = _identity(5)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=2)
    writer.append(_frame(identity, 0))
    changed = _frame(identity, 1)
    object.__setattr__(
        changed,
        "camera_high_rgb",
        np.zeros((4, 4, 3), dtype=np.uint8),
    )
    with pytest.raises(ValueError, match="camera shapes changed"):
        writer.append(changed)
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()

    identity = _identity(6)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)
    mismatched = _frame(identity, 0)
    object.__setattr__(mismatched, "action", np.ones(14, dtype=np.float32))
    with pytest.raises(ValueError, match="action must equal"):
        writer.append(mismatched)
    assert writer.abort("already_failed") == writer.incomplete_path


def test_writer_replaces_stale_camera_placeholder_with_fixed_shape_black_frame(
    tmp_path: Path,
):
    """A transient invalid camera sample must not poison the whole rollout."""
    identity = _identity(32)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=2)
    writer.append(_frame(identity, 0))

    stale = _frame(identity, 1)
    object.__setattr__(
        stale,
        "camera_high_rgb",
        np.zeros((1, 1, 3), dtype=np.uint8),
    )
    object.__setattr__(
        stale,
        "valid_mask",
        {**stale.valid_mask, "camera_high": False},
    )
    writer.append(stale)
    path = writer.finalize()

    with h5py.File(path, "r") as episode:
        assert episode["observations/images/cam_high"].shape == (2, 3, 4, 3)
        assert np.count_nonzero(episode["observations/images/cam_high"][1]) == 0
        assert not bool(episode["rollout/valid_mask/camera_high"][1])


def test_abort_after_writer_failure_revokes_publication(tmp_path: Path):
    """A sealed failure must not remain reported as an open publication."""
    identity = _identity(33)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=2)
    writer.append(_frame(identity, 0))
    changed = _frame(identity, 1)
    object.__setattr__(
        changed,
        "camera_high_rgb",
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    with pytest.raises(ValueError, match="camera shapes changed"):
        writer.append(changed)
    writer.abort("camera shape failure")

    assert writer.publication_status == "revoked"


def test_writer_cooperative_cancel_revokes_finalize_without_cross_thread_close(
    tmp_path: Path,
):
    """Catches shutdown cancellation leaving a real HDF5 writer publishable."""
    identity = _identity(22)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)

    writer.request_cancel("shutdown_timeout: writer")
    with pytest.raises(RuntimeError, match="writer_cancelled"):
        writer.append(_frame(identity, 0))
    with pytest.raises(RuntimeError, match="failed"):
        writer.finalize()

    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    with h5py.File(writer.incomplete_path, "r") as episode:
        assert episode.attrs["completion_state"] == "error"
        assert "shutdown_timeout: writer" in episode.attrs["failure_reason"]


def test_cancel_linearizes_before_commit_and_prevents_final_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches commit ignoring a cancellation that won before publication."""
    identity = _identity(29)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)
    writer.append(_frame(identity, 0))
    entered_precommit = Event()
    release_precommit = Event()
    real_directory_fsync = writer._fsync_parent_directory

    def block_before_commit() -> None:
        entered_precommit.set()
        release_precommit.wait(timeout=2.0)
        real_directory_fsync()

    monkeypatch.setattr(writer, "_fsync_parent_directory", block_before_commit)
    finalize_results: list[Path | Exception] = []

    def finalize_writer() -> None:
        try:
            finalize_results.append(writer.finalize())
        except Exception as error:  # noqa: BLE001 - capture thread result
            finalize_results.append(error)

    finalizer = Thread(target=finalize_writer)
    finalizer.start()
    assert entered_precommit.wait(timeout=1.0)
    cancel_won = writer.request_cancel("cancel won")
    release_precommit.set()
    finalizer.join(timeout=1.0)

    assert cancel_won is True
    assert not finalizer.is_alive()
    assert len(finalize_results) == 1
    assert isinstance(finalize_results[0], RuntimeError)
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()


def test_cancel_between_lock_entry_and_final_check_revokes_publication(tmp_path: Path):
    """Catches commit checking cancellation before publishing its in-progress state."""

    class StatusAssignmentBarrierWriter(Hdf5EpisodeWriter):
        def __init__(self, *args, **kwargs):
            self.status_assignment_entered = Event()
            self.release_status_assignment = Event()
            self._barrier_armed = False
            super().__init__(*args, **kwargs)
            self._barrier_armed = True

        def __setattr__(self, name, value):
            if (
                name == "_publication_status"
                and value == "commit_in_progress"
                and getattr(self, "_barrier_armed", False)
            ):
                self.status_assignment_entered.set()
                assert self.release_status_assignment.wait(timeout=2.0)
                self._barrier_armed = False
            super().__setattr__(name, value)

    identity = _identity(31)
    writer = StatusAssignmentBarrierWriter(tmp_path, identity, max_timesteps=1)
    writer.append(_frame(identity, 0))
    finalize_results: list[Path | Exception] = []

    def finalize_writer() -> None:
        try:
            finalize_results.append(writer.finalize())
        except Exception as error:  # noqa: BLE001 - capture thread result
            finalize_results.append(error)

    finalizer = Thread(target=finalize_writer)
    finalizer.start()
    assert writer.status_assignment_entered.wait(timeout=1.0)
    cancel_confirmed = writer.request_cancel("cancelled at final check")
    writer.release_status_assignment.set()
    finalizer.join(timeout=1.0)

    assert cancel_confirmed is False
    assert not finalizer.is_alive()
    assert len(finalize_results) == 1
    assert isinstance(finalize_results[0], RuntimeError)
    assert writer.publication_status == "revoked"
    assert writer.incomplete_path.exists()
    assert not writer.final_path.exists()
    with h5py.File(writer.incomplete_path, "r") as episode:
        assert episode.attrs["completion_state"] == "error"
        assert "cancelled at final check" in episode.attrs["failure_reason"]


def test_commit_linearizes_before_cancel_and_cancel_reports_already_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches cancellation falsely claiming it revoked an atomic commit."""
    identity = _identity(30)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)
    writer.append(_frame(identity, 0))
    entered_commit = Event()
    release_commit = Event()
    real_replace = os.replace

    def block_inside_replace(source: Path, destination: Path) -> None:
        entered_commit.set()
        release_commit.wait(timeout=2.0)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", block_inside_replace)
    finalize_results: list[Path | Exception] = []
    cancel_results: list[bool] = []
    cancel_returned = Event()

    def finalize_writer() -> None:
        try:
            finalize_results.append(writer.finalize())
        except Exception as error:  # noqa: BLE001 - capture thread result
            finalize_results.append(error)

    finalizer = Thread(target=finalize_writer)
    finalizer.start()
    assert entered_commit.wait(timeout=1.0)

    def cancel_writer() -> None:
        cancel_results.append(writer.request_cancel("too late"))
        cancel_returned.set()

    canceller = Thread(target=cancel_writer)
    canceller.start()
    try:
        returned_while_replace_blocked = cancel_returned.wait(timeout=0.1)
    finally:
        release_commit.set()
    finalizer.join(timeout=1.0)
    canceller.join(timeout=1.0)

    assert returned_while_replace_blocked is True
    assert finalize_results == [writer.final_path]
    assert cancel_results == [False]
    assert writer.final_path.exists()
    assert not writer.incomplete_path.exists()


def test_action_matches_coordinator_exactly_on_every_valid_frame(tmp_path: Path):
    """Catches validating invalid gaps or failing to enforce valid action labels."""
    identity = _identity(24)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=2)
    writer.append(_frame(identity, 0))
    invalid_gap = _frame(identity, 1, coordinator_valid=False)
    object.__setattr__(invalid_gap, "action", np.full(14, np.nan, dtype=np.float32))
    writer.append(invalid_gap)
    path = writer.finalize()

    with h5py.File(path, "r") as episode:
        valid = episode["rollout/valid_mask/coordinator_command"][:]
        assert valid.tolist() == [True, False]
        np.testing.assert_array_equal(
            episode["action"][:][valid],
            episode["rollout/coordinator_command"][:][valid],
        )


def test_empty_finalize_and_abort_are_fail_closed_and_idempotent(tmp_path: Path):
    """Catches empty publication or repeated cleanup changing forensic state."""
    identity = _identity(25)
    writer = Hdf5EpisodeWriter(tmp_path, identity, max_timesteps=1)

    with pytest.raises(ValueError, match="zero frames"):
        writer.finalize()
    with pytest.raises(RuntimeError, match="failed"):
        writer.finalize()
    first = writer.abort("empty_episode")
    second = writer.abort("ignored_second_reason")

    assert first == second == writer.incomplete_path
    assert not writer.final_path.exists()
    with h5py.File(first, "r") as episode:
        assert episode.attrs["completion_state"] == "error"
        assert "zero frames" in episode.attrs["failure_reason"]

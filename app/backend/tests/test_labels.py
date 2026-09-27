"""Behavioral tests for immutable rollout labels and episode discovery."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import h5py
import numpy as np
import pytest

import capture_core.labels as labels_module
from capture_core.labels import (
    LabelConflictError,
    LabelStore,
    LabelValidationError,
    labels_are_complete,
)
from capture_core.topics import REQUIRED_TOPICS


def test_unlabelled_save_is_final_but_excluded_and_keeps_markers(tmp_path):
    _, uuid = _write_episode(tmp_path)
    store = LabelStore(tmp_path)
    result = store.update_labels(uuid, {"episode_uuid": str(uuid), "episode_outcome": "unknown",
        "episode_quality": "uncertain", "termination_reason": "operator_save", "keep_for_training": "false",
        "operator_nodes": [{"frame_index": 2, "node_kind": "pause"}]})
    assert labels_are_complete(result)
    assert result["keep_for_training"] == "false" and result["episode_outcome"] == "unknown"
    assert store.get_labels(uuid)["operator_nodes"] == [{"frame_index": 2, "node_kind": "pause"}]
    with pytest.raises(LabelValidationError):
        store.update_labels(uuid, {"episode_uuid": str(uuid), "operator_nodes": [{"frame_index": 5, "node_kind": "marker"}]})


def _write_episode(
    root: Path,
    *,
    episode_uuid: UUID | None = None,
    index: int = 0,
    completion_state: str = "complete",
) -> tuple[Path, UUID]:
    episode_uuid = episode_uuid or uuid4()
    parent = root / "in_the_pot" / "pi05" / "round_001"
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f"episode_{index:06d}.hdf5"
    with h5py.File(path, "w") as episode:
        episode.attrs.update(
            project_id="task5_jiaan_hil_realworld_rl",
            collector_version="v1",
            rollout_schema_version=1,
            fps=30.0,
            DT=1.0 / 30.0,
            completion_state=completion_state,
            task_id="in_the_pot",
            model_id="pi05",
            checkpoint_id="step_2000",
            dataset_round="round_001",
            episode_index=index,
            episode_uuid=str(episode_uuid),
            start_timestamp=1.0,
            end_timestamp=1.4,
            termination_reason="operator_stop",
        )
        observations = episode.create_group("observations")
        images = observations.create_group("images")
        for camera in ("cam_high", "cam_left_wrist", "cam_right_wrist"):
            images.create_dataset(camera, data=np.zeros((5, 1, 1, 3), dtype=np.uint8))
        for field in ("qpos", "qvel", "effort"):
            observations.create_dataset(field, data=np.zeros((5, 14), dtype=np.float32))
        episode.create_dataset("action", data=np.zeros((5, 14), dtype=np.float32))
        episode.create_dataset("base_action", data=np.zeros((5, 2), dtype=np.float32))
        rollout = episode.create_group("rollout")
        for field in (
            "policy_command_submitted",
            "coordinator_command",
            "front_observation",
            "rear_observation",
        ):
            rollout.create_dataset(field, data=np.zeros((5, 14), dtype=np.float32))
        rollout.create_dataset("teach_active_left", data=np.zeros(5, dtype=np.bool_))
        rollout.create_dataset("teach_active_right", data=np.zeros(5, dtype=np.bool_))
        rollout.create_dataset(
            "control_source_left", data=np.array([1, 2, 2, 3, 2], dtype=np.uint8)
        )
        rollout.create_dataset(
            "control_source_right", data=np.array([1, 3, 3, 2, 2], dtype=np.uint8)
        )
        rollout.create_dataset(
            "intervention_id_left", data=np.array([0, 1, 1, 1, 2], dtype=np.uint64)
        )
        rollout.create_dataset(
            "intervention_id_right", data=np.array([0, 0, 0, 1, 1], dtype=np.uint64)
        )
        rollout.create_dataset(
            "is_intervention_left",
            data=np.array([False, True, True, False, True], dtype=np.bool_),
        )
        rollout.create_dataset(
            "is_intervention_right",
            data=np.array([False, False, False, True, True], dtype=np.bool_),
        )
        rollout.create_dataset(
            "frame_index", data=np.array([0, 1, 2, 3, 4], dtype=np.uint64)
        )
        rollout.create_dataset(
            "sample_timestamp",
            data=np.array([1.0, 1.1, 1.2, 1.3, 1.4], dtype=np.float64),
        )
        rollout.create_dataset(
            "handover_mode",
            data=np.array(
                [
                    "policy",
                    "manual:left",
                    "manual:left",
                    "manual:right",
                    "manual:left+right",
                ],
                dtype=object,
            ),
            dtype=h5py.string_dtype(encoding="utf-8"),
        )
        rollout.create_dataset(
            "handover_fault",
            data=np.array(["", "", "", "", ""], dtype=object),
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
            topic_timestamps.create_dataset(key, data=np.ones(5, dtype=np.float64))
            arrival_timestamps.create_dataset(key, data=np.ones(5, dtype=np.float64))
        for key in validity_keys:
            valid_masks.create_dataset(key, data=np.ones(5, dtype=np.bool_))
    return path, episode_uuid


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_labels_complete_requires_only_final_outcome_quality_and_keep_decision():
    assert labels_are_complete(
        {
            "episode_outcome": "success",
            "episode_quality": "good",
            "keep_for_training": "true",
        }
    )
    assert not labels_are_complete(
        {
            "episode_outcome": "success",
            "episode_quality": "uncertain",
            "keep_for_training": "true",
        }
    )
    assert labels_are_complete(
        {
            "episode_outcome": "failure",
            "episode_quality": "bad",
            "keep_for_training": "false",
        }
    )


def test_set_outcome_maps_compatibility_fields_and_interventions(tmp_path: Path):
    episode_path, episode_uuid = _write_episode(tmp_path)
    before = _sha256(episode_path)
    store = LabelStore(tmp_path)

    success = store.set_outcome(episode_uuid, "success")

    assert success["episode_outcome"] == "success"
    assert success["episode_quality"] == "good"
    assert success["keep_for_training"] == "true"
    assert all(item["quality"] == "good" for item in success["interventions"])
    assert all(item["outcome"] == "recovered" for item in success["interventions"])
    assert _sha256(episode_path) == before

    failure = store.set_outcome(episode_uuid, "failure")

    assert failure["episode_outcome"] == "failure"
    assert failure["episode_quality"] == "bad"
    assert failure["keep_for_training"] == "false"
    assert all(item["quality"] == "uncertain" for item in failure["interventions"])
    assert all(item["outcome"] == "uncertain" for item in failure["interventions"])
    assert labels_are_complete(failure)


def test_set_outcome_rejects_unknown_decision(tmp_path: Path):
    _, episode_uuid = _write_episode(tmp_path)

    with pytest.raises(LabelValidationError, match="outcome"):
        LabelStore(tmp_path).set_outcome(episode_uuid, "aborted")


def test_global_phases_split_unilateral_and_bimanual_handover(tmp_path: Path):
    _, episode_uuid = _write_episode(tmp_path)
    store = LabelStore(tmp_path)

    phases = store.get_intervention_phases(episode_uuid)

    assert phases == [
        {
            "start_frame": 1,
            "end_frame": 2,
            "handover_mode": "manual:left",
            "human_sides": ["left"],
        },
        {
            "start_frame": 3,
            "end_frame": 3,
            "handover_mode": "manual:right",
            "human_sides": ["right"],
        },
        {
            "start_frame": 4,
            "end_frame": 4,
            "handover_mode": "manual:left+right",
            "human_sides": ["left", "right"],
        },
    ]


def test_series_inspection_gates_only_on_latest_episode_labels(tmp_path: Path):
    _, older_uuid = _write_episode(tmp_path, index=0)
    _, latest_uuid = _write_episode(tmp_path, index=1)
    store = LabelStore(tmp_path)
    store.update_labels(
        older_uuid,
        {
            "episode_uuid": str(older_uuid),
            "episode_outcome": "aborted",
            "episode_quality": "bad",
            "keep_for_training": "false",
        },
    )

    pending = store.inspect_series("in_the_pot", "pi05", "round_001")

    assert pending["episode_count"] == 2
    assert pending["latest_episode_uuid"] == str(latest_uuid)
    assert pending["latest_episode_index"] == 1
    assert pending["latest_labels_complete"] is False
    assert pending["label_blocked"] is True

    store.update_labels(
        latest_uuid,
        {
            "episode_uuid": str(latest_uuid),
            "episode_outcome": "success",
            "episode_quality": "good",
            "keep_for_training": "true",
        },
    )
    complete = store.inspect_series("in_the_pot", "pi05", "round_001")
    assert complete["latest_labels_complete"] is True
    assert complete["label_blocked"] is False


def test_label_update_keeps_hdf5_immutable_and_derives_read_only_intervals(
    tmp_path: Path,
):
    """Catches label writes touching HDF5 or trusting client interval boundaries."""
    episode_path, episode_uuid = _write_episode(tmp_path)
    before = _sha256(episode_path)
    store = LabelStore(tmp_path)

    defaults = store.get_labels(episode_uuid)
    assert defaults["interventions"] == [
        {
            "side": "left",
            "intervention_id": 1,
            "start_frame": 1,
            "end_frame": 2,
            "start_timestamp": 1.1,
            "end_timestamp": 1.2,
            "handover_mode": "manual:left",
            "reason": "unknown",
            "outcome": "uncertain",
            "quality": "uncertain",
            "note": None,
        },
        {
            "side": "left",
            "intervention_id": 2,
            "start_frame": 4,
            "end_frame": 4,
            "start_timestamp": 1.4,
            "end_timestamp": 1.4,
            "handover_mode": "manual:left+right",
            "reason": "unknown",
            "outcome": "uncertain",
            "quality": "uncertain",
            "note": None,
        },
        {
            "side": "right",
            "intervention_id": 1,
            "start_frame": 3,
            "end_frame": 4,
            "start_timestamp": 1.3,
            "end_timestamp": 1.4,
            "handover_mode": "manual:right",
            "reason": "unknown",
            "outcome": "uncertain",
            "quality": "uncertain",
            "note": None,
        },
    ]

    saved = store.update_labels(
        episode_uuid,
        {
            "episode_uuid": str(episode_uuid),
            "episode_outcome": "success",
            "episode_quality": "good",
            "keep_for_training": "true",
            "operator_note": "clean recovery",
            "interventions": [
                {
                    "side": "left",
                    "intervention_id": 1,
                    "reason": "corrective",
                    "outcome": "recovered",
                    "quality": "good",
                    "note": "grip corrected",
                }
            ],
        },
    )

    assert _sha256(episode_path) == before
    assert saved["interventions"][0]["start_frame"] == 1
    assert saved["interventions"][0]["reason"] == "corrective"
    sidecar = episode_path.with_suffix(".labels.json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["episode_uuid"] == str(
        episode_uuid
    )
    assert not Path(f"{sidecar}.tmp").exists()


def test_label_validation_rejects_uuid_mismatch_and_interval_changes(
    tmp_path: Path,
):
    """Catches cross-episode writes and forged intervention intervals."""
    _, episode_uuid = _write_episode(tmp_path)
    store = LabelStore(tmp_path)

    with pytest.raises(LabelConflictError, match="episode_uuid_mismatch"):
        store.update_labels(
            episode_uuid,
            {"episode_uuid": str(uuid4()), "episode_outcome": "success"},
        )
    with pytest.raises(LabelValidationError, match="read_only"):
        store.update_labels(
            episode_uuid,
            {
                "episode_uuid": str(episode_uuid),
                "interventions": [
                    {"side": "left", "intervention_id": 1, "start_frame": 999}
                ],
            },
        )
    with pytest.raises(LabelValidationError, match="unknown intervention field"):
        store.update_labels(
            episode_uuid,
            {
                "episode_uuid": str(episode_uuid),
                "interventions": [
                    {"side": "left", "intervention_id": 1, "comment": "unsafe"}
                ],
            },
        )
    with pytest.raises(LabelValidationError, match="operator_note"):
        store.update_labels(
            episode_uuid,
            {"episode_uuid": str(episode_uuid), "operator_note": 42},
        )
    with pytest.raises(LabelValidationError, match="invalid intervention identity"):
        store.update_labels(
            episode_uuid,
            {
                "episode_uuid": str(episode_uuid),
                "interventions": [
                    {"side": "left", "intervention_id": 1.0, "quality": "good"}
                ],
            },
        )


def test_atomic_replace_failure_preserves_previous_sidecar_and_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches failed sidecar publication corrupting prior labels or rollout data."""
    episode_path, episode_uuid = _write_episode(tmp_path)
    store = LabelStore(tmp_path)
    store.update_labels(
        episode_uuid,
        {"episode_uuid": str(episode_uuid), "operator_note": "original"},
    )
    sidecar = episode_path.with_suffix(".labels.json")
    original_sidecar = sidecar.read_bytes()
    original_episode = _sha256(episode_path)

    def fail_replace(_source, _target):
        raise OSError("injected replace failure")

    monkeypatch.setattr(labels_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        store.update_labels(
            episode_uuid,
            {"episode_uuid": str(episode_uuid), "operator_note": "new"},
        )

    assert sidecar.read_bytes() == original_sidecar
    assert _sha256(episode_path) == original_episode
    assert not Path(f"{sidecar}.tmp").exists()


@pytest.mark.parametrize("failure_point", ["file_fsync", "replace", "parent_fsync"])
def test_every_sidecar_durability_failure_restores_previous_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
):
    """Catches durability faults leaving a new or truncated sidecar visible."""
    episode_path, episode_uuid = _write_episode(tmp_path)
    store = LabelStore(tmp_path)
    store.update_labels(
        episode_uuid,
        {"episode_uuid": str(episode_uuid), "operator_note": "durable original"},
    )
    sidecar = episode_path.with_suffix(".labels.json")
    original_sidecar = sidecar.read_bytes()
    original_episode = _sha256(episode_path)

    if failure_point == "file_fsync":
        monkeypatch.setattr(
            labels_module.os,
            "fsync",
            lambda _descriptor: (_ for _ in ()).throw(OSError("file fsync failed")),
        )
    elif failure_point == "replace":
        monkeypatch.setattr(
            labels_module.os,
            "replace",
            lambda _source, _target: (_ for _ in ()).throw(OSError("replace failed")),
        )
    else:
        monkeypatch.setattr(
            store,
            "_fsync_parent_directory",
            lambda _parent: (_ for _ in ()).throw(OSError("parent fsync failed")),
        )

    with pytest.raises(OSError, match="failed"):
        store.update_labels(
            episode_uuid,
            {"episode_uuid": str(episode_uuid), "operator_note": "not committed"},
        )

    assert sidecar.read_bytes() == original_sidecar
    assert _sha256(episode_path) == original_episode
    assert not Path(f"{sidecar}.tmp").exists()


def test_concurrent_partial_updates_are_atomic_without_lost_fields(tmp_path: Path):
    """Catches read-modify-write races that discard a concurrent augmentation."""
    _, episode_uuid = _write_episode(tmp_path)
    first_store = LabelStore(tmp_path)
    second_store = LabelStore(tmp_path)
    first_read = Event()
    second_read = Event()

    # Distinct store instances model independently wired request dependencies.
    # Force both stale reads to complete before either publication.
    for store, own_read, peer_read in (
        (first_store, first_read, second_read),
        (second_store, second_read, first_read),
    ):
        real_read = store._read_labels

        def synchronized_read(
            record,
            *,
            _real_read=real_read,
            _own_read=own_read,
            _peer_read=peer_read,
        ):
            result = _real_read(record)
            _own_read.set()
            _peer_read.wait(timeout=0.2)
            return result

        store._read_labels = synchronized_read  # type: ignore[method-assign]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                first_store.update_labels,
                episode_uuid,
                {"episode_uuid": str(episode_uuid), "operator_note": "reviewed"},
            ),
            pool.submit(
                second_store.update_labels,
                episode_uuid,
                {"episode_uuid": str(episode_uuid), "episode_quality": "good"},
            ),
        ]
        for future in futures:
            future.result()

    saved = LabelStore(tmp_path).get_labels(episode_uuid)
    assert saved["operator_note"] == "reviewed"
    assert saved["episode_quality"] == "good"


def test_hdf5_is_opened_read_only_and_listing_ignores_untrusted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Catches writable HDF5 access and unsafe/incomplete episode discovery."""
    valid_path, valid_uuid = _write_episode(tmp_path, index=0)
    _write_episode(tmp_path, index=1, completion_state="error")
    incomplete_path, _ = _write_episode(tmp_path, index=2)
    incomplete_path.rename(Path(f"{incomplete_path}.incomplete"))
    mismatched_path, _ = _write_episode(tmp_path, index=3)
    with h5py.File(mismatched_path, "r+") as episode:
        episode.attrs["task_id"] = "another_task"
    (tmp_path / "notes.txt").write_text("not an episode", encoding="utf-8")

    outside = tmp_path.parent / f"outside-{uuid4()}.hdf5"
    outside_uuid = uuid4()
    _write_episode(
        tmp_path.parent / f"outside-root-{uuid4()}", episode_uuid=outside_uuid
    )
    outside.write_bytes(valid_path.read_bytes())
    symlink = valid_path.parent / "episode_000004.hdf5"
    symlink.symlink_to(outside)

    real_h5_file = labels_module.h5py.File
    modes: list[str] = []

    def recording_file(path, mode="r", *args, **kwargs):
        modes.append(mode)
        return real_h5_file(path, mode, *args, **kwargs)

    monkeypatch.setattr(labels_module.h5py, "File", recording_file)
    store = LabelStore(tmp_path)
    episodes = store.list_episodes()
    labels = store.get_labels(valid_uuid)

    assert [item["episode_uuid"] for item in episodes] == [str(valid_uuid)]
    assert labels["episode_uuid"] == str(valid_uuid)
    assert modes and set(modes) == {"r"}


def test_episode_listing_exposes_success_and_failure_outcomes(tmp_path: Path):
    """History consumers need the actual outcome, not only a complete flag."""
    _, success_uuid = _write_episode(tmp_path, index=0)
    _, failure_uuid = _write_episode(tmp_path, index=1)
    store = LabelStore(tmp_path)
    store.set_outcome(success_uuid, "success")
    store.set_outcome(failure_uuid, "failure")

    episodes = store.list_episodes()

    assert [item["episode_outcome"] for item in episodes] == [
        "success",
        "failure",
    ]
    assert [item["has_hil"] for item in episodes] == [True, True]


def test_episode_history_survives_dataset_round_directory_rename(tmp_path: Path):
    """The UUID and sidecar remain discoverable when only the round folder moves."""
    episode_path, episode_uuid = _write_episode(tmp_path, index=0)
    original_store = LabelStore(tmp_path)
    original_store.set_outcome(episode_uuid, "success")

    renamed_round = episode_path.parent.with_name("renamed_round")
    episode_path.parent.rename(renamed_round)

    store = LabelStore(tmp_path)
    episodes = store.list_episodes()

    assert [item["episode_uuid"] for item in episodes] == [str(episode_uuid)]
    assert episodes[0]["dataset_round"] == "renamed_round"
    assert episodes[0]["recorded_dataset_round"] == "round_001"
    assert episodes[0]["round_name_mismatch"] is True
    assert store.inspect_series("in_the_pot", "pi05", "renamed_round") == {
        "episode_count": 1,
        "latest_episode_uuid": str(episode_uuid),
        "latest_episode_index": 0,
        "latest_labels_complete": True,
        "label_blocked": False,
    }
    assert store.get_labels(episode_uuid)["episode_outcome"] == "success"


def test_listing_rejects_malformed_finalized_hdf5_metadata_and_arrays(tmp_path: Path):
    """Catches coercive metadata and malformed telemetry being treated as validated."""
    _, valid_uuid = _write_episode(tmp_path, index=0)
    schema_path, _ = _write_episode(tmp_path, index=1)
    uuid_path, _ = _write_episode(tmp_path, index=2)
    source_path, _ = _write_episode(tmp_path, index=3)
    dtype_path, _ = _write_episode(tmp_path, index=4)
    shape_path, _ = _write_episode(tmp_path, index=5)
    reused_id_path, _ = _write_episode(tmp_path, index=6)
    mismatched_mode_path, _ = _write_episode(tmp_path, index=7)

    with h5py.File(schema_path, "r+") as episode:
        del episode.attrs["rollout_schema_version"]
        episode.attrs["rollout_schema_version"] = 1.9
    with h5py.File(uuid_path, "r+") as episode:
        episode.attrs["episode_uuid"] = "not-a-uuid"
    with h5py.File(source_path, "r+") as episode:
        episode["rollout/control_source_left"][0] = 99
    with h5py.File(dtype_path, "r+") as episode:
        values = episode["rollout/intervention_id_left"][:].astype(np.int64)
        del episode["rollout/intervention_id_left"]
        episode["rollout"].create_dataset("intervention_id_left", data=values)
    with h5py.File(shape_path, "r+") as episode:
        del episode["rollout/handover_mode"]
        episode["rollout"].create_dataset(
            "handover_mode",
            data=np.array(["policy"], dtype=object),
            dtype=h5py.string_dtype(encoding="utf-8"),
        )
    with h5py.File(reused_id_path, "r+") as episode:
        episode["rollout/intervention_id_left"][4] = 1
    with h5py.File(mismatched_mode_path, "r+") as episode:
        episode["rollout/handover_mode"][1] = "manual:right"

    episodes = LabelStore(tmp_path).list_episodes()

    assert [item["episode_uuid"] for item in episodes] == [str(valid_uuid)]


def test_listing_requires_the_complete_writer_schema_and_consistent_length(
    tmp_path: Path,
):
    """Catches plausible source-only or truncated files being exposed as finalized."""
    _, valid_uuid = _write_episode(tmp_path, index=0)
    missing_path, _ = _write_episode(tmp_path, index=1)
    truncated_path, _ = _write_episode(tmp_path, index=2)
    with h5py.File(missing_path, "r+") as episode:
        del episode["action"]
    with h5py.File(truncated_path, "r+") as episode:
        del episode["observations/qpos"]
        episode["observations"].create_dataset(
            "qpos", data=np.zeros((4, 14), dtype=np.float32)
        )

    episodes = LabelStore(tmp_path).list_episodes()

    assert [item["episode_uuid"] for item in episodes] == [str(valid_uuid)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unknown_field", "ignored-before"),
        ("label_schema_version", True),
        ("label_schema_version", 2),
        ("label_updated_at", "not-a-timestamp"),
        ("label_updated_at", "2026-08-11T00:00:00"),
    ],
)
def test_persisted_sidecar_rejects_unknown_or_non_strict_metadata(
    tmp_path: Path, field: str, value: object
):
    """Catches malformed server state being silently normalized on read."""
    episode_path, episode_uuid = _write_episode(tmp_path, index=0)
    store = LabelStore(tmp_path)
    store.update_labels(
        episode_uuid,
        {"episode_uuid": str(episode_uuid), "operator_note": "valid"},
    )
    sidecar = episode_path.with_suffix(".labels.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload[field] = value
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LabelValidationError):
        store.get_labels(episode_uuid)

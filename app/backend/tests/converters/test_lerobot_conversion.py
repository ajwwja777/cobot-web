from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from converters.lerobot_writer import convert_sources, task5_lerobot_features
from converters.validate_source import open_source
from tests.fixtures.build_hdf5_fixtures import (
    build_legacy_fixture,
    build_rollout_fixture,
)


class _FakeLeRobotDataset:
    def __init__(self, *, root: Path, features: dict[str, dict], **kwargs: object):
        self.root = root
        self.features = features
        self.kwargs = kwargs
        self.frames: list[dict[str, object]] = []
        self.episodes: list[list[dict[str, object]]] = []
        self.submitted_keys: list[set[str]] = []
        self.finalized = False
        root.mkdir(parents=True)

    def add_frame(self, frame: dict[str, object]) -> None:
        self.submitted_keys.append(set(frame))
        stored = dict(frame)
        stored["frame_index"] = np.int64(len(self.frames))
        stored["timestamp"] = np.float32(frame.get("timestamp", len(self.frames) / 30))
        stored["index"] = np.int64(len(self.frames))
        stored["episode_index"] = np.int64(len(self.episodes))
        self.frames.append(stored)

    def save_episode(self) -> None:
        self.episodes.append(self.frames.copy())
        self.frames.clear()

    def finalize(self) -> None:
        self.finalized = True


class _Factory:
    def __init__(self) -> None:
        self.dataset: _FakeLeRobotDataset | None = None

    def __call__(self, **kwargs: object) -> _FakeLeRobotDataset:
        self.dataset = _FakeLeRobotDataset(**kwargs)
        return self.dataset


def test_rollout_conversion_emits_standard_and_complementary_fields(tmp_path: Path):
    source_path = build_rollout_fixture(tmp_path / "rollout.hdf5")
    output = tmp_path / "lerobot"
    factory = _Factory()

    manifest = convert_sources(
        [open_source(source_path)],
        output=output,
        repo_id="jiaan/in_the_pot_task5",
        task="open the pot, put the object inside, and close the lid",
        backend_factory=factory,
    )

    assert factory.dataset is not None and factory.dataset.finalized
    assert len(factory.dataset.episodes) == 1
    assert len(factory.dataset.episodes[0]) == 12
    frame = factory.dataset.episodes[0][3]
    assert frame["observation.images.cam_high"].shape == (8, 8, 3)
    assert frame["observation.images.cam_left_wrist"].shape == (8, 8, 3)
    assert frame["observation.images.cam_right_wrist"].shape == (8, 8, 3)
    assert frame["observation.state"].shape == (14,)
    assert frame["action"].shape == (14,)
    assert frame["task"] == "open the pot, put the object inside, and close the lid"
    assert frame["frame_index"] == 3
    assert frame["episode_index"] == 0
    assert frame["timestamp"] == pytest.approx(3 / 30)
    assert frame["task5.sample_timestamp"].tolist() == [pytest.approx(1000.1)]
    assert frame["task5.control_source_left"].tolist() == [2]
    assert frame["task5.control_source_right"].tolist() == [3]
    assert frame["task5.intervention_id_left"].tolist() == [1]
    assert frame["task5.valid.action"].tolist() == [True]
    assert frame["task5.policy_command"].shape == (14,)
    assert frame["task5.coordinator_command"].shape == (14,)
    assert manifest["episode_count"] == 1
    assert manifest["frame_count"] == 12
    assert manifest["video_codec"] == "h264"


def test_official_backend_receives_tuple_shapes_and_no_automatic_timestamp(
    tmp_path: Path,
):
    source_path = build_rollout_fixture(tmp_path / "rollout.hdf5")
    factory = _Factory()

    convert_sources(
        [open_source(source_path)],
        output=tmp_path / "lerobot",
        repo_id="jiaan/rollout",
        task="in the pot",
        backend_factory=factory,
    )

    features = task5_lerobot_features((8, 8, 3))
    assert features["action"]["shape"] == (14,)
    assert features["observation.images.cam_high"]["shape"] == (8, 8, 3)
    assert features["task5.handover_mode"]["shape"] == (1,)
    assert factory.dataset is not None
    assert "timestamp" not in factory.dataset.submitted_keys[0]
    assert "task5.sample_timestamp" in factory.dataset.submitted_keys[0]
    assert factory.dataset.episodes[0][3]["task5.sample_timestamp"].tolist() == [
        pytest.approx(1000.1)
    ]


def test_legacy_conversion_emits_explicit_neutral_complementary_defaults(
    tmp_path: Path,
):
    source_path = build_legacy_fixture(tmp_path / "legacy.hdf5")
    factory = _Factory()

    convert_sources(
        [open_source(source_path)],
        output=tmp_path / "lerobot",
        repo_id="jiaan/legacy",
        task="put two fruits in the basket",
        backend_factory=factory,
    )

    assert factory.dataset is not None
    frame = factory.dataset.episodes[0][0]
    assert frame["task5.control_source_left"].tolist() == [0]
    assert frame["task5.control_source_right"].tolist() == [0]
    assert frame["task5.intervention_id_left"].tolist() == [0]
    assert frame["task5.valid.action"].tolist() == [True]
    np.testing.assert_array_equal(frame["task5.policy_command"], np.zeros(14))


def test_manifest_records_source_hash_schema_and_converter_identity(tmp_path: Path):
    source_path = build_legacy_fixture(tmp_path / "legacy.hdf5")
    factory = _Factory()

    returned = convert_sources(
        [open_source(source_path)],
        output=tmp_path / "lerobot",
        repo_id="jiaan/legacy",
        task="put two fruits in the basket",
        backend_factory=factory,
    )
    stored = json.loads((tmp_path / "lerobot/task5_manifest.json").read_text())

    assert stored == returned
    assert stored["lerobot_version"] == "0.4.2"
    assert stored["manifest_schema_version"] == 1
    assert stored["sources"] == [
        {
            "path": str(source_path.resolve()),
            "schema": "legacy_cobot_hdf5",
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "frame_count": 6,
            "facts_overlay": "task5_facts/episode_000000.npz",
            "episode_uuid": None,
        }
    ]
    assert stored["converter_git_commit"]
    assert stored["features"] == json.loads(
        json.dumps(task5_lerobot_features((8, 8, 3)))
    )


def test_conversion_exports_small_portable_facts_without_hdf5_images(tmp_path: Path):
    source_path = build_rollout_fixture(tmp_path / "rollout.hdf5")
    output = tmp_path / "lerobot"

    manifest = convert_sources(
        [open_source(source_path)],
        output=output,
        repo_id="jiaan/rollout",
        task="in the pot",
        backend_factory=_Factory(),
    )

    facts_path = output / manifest["sources"][0]["facts_overlay"]
    assert facts_path.stat().st_size < 20_000
    with np.load(facts_path) as facts:
        assert set(facts.files) == {
            "action_valid",
            "control_source_left",
            "control_source_right",
            "intervention_id_left",
            "intervention_id_right",
            "left_valid",
            "right_valid",
            "state_valid",
        }
        assert facts["control_source_left"].tolist()[2:5] == [2, 2, 2]
        assert facts["left_valid"].dtype == np.bool_


def test_required_rollout_labels_are_archived_with_identity_and_hash(tmp_path: Path):
    source_path = build_rollout_fixture(tmp_path / "rollout.hdf5")
    source = open_source(source_path)
    labels = {
        "label_schema_version": 1,
        "episode_uuid": source.metadata.episode_uuid,
        "episode_outcome": "success",
        "episode_quality": "good",
        "keep_for_training": "true",
        "last_completed_stage": None,
        "failure_stage": None,
        "failure_type": None,
        "termination_reason": None,
        "operator_note": None,
        "interventions": [],
        "label_updated_at": "2026-08-12T00:00:00Z",
    }
    sidecar = source_path.with_suffix(".labels.json")
    sidecar.write_text(json.dumps(labels, sort_keys=True) + "\n", encoding="utf-8")
    output = tmp_path / "lerobot"

    manifest = convert_sources(
        [source],
        output=output,
        repo_id="jiaan/rollout",
        task="in the pot",
        backend_factory=_Factory(),
        require_labels=True,
    )

    record = manifest["sources"][0]
    archived = output / record["labels_path"]
    assert archived.read_bytes() == sidecar.read_bytes()
    assert record["labels_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
    assert record["source_metadata"] == {
        "task_id": source.metadata.task_id,
        "model_id": source.metadata.model_id,
        "checkpoint_id": source.metadata.checkpoint_id,
        "dataset_round": source.metadata.dataset_round,
        "episode_index": source.metadata.episode_index,
        "episode_uuid": source.metadata.episode_uuid,
        "termination_reason": source.metadata.termination_reason,
    }


def test_conversion_rejects_mixed_fps_and_existing_output(tmp_path: Path):
    first = build_legacy_fixture(tmp_path / "first.hdf5")
    second = build_legacy_fixture(tmp_path / "second.hdf5")
    import h5py

    with h5py.File(second, "r+") as handle:
        handle.attrs["fps"] = 20
        handle.attrs["DT"] = 0.05
    sources = [open_source(first), open_source(second)]

    with pytest.raises(ValueError, match="same fps"):
        convert_sources(
            sources,
            output=tmp_path / "out",
            repo_id="jiaan/mixed",
            task="task",
            backend_factory=_Factory(),
        )

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        convert_sources(
            [open_source(first)],
            output=existing,
            repo_id="jiaan/existing",
            task="task",
            backend_factory=_Factory(),
        )


def test_conversion_rejects_unsupported_video_codec(tmp_path: Path):
    source_path = build_legacy_fixture(tmp_path / "legacy.hdf5")

    with pytest.raises(ValueError, match="video codec"):
        convert_sources(
            [open_source(source_path)],
            output=tmp_path / "out",
            repo_id="jiaan/legacy",
            task="in the pot",
            backend_factory=_Factory(),
            video_codec="mpeg2video",
        )

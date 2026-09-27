from __future__ import annotations

import hashlib
from pathlib import Path

import h5py
import numpy as np
import pytest

from converters.validate_source import SourceValidationError, detect_source, open_source
from capture_core.schema import ControlSource
from tests.fixtures.build_hdf5_fixtures import (
    build_legacy_fixture,
    build_rollout_fixture,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_adapter_normalizes_six_frames_without_mutating_source(tmp_path: Path):
    path = build_legacy_fixture(tmp_path / "episode_1.hdf5")
    before = _sha256(path)

    source = open_source(path)
    frames = list(source.iter_frames())

    assert detect_source(path) == "legacy"
    assert source.metadata.source_schema == "legacy_cobot_hdf5"
    assert source.metadata.frame_count == 6
    assert source.metadata.fps == 30.0
    assert len(frames) == 6
    assert frames[3].frame_index == 3
    assert frames[3].sample_timestamp == pytest.approx(0.1)
    assert frames[3].images["cam_high"].shape == (8, 8, 3)
    assert frames[3].qpos.shape == (14,)
    assert frames[3].base_action.shape == (2,)
    assert frames[3].control_source_left is ControlSource.UNKNOWN
    assert frames[3].intervention_id_left == 0
    assert frames[3].valid["action"] is True
    assert before == _sha256(path)


def test_rollout_adapter_preserves_per_side_interventions_and_faults(tmp_path: Path):
    path = build_rollout_fixture(tmp_path / "episode_000003.hdf5")

    source = open_source(path)
    frames = list(source.iter_frames())

    assert detect_source(path) == "rollout_v1"
    assert source.metadata.task_id == "in_the_pot"
    assert source.metadata.episode_uuid == "12345678-1234-5678-1234-567812345678"
    assert len(frames) == 12
    assert frames[3].control_source_left is ControlSource.HUMAN
    assert frames[3].control_source_right is ControlSource.HOLD
    assert frames[3].intervention_id_left == 1
    assert frames[6].control_source_right is ControlSource.HUMAN
    assert frames[8].intervention_id_left == 2
    assert frames[8].intervention_id_right == 2
    assert frames[10].handover_fault == "watchdog"
    assert frames[10].valid["action"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong_action_width", "action"),
        ("different_length", "temporal length"),
        ("missing_camera", "cam_high"),
        ("rollout_without_version", "ambiguous"),
        ("non_monotonic_timestamp", "monotonic"),
        ("not_complete", "complete"),
    ],
)
def test_source_validation_rejects_malformed_files(
    tmp_path: Path, mutation: str, message: str
):
    path = build_rollout_fixture(tmp_path / f"{mutation}.hdf5")
    with h5py.File(path, "r+") as handle:
        if mutation == "wrong_action_width":
            data = np.asarray(handle["action"][:, :13])
            del handle["action"]
            handle.create_dataset("action", data=data)
        elif mutation == "different_length":
            data = np.asarray(handle["base_action"][:-1])
            del handle["base_action"]
            handle.create_dataset("base_action", data=data)
        elif mutation == "missing_camera":
            del handle["observations/images/cam_high"]
        elif mutation == "rollout_without_version":
            del handle.attrs["rollout_schema_version"]
        elif mutation == "non_monotonic_timestamp":
            handle["rollout/sample_timestamp"][5] = 999.0
        elif mutation == "not_complete":
            handle.attrs["completion_state"] = "recording"

    with pytest.raises(SourceValidationError, match=message):
        open_source(path)


def test_detect_source_rejects_unmarked_hdf5_instead_of_guessing(tmp_path: Path):
    path = tmp_path / "unknown.hdf5"
    with h5py.File(path, "x") as handle:
        handle.create_dataset("action", data=np.zeros((1, 14), dtype=np.float32))

    with pytest.raises(SourceValidationError, match="ambiguous"):
        detect_source(path)

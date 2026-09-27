"""Bounded latest-series inspection tests."""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import pytest

from capture_core.labels import LabelStore, LabelValidationError
from capture_core.series_inspection import inspect_prepared_series
from capture_core.storage import SeriesIdentity, prepare_series
from tests.test_labels import _write_episode


SERIES = SeriesIdentity("in_the_pot", "pi05", "round_001")


def _prepared(root: Path):
    return prepare_series(root, SERIES)


def _complete_latest_labels(path: Path, episode_uuid: object) -> None:
    path.with_suffix(".labels.json").write_text(
        json.dumps(
            {
                "label_schema_version": 1,
                "episode_uuid": str(episode_uuid),
                "episode_outcome": "success",
                "episode_quality": "good",
                "keep_for_training": "true",
            }
        ),
        encoding="utf-8",
    )


def test_inspection_opens_only_latest_finalized_episode(tmp_path: Path, monkeypatch):
    for index in range(1, 279):
        _write_episode(tmp_path, index=index)
    prepared = _prepared(tmp_path)
    store = LabelStore(tmp_path)
    opened = []
    real = store._read_record

    def traced(path: Path):
        opened.append(path)
        return real(path)

    monkeypatch.setattr(store, "_read_record", traced)
    result = inspect_prepared_series(store, prepared, SERIES)

    assert result["episode_count"] == 278
    assert result["latest_episode_index"] == 278
    assert [path.name for path in opened] == ["episode_000278.hdf5"]


def test_empty_series_is_ready_without_opening_hdf5(tmp_path: Path, monkeypatch):
    prepared = _prepared(tmp_path)
    store = LabelStore(tmp_path)
    monkeypatch.setattr(
        store,
        "_read_record",
        lambda _path: (_ for _ in ()).throw(AssertionError("must not open")),
    )

    assert inspect_prepared_series(store, prepared, SERIES) == {
        "episode_count": 0,
        "latest_episode_uuid": None,
        "latest_episode_index": None,
        "latest_labels_complete": True,
        "label_blocked": False,
    }


def test_complete_latest_labels_open_the_forward_gate(tmp_path: Path):
    path, episode_uuid = _write_episode(tmp_path, index=7)
    _complete_latest_labels(path, episode_uuid)

    result = inspect_prepared_series(LabelStore(tmp_path), _prepared(tmp_path), SERIES)

    assert result["latest_episode_uuid"] == str(episode_uuid)
    assert result["latest_labels_complete"] is True
    assert result["label_blocked"] is False


def test_corrupt_highest_finalized_episode_fails_closed(tmp_path: Path):
    _write_episode(tmp_path, index=1)
    directory = tmp_path / "in_the_pot" / "pi05" / "round_001"
    (directory / "episode_000002.hdf5").write_bytes(b"not-hdf5")

    with pytest.raises(LabelValidationError, match="latest_episode_invalid"):
        inspect_prepared_series(LabelStore(tmp_path), _prepared(tmp_path), SERIES)


def test_higher_incomplete_tail_fails_closed_without_opening_older_episode(
    tmp_path: Path, monkeypatch
):
    _write_episode(tmp_path, index=1)
    directory = tmp_path / "in_the_pot" / "pi05" / "round_001"
    (directory / "episode_000002.hdf5.incomplete").write_bytes(b"partial")
    store = LabelStore(tmp_path)
    monkeypatch.setattr(
        store,
        "_read_record",
        lambda _path: (_ for _ in ()).throw(AssertionError("must fail before open")),
    )

    with pytest.raises(LabelValidationError, match="latest_episode_incomplete"):
        inspect_prepared_series(store, _prepared(tmp_path), SERIES)


def test_latest_identity_mismatch_fails_closed(tmp_path: Path):
    path, _episode_uuid = _write_episode(tmp_path, index=3)
    with h5py.File(path, "r+") as episode:
        episode.attrs["task_id"] = "another_task"

    with pytest.raises(LabelValidationError, match="latest_episode_invalid"):
        inspect_prepared_series(LabelStore(tmp_path), _prepared(tmp_path), SERIES)

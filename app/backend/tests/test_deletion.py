"""Permanent rollout deletion and append-only index ledger tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import h5py
import pytest

from capture_core.deletion import (
    DeletionError,
    permanently_delete_episode,
    read_deletion_records,
)


def _episode(series: Path, index: int = 4) -> tuple[Path, str]:
    series.mkdir(parents=True, exist_ok=True)
    episode_uuid = str(uuid4())
    path = series / f"episode_{index:06d}.hdf5"
    with h5py.File(path, "w") as episode:
        episode.attrs["episode_uuid"] = episode_uuid
        episode.attrs["episode_index"] = index
        episode.attrs["completion_state"] = "complete"
    path.with_suffix(".labels.json").write_text("{}\n", encoding="utf-8")
    preview = series / ".previews" / episode_uuid
    preview.mkdir(parents=True)
    (preview / "preview.mp4").write_bytes(b"video")
    return path, episode_uuid


def test_permanent_delete_removes_artifacts_and_records_identity(tmp_path: Path):
    series = tmp_path / "in_the_pot" / "pi05" / "round_001"
    episode, episode_uuid = _episode(series)
    sidecar = episode.with_suffix(".labels.json")
    preview = series / ".previews" / episode_uuid

    record = permanently_delete_episode(
        episode, sidecar, preview, expected_uuid=episode_uuid
    )

    assert record.episode_uuid == episode_uuid
    assert record.episode_index == 4
    assert not episode.exists()
    assert not sidecar.exists()
    assert not preview.exists()
    assert read_deletion_records(series) == (record,)
    assert not (series / ".task5" / "deleting" / episode_uuid).exists()


def test_permanent_delete_accepts_only_validated_empty_incomplete_episode(
    tmp_path: Path,
):
    series = tmp_path / "plug" / "expert" / "collection"
    series.mkdir(parents=True)
    episode_uuid = str(uuid4())
    expected = series / "episode_000007.hdf5"
    incomplete = Path(str(expected) + ".incomplete")
    with h5py.File(incomplete, "w") as episode:
        episode.attrs["episode_uuid"] = episode_uuid
        episode.attrs["episode_index"] = 7
        episode.attrs["completion_state"] = "error"
        episode.attrs["failure_reason"] = "empty_episode"

    record = permanently_delete_episode(
        expected,
        expected.with_suffix(".labels.json"),
        series / ".previews" / episode_uuid,
        expected_uuid=episode_uuid,
        incomplete_episode_path=incomplete,
    )

    assert record.episode_index == 7
    assert not incomplete.exists()
    assert read_deletion_records(series) == (record,)


def test_incomplete_episode_deletion_rejects_nonempty_artifact(tmp_path: Path):
    series = tmp_path / "plug" / "expert" / "collection"
    series.mkdir(parents=True)
    episode_uuid = str(uuid4())
    expected = series / "episode_000008.hdf5"
    incomplete = Path(str(expected) + ".incomplete")
    with h5py.File(incomplete, "w") as episode:
        episode.attrs["episode_uuid"] = episode_uuid
        episode.attrs["episode_index"] = 8
        episode.attrs["completion_state"] = "error"
        episode.attrs["failure_reason"] = "empty_episode"
        episode.create_dataset("action", data=[0.0])

    with pytest.raises(DeletionError, match="empty deletion request"):
        permanently_delete_episode(
            expected,
            expected.with_suffix(".labels.json"),
            series / ".previews" / episode_uuid,
            expected_uuid=episode_uuid,
            incomplete_episode_path=incomplete,
        )

    assert incomplete.is_file()
    assert read_deletion_records(series) == ()


def test_staging_failure_rolls_back_already_moved_artifacts(tmp_path: Path):
    series = tmp_path / "in_the_pot" / "pi05" / "round_001"
    episode, episode_uuid = _episode(series)
    sidecar = episode.with_suffix(".labels.json")
    preview = series / ".previews" / episode_uuid
    calls = 0

    def failing_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected move failure")
        source.replace(target)

    with pytest.raises(DeletionError, match="stage"):
        permanently_delete_episode(
            episode,
            sidecar,
            preview,
            expected_uuid=episode_uuid,
            replace=failing_replace,
        )

    assert episode.is_file()
    assert sidecar.is_file()
    assert preview.is_dir()
    assert read_deletion_records(series) == ()


def test_permanent_delete_includes_validated_extra_sidecar_directory(tmp_path: Path):
    series = tmp_path / "plug_insertion" / "expert" / "node_pilot_v1"
    episode, episode_uuid = _episode(series)
    segmented = tmp_path / ".segments" / "plug_insertion" / "expert" / "node_pilot_v1" / episode_uuid
    segmented.mkdir(parents=True)
    (segmented / "sidecar.json").write_text("{}\n", encoding="utf-8")

    permanently_delete_episode(
        episode,
        episode.with_suffix(".labels.json"),
        series / ".previews" / episode_uuid,
        expected_uuid=episode_uuid,
        extra_artifacts=(segmented,),
    )

    assert not segmented.exists()
    assert read_deletion_records(series)[0].episode_uuid == episode_uuid


def test_extra_sidecar_staging_failure_rolls_back_all_artifacts(tmp_path: Path):
    series = tmp_path / "plug_insertion" / "expert" / "node_pilot_v1"
    episode, episode_uuid = _episode(series)
    segmented = tmp_path / ".segments" / "plug_insertion" / "expert" / "node_pilot_v1" / episode_uuid
    segmented.mkdir(parents=True)
    (segmented / "sidecar.json").write_text("{}\n", encoding="utf-8")
    calls = 0

    def failing_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected extra move failure")
        source.replace(target)

    with pytest.raises(DeletionError, match="stage"):
        permanently_delete_episode(
            episode,
            episode.with_suffix(".labels.json"),
            series / ".previews" / episode_uuid,
            expected_uuid=episode_uuid,
            extra_artifacts=(segmented,),
            replace=failing_replace,
        )

    assert episode.is_file()
    assert episode.with_suffix(".labels.json").is_file()
    assert (series / ".previews" / episode_uuid).is_dir()
    assert segmented.is_dir()
    assert read_deletion_records(series) == ()


@pytest.mark.parametrize(
    "line",
    [
        "not-json\n",
        '{"episode_uuid":"bad","episode_index":4,"deleted_at":"2026-08-11T00:00:00Z"}\n',
        '{"episode_uuid":"00000000-0000-0000-0000-000000000001","episode_index":true,"deleted_at":"2026-08-11T00:00:00Z"}\n',
        '{"episode_uuid":"00000000-0000-0000-0000-000000000001","episode_index":-1,"deleted_at":"2026-08-11T00:00:00Z"}\n',
        '{"episode_uuid":"00000000-0000-0000-0000-000000000001","episode_index":4,"deleted_at":"not-utc"}\n',
    ],
)
def test_deletion_ledger_rejects_malformed_records(tmp_path: Path, line: str):
    series = tmp_path / "series"
    ledger = series / ".task5" / "deletions.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(line, encoding="utf-8")

    with pytest.raises(DeletionError, match="ledger"):
        read_deletion_records(series)


def test_deletion_ledger_allows_same_index_to_be_deleted_again(tmp_path: Path):
    series = tmp_path / "series"
    ledger = series / ".task5" / "deletions.jsonl"
    ledger.parent.mkdir(parents=True)
    first = {
        "episode_uuid": str(uuid4()),
        "episode_index": 4,
        "deleted_at": "2026-08-11T00:00:00Z",
    }
    second = {**first, "episode_uuid": str(uuid4())}
    ledger.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
    )

    records = read_deletion_records(series)

    assert len(records) == 2
    assert [record.episode_index for record in records] == [4, 4]
    assert records[0].episode_uuid != records[1].episode_uuid


def test_permanent_delete_supports_recreated_trailing_index(tmp_path: Path):
    series = tmp_path / "in_the_pot" / "pi05" / "round_001"
    first_path, first_uuid = _episode(series, index=4)
    permanently_delete_episode(
        first_path,
        first_path.with_suffix(".labels.json"),
        series / ".previews" / first_uuid,
        expected_uuid=first_uuid,
    )
    second_path, second_uuid = _episode(series, index=4)

    permanently_delete_episode(
        second_path,
        second_path.with_suffix(".labels.json"),
        series / ".previews" / second_uuid,
        expected_uuid=second_uuid,
    )

    records = read_deletion_records(series)
    assert [record.episode_index for record in records] == [4, 4]
    assert {record.episode_uuid for record in records} == {first_uuid, second_uuid}


def test_deletion_ledger_rejects_same_uuid_with_different_index(tmp_path: Path):
    series = tmp_path / "series"
    ledger = series / ".task5" / "deletions.jsonl"
    ledger.parent.mkdir(parents=True)
    first = {
        "episode_uuid": str(uuid4()),
        "episode_index": 4,
        "deleted_at": "2026-08-11T00:00:00Z",
    }
    second = {**first, "episode_index": 5}
    ledger.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8"
    )

    with pytest.raises(DeletionError, match="conflict"):
        read_deletion_records(series)


def test_deletion_rejects_symlinked_task5_directory(tmp_path: Path):
    series = tmp_path / "series"
    series.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (series / ".task5").symlink_to(outside, target_is_directory=True)

    with pytest.raises(DeletionError, match="symlink"):
        read_deletion_records(series)

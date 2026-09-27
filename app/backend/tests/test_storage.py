"""Behavioral tests for safe rollout series preparation."""

from pathlib import Path

import pytest

from capture_core.storage import (
    SeriesIdentity,
    StoragePathError,
    prepare_series,
)


def test_prepare_series_creates_missing_path_and_uses_max_index_not_count(
    tmp_path: Path,
):
    root = tmp_path / "new-root"
    series = SeriesIdentity("in_the_pot", "pi05", "round_001")
    parent = root / "in_the_pot" / "pi05" / "round_001"
    parent.mkdir(parents=True)
    (parent / "episode_000000.hdf5").touch()
    (parent / "episode_000004.hdf5.incomplete").touch()
    (parent / "episode_bad.hdf5").touch()

    prepared = prepare_series(root, series)

    assert prepared.data_root == root.resolve()
    assert prepared.episode_directory == parent.resolve()
    assert prepared.existing_indices == (0, 4)
    assert prepared.deleted_indices == ()
    assert prepared.next_episode_index == 5


def test_prepare_series_creates_an_empty_series_at_index_one(tmp_path: Path):
    root = tmp_path / "missing"

    prepared = prepare_series(
        root, SeriesIdentity("in_the_pot", "pi05", "round_001")
    )

    assert prepared.episode_directory.is_dir()
    assert prepared.existing_indices == ()
    assert prepared.deleted_indices == ()
    assert prepared.next_episode_index == 1


def test_prepare_series_reuses_deleted_trailing_index(tmp_path: Path):
    root = tmp_path / "root"
    parent = root / "in_the_pot" / "pi05" / "round_001"
    parent.mkdir(parents=True)
    for index in range(1, 4):
        (parent / f"episode_{index:06d}.hdf5").touch()
    ledger = parent / ".task5" / "deletions.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(
        '{"episode_uuid":"00000000-0000-0000-0000-000000000004",'
        '"episode_index":4,"deleted_at":"2026-08-11T00:00:00Z"}\n',
        encoding="utf-8",
    )

    prepared = prepare_series(
        root, SeriesIdentity("in_the_pot", "pi05", "round_001")
    )

    assert prepared.existing_indices == (1, 2, 3)
    assert prepared.deleted_indices == (4,)
    assert prepared.next_episode_index == 4


def test_prepare_series_does_not_renumber_after_deleting_middle_index(
    tmp_path: Path,
):
    root = tmp_path / "root"
    parent = root / "in_the_pot" / "pi05" / "round_001"
    parent.mkdir(parents=True)
    for index in (2, 3, 4):
        (parent / f"episode_{index:06d}.hdf5").touch()
    ledger = parent / ".task5" / "deletions.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(
        '{"episode_uuid":"00000000-0000-0000-0000-000000000001",'
        '"episode_index":1,"deleted_at":"2026-08-11T00:00:00Z"}\n',
        encoding="utf-8",
    )

    prepared = prepare_series(
        root, SeriesIdentity("in_the_pot", "pi05", "round_001")
    )

    assert prepared.existing_indices == (2, 3, 4)
    assert prepared.next_episode_index == 5


def test_prepare_series_restarts_at_one_after_all_episodes_are_deleted(
    tmp_path: Path,
):
    root = tmp_path / "root"
    parent = root / "in_the_pot" / "pi05" / "round_001"
    ledger = parent / ".task5" / "deletions.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "".join(
            f'{{"episode_uuid":"00000000-0000-0000-0000-00000000000{index}",'
            f'"episode_index":{index},"deleted_at":"2026-08-11T00:00:0{index}Z"}}\n'
            for index in range(1, 5)
        ),
        encoding="utf-8",
    )

    prepared = prepare_series(
        root, SeriesIdentity("in_the_pot", "pi05", "round_001")
    )

    assert prepared.existing_indices == ()
    assert prepared.next_episode_index == 1


def test_prepare_series_can_preserve_monotonic_index_after_deletion(tmp_path: Path):
    root = tmp_path / "root"
    parent = root / "plug_insertion" / "expert" / "node_pilot_v1"
    ledger = parent / ".task5" / "deletions.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        '{"episode_uuid":"00000000-0000-0000-0000-000000000004",'
        '"episode_index":4,"deleted_at":"2026-09-08T00:00:00Z"}\n',
        encoding="utf-8",
    )

    prepared = prepare_series(
        root,
        SeriesIdentity("plug_insertion", "expert", "node_pilot_v1"),
        reuse_deleted_indices=False,
    )

    assert prepared.existing_indices == ()
    assert prepared.deleted_indices == (4,)
    assert prepared.next_episode_index == 5


def test_prepare_series_rejects_relative_unsafe_and_non_directory_roots(
    tmp_path: Path,
):
    with pytest.raises(StoragePathError, match="absolute"):
        prepare_series(
            Path("relative/root"),
            SeriesIdentity("in_the_pot", "pi05", "round_001"),
        )

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(StoragePathError, match="symlink"):
        prepare_series(
            link / "child",
            SeriesIdentity("in_the_pot", "pi05", "round_001"),
        )

    regular_file = tmp_path / "file"
    regular_file.write_text("occupied", encoding="utf-8")
    with pytest.raises(StoragePathError, match="directory"):
        prepare_series(
            regular_file,
            SeriesIdentity("in_the_pot", "pi05", "round_001"),
        )


def test_series_identity_rejects_path_components():
    with pytest.raises(ValueError, match="task_id"):
        SeriesIdentity("../escape", "pi05", "round_001")

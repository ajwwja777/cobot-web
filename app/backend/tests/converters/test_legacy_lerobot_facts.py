from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from converters.legacy_lerobot_facts import annotate_legacy_lerobot


def _dataset(root: Path) -> None:
    meta = root / "meta"
    episodes = meta / "episodes/chunk-000"
    episodes.mkdir(parents=True)
    (meta / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "total_episodes": 2,
                "total_frames": 5,
                "fps": 30,
            }
        ),
        encoding="utf-8",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": [0, 1],
                "length": [2, 3],
                "data/chunk_index": [0, 0],
                "data/file_index": [0, 0],
            }
        ),
        episodes / "file-000.parquet",
    )
    data = root / "data/chunk-000"
    data.mkdir(parents=True)
    pq.write_table(
        pa.table({"episode_index": [0, 0, 1, 1, 1]}),
        data / "file-000.parquet",
    )


def test_annotate_existing_lerobot_writes_neutral_legacy_facts(tmp_path: Path):
    root = tmp_path / "dataset"
    _dataset(root)

    manifest = annotate_legacy_lerobot(
        root,
        repo_id="jiaan/task5_cobot_in_the_pot",
        source_version="v2.1",
    )

    assert manifest["manifest_schema_version"] == 1
    assert manifest["repo_id"] == "jiaan/task5_cobot_in_the_pot"
    assert manifest["source_lerobot_version"] == "v2.1"
    assert manifest["lerobot_version"] == "v3.0"
    assert len(manifest["sources"]) == 2
    assert sum(item["frame_count"] for item in manifest["sources"]) == 5
    assert json.loads((root / "task5_manifest.json").read_text()) == manifest
    for episode, expected_frames in enumerate((2, 3)):
        facts = np.load(root / f"task5_facts/episode_{episode:06d}.npz")
        assert set(facts.files) == {
            "control_source_left",
            "control_source_right",
            "intervention_id_left",
            "intervention_id_right",
            "left_valid",
            "right_valid",
            "action_valid",
            "state_valid",
        }
        assert facts["control_source_left"].shape == (expected_frames,)
        assert not facts["control_source_left"].any()
        assert not facts["intervention_id_right"].any()
        assert facts["left_valid"].all()
        assert facts["action_valid"].all()


def test_annotate_rejects_wrong_version_or_noncontiguous_episodes(tmp_path: Path):
    root = tmp_path / "dataset"
    _dataset(root)
    info = json.loads((root / "meta/info.json").read_text())
    info["codebase_version"] = "v2.1"
    (root / "meta/info.json").write_text(json.dumps(info), encoding="utf-8")

    with pytest.raises(ValueError, match="v3.0"):
        annotate_legacy_lerobot(root, repo_id="jiaan/task", source_version="v2.1")


def test_annotate_never_overwrites_existing_task5_metadata(tmp_path: Path):
    root = tmp_path / "dataset"
    _dataset(root)
    (root / "task5_manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        annotate_legacy_lerobot(root, repo_id="jiaan/task", source_version="v2.1")

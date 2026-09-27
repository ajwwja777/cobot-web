from __future__ import annotations

import hashlib
import json
from pathlib import Path

from converters.lerobot_writer import convert_sources
from converters.validate_source import open_source
from dataset_tools.build_dagger_view import _load_dataset_facts, write_view
from scripts.check_legacy_parity import compare_legacy_frames
from tests.converters.test_lerobot_conversion import _Factory
from tests.fixtures.build_hdf5_fixtures import (
    build_legacy_fixture,
    build_rollout_fixture,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_standard_arrays_match_reference_frame_iterator(tmp_path: Path):
    path = build_legacy_fixture(tmp_path / "legacy.hdf5")

    def reference():
        source = open_source(path)
        for frame in source.iter_frames():
            yield {
                "observation.state": frame.qpos.copy(),
                "action": frame.action.copy(),
                **{
                    f"observation.images.{name}": value.copy()
                    for name, value in frame.images.items()
                },
            }

    report = compare_legacy_frames(path, reference())

    assert report == {
        "frame_count": 6,
        "camera_count": 3,
        "standard_arrays_equal": True,
    }


def test_fixture_pipeline_converts_both_sources_and_builds_both_views(tmp_path: Path):
    legacy = build_legacy_fixture(tmp_path / "legacy.hdf5")
    rollout = build_rollout_fixture(tmp_path / "rollout.hdf5")
    source_hashes = {_sha(legacy), _sha(rollout)}
    demo_root = tmp_path / "data/derived/in_the_pot/pi05/round_001/demo"
    rollout_root = tmp_path / "data/derived/in_the_pot/pi05/round_001/rollout"
    convert_sources(
        [open_source(legacy)],
        output=demo_root,
        repo_id="jiaan/demo",
        task="in the pot",
        backend_factory=_Factory(),
    )
    convert_sources(
        [open_source(rollout)],
        output=rollout_root,
        repo_id="jiaan/rollout",
        task="in the pot",
        backend_factory=_Factory(),
    )
    labels = {
        "keep_for_training": "true",
        "interventions": [
            {
                "side": "left",
                "intervention_id": 1,
                "quality": "good",
                "outcome": "recovered",
            },
            {
                "side": "right",
                "intervention_id": 1,
                "quality": "bad",
                "outcome": "not_recovered",
            },
            {
                "side": "left",
                "intervention_id": 2,
                "quality": "good",
                "outcome": "recovered",
            },
            {
                "side": "right",
                "intervention_id": 2,
                "quality": "good",
                "outcome": "recovered",
            },
        ],
    }
    facts = [
        *[(item, None) for item, _uuid in _load_dataset_facts(demo_root)],
        *[(item, labels) for item, _uuid in _load_dataset_facts(rollout_root)],
    ]

    masked = write_view(
        facts,
        output=tmp_path / "data/derived/in_the_pot/pi05/round_001/masked",
        view="masked",
        action_horizon=3,
        seed=0,
        command_line=["e2e"],
    )
    safe = write_view(
        facts,
        output=tmp_path / "data/derived/in_the_pot/pi05/round_001/safe",
        view="safe",
        action_horizon=3,
        seed=0,
        command_line=["e2e"],
    )

    assert masked["episode_count"] == safe["episode_count"] == 2
    assert masked["frame_count"] == safe["frame_count"] == 18
    assert masked["accepted_window_count"] == 15
    assert safe["accepted_window_count"] == 4
    assert {_sha(legacy), _sha(rollout)} == source_hashes
    assert (
        json.loads(
            (
                masked_root := tmp_path
                / "data/derived/in_the_pot/pi05/round_001/masked/task5_view_manifest.json"
            ).read_text()
        )["rule_version"]
        == "task5_expert_mask_v1"
    )
    assert masked_root.is_file()

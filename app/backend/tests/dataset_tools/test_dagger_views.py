from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from dataset_tools.build_dagger_view import (
    EpisodeFacts,
    _load_dataset_facts,
    build_episode_overlay,
    write_view,
)


def _facts(
    left: list[int],
    right: list[int],
    *,
    left_ids: list[int] | None = None,
    right_ids: list[int] | None = None,
    source_kind: str = "rollout_v1",
    episode_key: str = "rollout:0",
    dataset_root: Path | None = None,
    dataset_episode_index: int | None = None,
) -> EpisodeFacts:
    frames = len(left)
    return EpisodeFacts(
        source_kind=source_kind,
        episode_key=episode_key,
        source_path=Path(f"/{episode_key}.hdf5"),
        source_sha256="a" * 64,
        control_source_left=np.asarray(left, dtype=np.uint8),
        control_source_right=np.asarray(right, dtype=np.uint8),
        intervention_id_left=np.asarray(left_ids or [0] * frames, dtype=np.uint64),
        intervention_id_right=np.asarray(right_ids or [0] * frames, dtype=np.uint64),
        left_valid=np.ones(frames, dtype=np.bool_),
        right_valid=np.ones(frames, dtype=np.bool_),
        action_valid=np.ones(frames, dtype=np.bool_),
        state_valid=np.ones(frames, dtype=np.bool_),
        dataset_root=dataset_root,
        dataset_episode_index=dataset_episode_index,
    )


def _labels(*interventions: dict[str, object], keep: str = "true") -> dict[str, object]:
    return {
        "keep_for_training": keep,
        "episode_outcome": "failure",
        "episode_quality": "bad",
        "interventions": list(interventions),
    }


def _good(side: str, intervention_id: int) -> dict[str, object]:
    return {
        "side": side,
        "intervention_id": intervention_id,
        "quality": "good",
        "outcome": "recovered",
    }


def test_masked_view_keeps_good_left_correction_inside_failed_episode():
    facts = _facts(
        [1, 2, 2, 1],
        [1, 3, 3, 1],
        left_ids=[0, 1, 1, 1],
        right_ids=[0, 0, 0, 0],
    )

    overlay = build_episode_overlay(
        facts,
        labels=_labels(_good("left", 1)),
        view="masked",
        action_horizon=2,
    )

    np.testing.assert_array_equal(overlay.expert_mask[:, :7].sum(axis=1), [0, 7, 7, 0])
    np.testing.assert_array_equal(overlay.expert_mask[:, 7:].sum(axis=1), [0, 0, 0, 0])
    np.testing.assert_array_equal(overlay.train_anchor, [True, True, True, False])


@pytest.mark.parametrize(
    "labels",
    [None, _labels(_good("left", 1), keep="false"), _labels()],
)
def test_unlabeled_rejected_or_unapproved_rollout_has_no_training_targets(labels):
    facts = _facts([2, 2], [3, 3], left_ids=[1, 1])

    overlay = build_episode_overlay(
        facts, labels=labels, view="masked", action_horizon=2
    )

    assert overlay.expert_mask.sum() == 0
    assert not overlay.train_anchor.any()


def test_bad_or_uncertain_interventions_remain_zero():
    facts = _facts([2, 2, 2], [3, 3, 3], left_ids=[1, 1, 1])
    labels = _labels(
        {
            "side": "left",
            "intervention_id": 1,
            "quality": "bad",
            "outcome": "not_recovered",
        }
    )

    overlay = build_episode_overlay(
        facts, labels=labels, view="masked", action_horizon=2
    )

    assert overlay.expert_mask.sum() == 0


def test_legacy_demo_receives_full_masks_without_rollout_labels():
    facts = _facts(
        [0, 0, 0],
        [0, 0, 0],
        source_kind="legacy",
        episode_key="demo:0",
    )

    overlay = build_episode_overlay(facts, labels=None, view="masked", action_horizon=2)

    np.testing.assert_array_equal(overlay.expert_mask, np.ones((3, 14)))
    assert overlay.train_anchor.all()


def test_safe_view_accepts_only_complete_bilateral_horizons():
    facts = _facts(
        [2, 2, 2, 2, 1],
        [2, 2, 2, 3, 1],
        left_ids=[1, 1, 1, 1, 1],
        right_ids=[1, 1, 1, 1, 1],
    )

    overlay = build_episode_overlay(
        facts,
        labels=_labels(_good("left", 1), _good("right", 1)),
        view="safe",
        action_horizon=3,
    )

    np.testing.assert_array_equal(
        overlay.train_anchor, [True, False, False, False, False]
    )
    assert overlay.expert_mask[:3].sum() == 3 * 14
    assert overlay.expert_mask[3:].sum() == 0


def test_invalid_expert_row_removes_its_mask_and_affected_anchor():
    facts = _facts([2, 2], [3, 3], left_ids=[1, 1])
    facts.left_valid[1] = False

    overlay = build_episode_overlay(
        facts,
        labels=_labels(_good("left", 1)),
        view="masked",
        action_horizon=1,
    )

    assert overlay.expert_mask[0, :7].sum() == 7
    assert overlay.expert_mask[1].sum() == 0
    np.testing.assert_array_equal(overlay.train_anchor, [True, False])


def test_write_view_emits_deterministic_npz_overlays_and_audit_manifest(tmp_path: Path):
    demo = _facts([0, 0], [0, 0], source_kind="legacy", episode_key="demo:0")
    rollout = _facts([2, 2], [3, 3], left_ids=[1, 1])
    output = tmp_path / "data/derived/in_the_pot/pi05/round_001/masked"

    manifest = write_view(
        [(rollout, _labels(_good("left", 1))), (demo, None)],
        output=output,
        view="masked",
        action_horizon=2,
        seed=7,
        command_line=["build", "--view", "masked"],
    )

    stored = json.loads((output / "task5_view_manifest.json").read_text())
    assert stored == manifest
    assert stored["episode_count"] == 2
    assert stored["frame_count"] == 4
    assert stored["accepted_window_count"] == 4
    assert stored["rejected_window_count"] == 0
    assert stored["left_expert_element_count"] == 2 * 7 + 2 * 7
    assert stored["right_expert_element_count"] == 2 * 7
    assert stored["bilateral_expert_frame_count"] == 2
    assert stored["rule_version"] == "task5_expert_mask_v1"
    assert [item["episode_key"] for item in stored["episodes"]] == [
        "demo:0",
        "rollout:0",
    ]
    for item in stored["episodes"]:
        payload = np.load(output / item["overlay"])
        assert payload["expert_mask"].shape == (2, 14)
        assert payload["train_anchor"].shape == (2,)


def test_view_manifest_identifies_lerobot_dataset_and_episode(tmp_path: Path):
    dataset_root = (tmp_path / "converted-demo").resolve()
    demo = _facts(
        [0, 0],
        [0, 0],
        source_kind="legacy",
        episode_key="demo:0",
        dataset_root=dataset_root,
        dataset_episode_index=3,
    )
    output = tmp_path / "data/derived/in_the_pot/pi05/round_001/masked"

    manifest = write_view(
        [(demo, None)],
        output=output,
        view="masked",
        action_horizon=2,
        seed=0,
        command_line=["build"],
    )

    assert manifest["episodes"][0]["dataset_root"] == str(dataset_root)
    assert manifest["episodes"][0]["dataset_episode_index"] == 3


def test_existing_lerobot_demo_manifest_is_loaded_as_legacy_expert_data(
    tmp_path: Path,
):
    dataset_root = tmp_path / "cobot_in_the_pot_40episodes"
    facts_root = dataset_root / "task5_facts"
    facts_root.mkdir(parents=True)
    np.savez_compressed(
        facts_root / "episode_000000.npz",
        control_source_left=np.zeros(3, dtype=np.uint8),
        control_source_right=np.zeros(3, dtype=np.uint8),
        intervention_id_left=np.zeros(3, dtype=np.uint64),
        intervention_id_right=np.zeros(3, dtype=np.uint64),
        left_valid=np.ones(3, dtype=np.bool_),
        right_valid=np.ones(3, dtype=np.bool_),
        action_valid=np.ones(3, dtype=np.bool_),
        state_valid=np.ones(3, dtype=np.bool_),
    )
    (dataset_root / "task5_manifest.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "path": "/immutable/legacy/episode_0.parquet",
                        "schema": "legacy_lerobot_v21",
                        "sha256": "b" * 64,
                        "facts_overlay": "task5_facts/episode_000000.npz",
                        "episode_uuid": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = _load_dataset_facts(dataset_root)

    assert len(loaded) == 1
    facts, episode_uuid = loaded[0]
    assert facts.source_kind == "legacy"
    assert facts.dataset_root == dataset_root.resolve()
    assert facts.dataset_episode_index == 0
    assert episode_uuid is None


def test_existing_output_requires_explicit_safe_overwrite(tmp_path: Path):
    demo = _facts([0], [0], source_kind="legacy", episode_key="demo:0")
    unsafe = tmp_path / "ordinary-output"
    unsafe.mkdir()

    with pytest.raises(FileExistsError):
        write_view(
            [(demo, None)],
            output=unsafe,
            view="masked",
            action_horizon=1,
            seed=0,
            command_line=[],
        )
    with pytest.raises(ValueError, match="data/derived"):
        write_view(
            [(demo, None)],
            output=unsafe,
            view="masked",
            action_horizon=1,
            seed=0,
            command_line=[],
            overwrite_derived=True,
        )

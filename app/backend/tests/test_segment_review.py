from __future__ import annotations

import json

import pytest

from segmented_capture.review import SegmentReviewError, SegmentReviewStore


def _episode(project_tmp):
    root = project_tmp / "plug_cycle" / "expert" / "expert80" / "ep-1"
    root.mkdir(parents=True)
    sidecar = {
        "schema_version": "task5-segmented-teach-v1",
        "episode_uuid": "ep-1",
        "commit_state": "complete",
        "nodes": [
            {"node_id": 1, "kind": "start", "capture_state": "recording", "frame_index": 0},
            {"node_id": 2, "kind": "marker", "capture_state": "recording", "frame_index": 3},
            {"node_id": 3, "kind": "end", "capture_state": "committed", "frame_index": 6},
        ],
    }
    (root / "sidecar.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return root


def test_review_defaults_unselected_and_appends_immutable_revisions(project_tmp) -> None:
    root = _episode(project_tmp)
    store = SegmentReviewStore(project_tmp)

    initial = store.load("ep-1")
    assert initial["review_revision"] == 0
    assert [item["interval_id"] for item in initial["intervals"]] == [1, 2]
    assert initial["selected_interval_ids"] == []

    saved = store.save(
        "ep-1", expected_revision=0, selected_interval_ids=[2], note="插入阶段"
    )
    assert saved["review_revision"] == 1
    assert saved["selected_interval_ids"] == [2]
    assert (root / "reviews" / "review_000001.json").is_file()
    assert (root / "segment-review.json").is_file()


def test_review_rejects_stale_revision_and_unknown_interval(project_tmp) -> None:
    _episode(project_tmp)
    store = SegmentReviewStore(project_tmp)
    store.save("ep-1", expected_revision=0, selected_interval_ids=[1], note=None)

    with pytest.raises(SegmentReviewError, match="stale_review_revision"):
        store.save("ep-1", expected_revision=0, selected_interval_ids=[2], note=None)
    with pytest.raises(SegmentReviewError, match="unknown_interval"):
        store.save("ep-1", expected_revision=1, selected_interval_ids=[9], note=None)


def test_incomplete_episode_cannot_be_reviewed(project_tmp) -> None:
    root = _episode(project_tmp)
    sidecar = json.loads((root / "sidecar.json").read_text(encoding="utf-8"))
    sidecar["commit_state"] = "recording"
    (root / "sidecar.json").write_text(json.dumps(sidecar), encoding="utf-8")

    with pytest.raises(SegmentReviewError, match="episode_not_complete"):
        SegmentReviewStore(project_tmp).load("ep-1")

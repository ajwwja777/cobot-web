from __future__ import annotations

import json

import h5py
import numpy as np

from segmented_capture.converter.selection import load_selected_training_view


def _write_episode(root, episode_uuid, nodes, selected):
    sidecar = {
        "schema_version": "task5-segmented-teach-v1",
        "episode_uuid": episode_uuid,
        "commit_state": "complete",
        "training_frame_count": nodes[-1]["frame_index"],
        "nodes": nodes,
    }
    (root / "sidecar.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (root / "segment-review.json").write_text(
        json.dumps({"schema_version": "task5-segment-review-v1", "episode_uuid": episode_uuid, "review_revision": 1, "selected_interval_ids": selected}),
        encoding="utf-8",
    )
    hdf5 = root / "episode.hdf5"
    with h5py.File(hdf5, "w") as handle:
        handle.attrs["episode_uuid"] = episode_uuid
        handle.create_dataset("action", data=np.arange(nodes[-1]["frame_index"] * 14, dtype=np.float32).reshape(nodes[-1]["frame_index"], 14))
    return hdf5


def test_selected_training_view_only_yields_reviewed_node_intervals(project_tmp) -> None:
    root = project_tmp / "ep-1"
    root.mkdir()
    nodes = [
        {"node_id": 1, "kind": "start", "capture_state": "recording", "frame_index": 0},
        {"node_id": 2, "kind": "marker", "capture_state": "recording", "frame_index": 3},
        {"node_id": 3, "kind": "transition", "capture_state": "paused", "frame_index": 5},
        {"node_id": 4, "kind": "transition", "capture_state": "recording", "frame_index": 5},
        {"node_id": 5, "kind": "end", "capture_state": "committed", "frame_index": 8},
    ]
    hdf5 = _write_episode(root, "ep-1", nodes, [2])

    view = load_selected_training_view(root, hdf5_path=hdf5)

    assert len(view.sequences) == 1
    assert view.sequences[0].interval_id == 2
    assert view.sequences[0].frame_indices == (3, 4)
    assert view.sequences[0].node_before == 2
    assert view.sequences[0].node_after == 3


def test_windowing_never_crosses_selected_node_interval(project_tmp) -> None:
    root = project_tmp / "ep-2"
    root.mkdir()
    nodes = [
        {"node_id": 1, "kind": "start", "capture_state": "recording", "frame_index": 0},
        {"node_id": 2, "kind": "marker", "capture_state": "recording", "frame_index": 3},
        {"node_id": 3, "kind": "end", "capture_state": "committed", "frame_index": 6},
    ]
    hdf5 = _write_episode(root, "ep-2", nodes, [1, 2])

    windows = load_selected_training_view(root, hdf5_path=hdf5).windows(horizon=3)

    assert [(w.interval_id, w.frame_indices) for w in windows] == [
        (1, (0, 1, 2)),
        (2, (3, 4, 5)),
    ]


def test_training_view_discovers_hdf5_from_recorded_relative_lineage(project_tmp) -> None:
    data_root = project_tmp / "spool"
    root = data_root / ".segments" / "plug_cycle" / "expert" / "expert80" / "ep-3"
    root.mkdir(parents=True)
    nodes = [
        {"node_id": 1, "kind": "start", "capture_state": "recording", "frame_index": 0},
        {"node_id": 2, "kind": "end", "capture_state": "committed", "frame_index": 2},
    ]
    sidecar = {
        "schema_version": "task5-segmented-teach-v1",
        "episode_uuid": "ep-3",
        "commit_state": "complete",
        "training_frame_count": 2,
        "source_hdf5_relative": "plug_cycle/expert/expert80/episode_000003.hdf5",
        "nodes": nodes,
    }
    (root / "sidecar.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (root / "segment-review.json").write_text(
        json.dumps({"schema_version": "task5-segment-review-v1", "episode_uuid": "ep-3", "review_revision": 1, "selected_interval_ids": [1]}),
        encoding="utf-8",
    )
    hdf5 = data_root / sidecar["source_hdf5_relative"]
    hdf5.parent.mkdir(parents=True)
    with h5py.File(hdf5, "w") as handle:
        handle.attrs["episode_uuid"] = "ep-3"
        handle.create_dataset("action", data=np.zeros((2, 14), dtype=np.float32))

    view = load_selected_training_view(root)

    assert view.sequences[0].frame_indices == (0, 1)

from __future__ import annotations

import json

import pytest

from segmented_capture.schema import active_segments, training_intervals
from segmented_capture.state import (
    SegmentedCaptureReducer,
    SyncedSnapshot,
    TeachMask,
)
from segmented_capture.store import AtomicEpisodeStore, SidecarValidationError


def snap(index: int, keyframe: str | None = None) -> SyncedSnapshot:
    return SyncedSnapshot(float(index), index, keyframe)


def test_finalize_publishes_complete_sidecar_atomically(project_tmp) -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-1")
    started = reducer.start(TeachMask(True, False), snap(0, "nodes/node1"))
    store = AtomicEpisodeStore(project_tmp, episode_uuid="ep-1")
    store.begin(started)
    store.append_frame()
    paused = reducer.teach_exit("left", snap(1, "nodes/node2"))
    store.transition(paused)
    stopped = reducer.stop(snap(1, "nodes/node3"))

    result = store.finalize(stopped)

    payload = json.loads(result.sidecar.read_text())
    assert payload["commit_state"] == "complete"
    assert payload["training_frame_count"] == 1
    assert payload["active_segments"] == [
        {
            "segment_id": 1,
            "start_node_id": 1,
            "end_node_id": 2,
            "start_frame": 0,
            "end_frame_exclusive": 1,
        }
    ]
    assert not list(project_tmp.rglob("*.incomplete"))


def test_complete_sidecar_is_never_overwritten(project_tmp) -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-safe")
    state = reducer.start(TeachMask(False, False), snap(0))
    store = AtomicEpisodeStore(project_tmp, episode_uuid="ep-safe")
    store.begin(state)
    final = reducer.stop(snap(0))
    store.finalize(final)

    with pytest.raises(FileExistsError):
        AtomicEpisodeStore(project_tmp, episode_uuid="ep-safe").begin(state)


def test_validator_rejects_node_path_escape(project_tmp) -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-path")
    state = reducer.start(TeachMask(True, False), snap(0, "../secret.jpg"))
    store = AtomicEpisodeStore(project_tmp, episode_uuid="ep-path")

    with pytest.raises(SidecarValidationError, match="keyframe_ref"):
        store.begin(state)


def test_marker_is_persisted_without_splitting_capture_state(project_tmp) -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-marker")
    state = reducer.start(TeachMask(True, False), snap(0))
    store = AtomicEpisodeStore(project_tmp, episode_uuid="ep-marker")
    store.begin(state)
    store.append_frame()
    marked = reducer.marker(snap(1))
    store.write_marker(marked)
    store.append_frame()
    final = reducer.stop(snap(2))
    payload = json.loads(store.finalize(final).sidecar.read_text())

    assert [node["kind"] for node in payload["nodes"]] == ["start", "marker", "end"]
    assert payload["active_segments"][0]["end_frame_exclusive"] == 2


def test_recovery_reads_incomplete_state(project_tmp) -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-recover")
    started = reducer.start(TeachMask(True, False), snap(0))
    store = AtomicEpisodeStore(project_tmp, episode_uuid="ep-recover")
    store.begin(started)
    store.append_frame()

    recovered = AtomicEpisodeStore.recover(project_tmp, "ep-recover")

    assert recovered["commit_state"] == "recording"
    assert recovered["training_frame_count"] == 1


def test_training_intervals_split_recording_at_marker() -> None:
    nodes = [
        {"node_id": 1, "kind": "start", "capture_state": "recording", "frame_index": 0},
        {"node_id": 2, "kind": "marker", "capture_state": "recording", "frame_index": 4},
        {"node_id": 3, "kind": "transition", "capture_state": "paused", "frame_index": 7},
        {"node_id": 4, "kind": "transition", "capture_state": "recording", "frame_index": 7},
        {"node_id": 5, "kind": "end", "capture_state": "committed", "frame_index": 10},
    ]

    assert training_intervals(nodes) == [
        {"interval_id": 1, "start_node_id": 1, "end_node_id": 2, "start_frame": 0, "end_frame_exclusive": 4},
        {"interval_id": 2, "start_node_id": 2, "end_node_id": 3, "start_frame": 4, "end_frame_exclusive": 7},
        {"interval_id": 3, "start_node_id": 4, "end_node_id": 5, "start_frame": 7, "end_frame_exclusive": 10},
    ]


def test_merged_pause_resume_boundary_splits_active_segments() -> None:
    nodes = [
        {"node_id": 1, "kind": "start", "capture_state": "recording", "frame_index": 0, "merged_events": ["start"]},
        {
            "node_id": 2,
            "kind": "transition",
            "capture_state": "recording",
            "frame_index": 5,
            "merged_events": ["teach_exit:left", "teach_enter:left"],
        },
        {"node_id": 3, "kind": "end", "capture_state": "committed", "frame_index": 8, "merged_events": ["stop"]},
    ]

    assert active_segments(nodes) == [
        {"segment_id": 1, "start_node_id": 1, "end_node_id": 2, "start_frame": 0, "end_frame_exclusive": 5},
        {"segment_id": 2, "start_node_id": 2, "end_node_id": 3, "start_frame": 5, "end_frame_exclusive": 8},
    ]

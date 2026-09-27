from __future__ import annotations

import pytest

from segmented_capture.state import (
    CaptureState,
    InvalidCaptureEvent,
    SegmentedCaptureReducer,
    SyncedSnapshot,
    TeachMask,
)


def fixture(index: int) -> SyncedSnapshot:
    return SyncedSnapshot(sample_timestamp=float(index), frame_index=index)


def test_pause_and_resume_share_one_boundary_and_keep_pause_snapshot() -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-1")
    reducer.start(teach_mask=TeachMask(True, True), snapshot=fixture(0))

    paused = reducer.teach_exit("right", fixture(1))
    merged_pause = reducer.teach_exit("left", fixture(2))
    resumed = reducer.ui_resume(fixture(3))
    merged_resume = reducer.teach_enter("right", fixture(4))

    assert paused.capture_state is CaptureState.PAUSED
    assert len(merged_pause.nodes) == len(paused.nodes)
    assert merged_pause.nodes[-1].merged_sides == ("right", "left")
    assert resumed.capture_state is CaptureState.RECORDING
    assert len(resumed.nodes) == 2
    assert len(merged_resume.nodes) == len(resumed.nodes)
    assert merged_resume.nodes[-1].primary_trigger == "teach_exit"
    assert merged_resume.nodes[-1].frame_index == 1
    assert merged_resume.nodes[-1].sample_timestamp == 1.0
    assert merged_resume.nodes[-1].capture_state is CaptureState.RECORDING
    assert merged_resume.nodes[-1].merged_sides == ("right", "left")
    assert merged_resume.nodes[-1].merged_events == (
        "teach_exit:right",
        "teach_exit:left",
        "ui_resume",
        "teach_enter:right",
    )


def test_first_resume_updates_start_node_to_post_teach_snapshot() -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-first")
    reducer.start(TeachMask(False, False), fixture(0))

    resumed = reducer.teach_enter("left", fixture(1))

    assert len(resumed.nodes) == 1
    assert resumed.capture_state is CaptureState.RECORDING
    assert resumed.nodes[0].kind == "start"
    assert resumed.nodes[0].capture_state is CaptureState.RECORDING
    assert resumed.nodes[0].frame_index == 1
    assert resumed.nodes[0].sample_timestamp == 1.0
    assert resumed.nodes[0].primary_trigger == "start"
    assert resumed.nodes[0].merged_events == ("start", "teach_enter:left")


def test_stop_while_paused_promotes_pause_boundary_to_end() -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-promote")
    reducer.start(TeachMask(True, False), fixture(0))
    reducer.teach_exit("left", fixture(5))

    stopped = reducer.stop(fixture(8))

    assert [node.kind for node in stopped.nodes] == ["start", "end"]
    assert stopped.nodes[-1].capture_state is CaptureState.COMMITTED
    assert stopped.nodes[-1].frame_index == 5
    assert stopped.nodes[-1].sample_timestamp == 5.0
    assert stopped.nodes[-1].primary_trigger == "teach_exit"
    assert stopped.nodes[-1].merged_events == ("teach_exit:left", "stop")


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        (TeachMask(True, False), CaptureState.RECORDING),
        (TeachMask(False, True), CaptureState.RECORDING),
        (TeachMask(True, True), CaptureState.RECORDING),
        (TeachMask(False, False), CaptureState.PAUSED),
    ],
)
def test_start_node_reflects_initial_teach_mask(
    mask: TeachMask, expected: CaptureState
) -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-start")
    state = reducer.start(mask, fixture(0))

    assert state.capture_state is expected
    assert len(state.nodes) == 1
    assert state.nodes[0].kind == "start"
    assert state.nodes[0].node_id == 1


def test_marker_only_works_while_recording_and_does_not_toggle() -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-marker")
    reducer.start(TeachMask(True, False), fixture(0))

    marked = reducer.marker(fixture(1))

    assert marked.capture_state is CaptureState.RECORDING
    assert marked.nodes[-1].kind == "marker"
    assert marked.nodes[-1].node_id == 2

    reducer.ui_pause(fixture(2))
    with pytest.raises(InvalidCaptureEvent, match="marker_requires_recording"):
        reducer.marker(fixture(3))


def test_ui_buttons_reject_requests_that_match_current_state() -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-buttons")
    reducer.start(TeachMask(False, False), fixture(0))

    with pytest.raises(InvalidCaptureEvent, match="already_paused"):
        reducer.ui_pause(fixture(1))

    reducer.ui_resume(fixture(2))
    with pytest.raises(InvalidCaptureEvent, match="already_recording"):
        reducer.ui_resume(fixture(3))


def test_stop_always_adds_final_node_and_seals_reducer() -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-stop")
    reducer.start(TeachMask(False, False), fixture(0))

    stopped = reducer.stop(fixture(1))

    assert stopped.capture_state is CaptureState.COMMITTED
    assert [node.kind for node in stopped.nodes] == ["start", "end"]
    with pytest.raises(InvalidCaptureEvent, match="episode_is_sealed"):
        reducer.teach_enter("left", fixture(2))


def test_duplicate_teach_mask_message_is_ignored_by_observe_mask() -> None:
    reducer = SegmentedCaptureReducer(episode_uuid="ep-mask")
    reducer.start(TeachMask(True, True), fixture(0))

    unchanged = reducer.observe_teach_mask(TeachMask(True, True), fixture(1))

    assert len(unchanged.nodes) == 1
    assert unchanged.generation == 1

"""Single-writer mode lease tests for the unified console."""

from __future__ import annotations

import pytest

from cobot_console.mode import ModeConflict, RecorderModeCoordinator


def test_mode_can_change_only_while_idle():
    modes = RecorderModeCoordinator(initial_mode="normal")
    modes.select("rlt")
    lease = modes.acquire_writer("rlt")
    with pytest.raises(ModeConflict, match="writer_busy"):
        modes.select("normal")
    modes.release_writer(lease)
    assert modes.select("normal").selected_mode == "normal"


def test_repeated_selection_and_release_are_idempotent():
    modes = RecorderModeCoordinator(initial_mode="normal")
    assert modes.select("normal").selected_mode == "normal"
    lease = modes.acquire_writer("normal", episode_uuid="ep-1")
    modes.release_writer(lease)
    modes.release_writer(lease)
    assert modes.snapshot().active_mode is None


def test_wrong_mode_and_second_writer_fail_closed():
    modes = RecorderModeCoordinator(initial_mode="normal")
    with pytest.raises(ModeConflict, match="mode_not_selected"):
        modes.acquire_writer("rlt")
    lease = modes.acquire_writer("normal")
    with pytest.raises(ModeConflict, match="writer_busy"):
        modes.acquire_writer("normal")
    modes.release_writer(lease)


def test_stale_lease_cannot_release_new_writer():
    modes = RecorderModeCoordinator(initial_mode="normal")
    old = modes.acquire_writer("normal")
    modes.release_writer(old)
    current = modes.acquire_writer("normal")
    with pytest.raises(ModeConflict, match="stale_writer_lease"):
        modes.release_writer(old)
    assert modes.snapshot().writer_token == current.token


def test_fault_retains_owner_until_explicit_release():
    modes = RecorderModeCoordinator(initial_mode="rlt")
    lease = modes.acquire_writer("rlt", episode_uuid="ep-9")
    snapshot = modes.mark_fault(lease, "writer_failed")
    assert snapshot.fault_reason == "writer_failed"
    assert snapshot.active_mode == "rlt"
    with pytest.raises(ModeConflict, match="writer_busy"):
        modes.select("normal")
    modes.release_writer(lease)
    assert modes.snapshot().fault_reason == "writer_failed"

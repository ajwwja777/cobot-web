"""Behavioral tests for one immutable synchronized rollout frame."""

from __future__ import annotations

import numpy as np
import pytest

from capture_core.ros_cache import LatestMessageCache
from capture_core.sampler import FrameSampler
from capture_core.schema import ControlSource, EpisodeIdentity


def _identity() -> EpisodeIdentity:
    return EpisodeIdentity(
        task_id="in_the_pot",
        model_id="pi05",
        checkpoint_id="step_2000",
        dataset_round="round_001",
    )


def _cache_with_required_observations(now: float = 10.0) -> LatestMessageCache:
    cache = LatestMessageCache()
    for key, image in {
        "camera_high": np.array([[[10, 20, 30]]], dtype=np.uint8),
        "camera_left": np.array([[[40, 50, 60]]], dtype=np.uint8),
        "camera_right": np.array([[[70, 80, 90]]], dtype=np.uint8),
    }.items():
        cache.put(key, image, source_stamp=now - 0.01, arrival_stamp=now - 0.01)
    for key, offset in (
        ("front_left", 0.0),
        ("front_right", 10.0),
        ("rear_left", 20.0),
        ("rear_right", 30.0),
    ):
        cache.put(
            key,
            {"position": np.arange(7, dtype=np.float32) + offset},
            source_stamp=now - 0.01,
            arrival_stamp=now - 0.01,
        )
    return cache


def test_sample_copies_bgr_images_and_combines_exactly_seven_joints_per_side():
    """Catches camera channel swaps or accidental six/eight joint arm vectors."""
    cache = _cache_with_required_observations()
    cache.put("handover_mode", "policy", source_stamp=9.99, arrival_stamp=9.99)
    cache.put(
        "policy_left",
        np.arange(7, dtype=np.float32) + 40,
        source_stamp=9.99,
        arrival_stamp=9.99,
    )
    cache.put(
        "policy_right",
        np.arange(7, dtype=np.float32) + 50,
        source_stamp=9.99,
        arrival_stamp=9.99,
    )
    cache.put(
        "coordinator_left",
        np.arange(7, dtype=np.float32) + 60,
        source_stamp=9.99,
        arrival_stamp=9.99,
    )
    cache.put(
        "coordinator_right",
        np.arange(7, dtype=np.float32) + 70,
        source_stamp=9.99,
        arrival_stamp=9.99,
    )

    frame = FrameSampler().sample(cache.snapshot(now=10.0), _identity(), frame_index=0)

    assert frame.camera_high_rgb.tolist() == [[[30, 20, 10]]]
    assert frame.qpos.tolist() == list(range(7)) + list(range(10, 17))
    assert frame.rear_observation.tolist() == list(range(20, 27)) + list(range(30, 37))
    assert frame.policy_command_submitted.tolist() == list(range(40, 47)) + list(
        range(50, 57)
    )
    assert frame.coordinator_command.tolist() == list(range(60, 67)) + list(
        range(70, 77)
    )
    assert frame.action.tolist() == list(range(60, 67)) + list(range(70, 77))
    assert frame.valid_mask["qpos"] is True
    assert frame.valid_mask["policy_command_submitted"] is True


def test_missing_policy_in_manual_mode_is_nan_and_never_reuses_old_command():
    """Catches stale policy values being emitted while a human controls an arm."""
    cache = _cache_with_required_observations()
    cache.put("handover_mode", "manual:left", source_stamp=9.99, arrival_stamp=9.99)
    cache.put(
        "policy_left",
        np.arange(7, dtype=np.float32),
        source_stamp=8.0,
        arrival_stamp=8.0,
    )
    cache.put(
        "policy_right",
        np.arange(7, dtype=np.float32),
        source_stamp=8.0,
        arrival_stamp=8.0,
    )

    frame = FrameSampler().sample(cache.snapshot(now=10.0), _identity(), frame_index=0)

    assert np.isnan(frame.policy_command_submitted).all()
    assert frame.valid_mask["policy_command_submitted"] is False
    assert frame.control_source_left is ControlSource.HUMAN
    assert frame.control_source_right is ControlSource.HOLD


def test_missing_velocity_and_effort_zero_pad_but_report_invalidity():
    """Catches absent optional feedback being mislabeled as measured zero motion."""
    cache = _cache_with_required_observations()
    cache.put("handover_mode", "policy", source_stamp=9.99, arrival_stamp=9.99)

    frame = FrameSampler().sample(cache.snapshot(now=10.0), _identity(), frame_index=0)

    assert frame.qvel.tolist() == [0.0] * 14
    assert frame.effort.tolist() == [0.0] * 14
    assert frame.valid_mask["qvel"] is False
    assert frame.valid_mask["effort"] is False


def test_mode_is_authoritative_and_teach_active_is_diagnostic_only():
    """Catches physical teach telemetry overriding the Task2 coordinator mode."""
    cache = _cache_with_required_observations()
    cache.put("handover_mode", "policy", source_stamp=9.99, arrival_stamp=9.99)
    cache.put("teach_left", True, source_stamp=9.99, arrival_stamp=9.99)
    cache.put("teach_right", True, source_stamp=9.99, arrival_stamp=9.99)

    frame = FrameSampler().sample(cache.snapshot(now=10.0), _identity(), frame_index=0)

    assert (frame.control_source_left, frame.control_source_right) == (
        ControlSource.POLICY,
        ControlSource.POLICY,
    )
    assert (frame.teach_active_left, frame.teach_active_right) == (True, True)


def test_missing_mode_is_unknown_and_sample_time_must_be_monotonic():
    """Catches inventing authority without state or accepting time regression."""
    cache = _cache_with_required_observations()
    sampler = FrameSampler()

    missing = sampler.sample(cache.snapshot(now=10.0), _identity(), frame_index=0)

    assert (missing.control_source_left, missing.control_source_right) == (
        ControlSource.UNKNOWN,
        ControlSource.UNKNOWN,
    )
    with pytest.raises(ValueError, match="monotonic"):
        sampler.sample(cache.snapshot(now=9.0), _identity(), frame_index=1)


def test_latched_handover_mode_remains_authoritative_between_transitions():
    """Catches losing intervention labels one second after a latched mode update."""
    cache = _cache_with_required_observations()
    cache.put("handover_mode", "manual:left", source_stamp=8.0, arrival_stamp=8.0)

    frame = FrameSampler().sample(
        cache.snapshot(now=10.0), _identity(), frame_index=0
    )

    assert frame.handover_mode == "manual:left"
    assert frame.valid_mask["handover_mode"] is True
    assert (frame.control_source_left, frame.control_source_right) == (
        ControlSource.HUMAN,
        ControlSource.HOLD,
    )


def test_malformed_joint_side_is_invalid_instead_of_being_padded_or_truncated():
    """Catches a five-joint input silently changing the 14-DOF robot state layout."""
    cache = _cache_with_required_observations()
    cache.put(
        "front_left",
        {"position": np.arange(6, dtype=np.float32)},
        source_stamp=9.99,
        arrival_stamp=9.99,
    )
    cache.put("handover_mode", "policy", source_stamp=9.99, arrival_stamp=9.99)

    frame = FrameSampler().sample(cache.snapshot(now=10.0), _identity(), frame_index=0)

    assert np.isnan(frame.qpos[:7]).all()
    assert frame.valid_mask["qpos"] is False


def test_sampler_copies_fresh_ros_string_fault_payload():
    """Catches preserving only fault timestamps while dropping the diagnostic text."""
    cache = _cache_with_required_observations()
    cache.put("handover_mode", "fault", source_stamp=9.99, arrival_stamp=9.99)
    cache.put(
        "handover_fault",
        {"data": "left servo overcurrent"},
        source_stamp=9.98,
        arrival_stamp=9.99,
    )

    frame = FrameSampler().sample(cache.snapshot(now=10.0), _identity(), frame_index=0)

    assert frame.handover_fault == "left servo overcurrent"
    assert frame.valid_mask["handover_fault"] is True


def test_latched_diagnostic_states_remain_valid_between_transitions():
    """Catches latched fault and teach states expiring as if they were heartbeats."""
    cache = _cache_with_required_observations()
    cache.put("handover_mode", "policy", source_stamp=8.0, arrival_stamp=8.0)
    cache.put("handover_fault", "rear warning", source_stamp=8.0, arrival_stamp=8.0)
    cache.put("teach_left", True, source_stamp=8.0, arrival_stamp=8.0)
    cache.put("teach_right", False, source_stamp=8.0, arrival_stamp=8.0)

    frame = FrameSampler().sample(
        cache.snapshot(now=10.0), _identity(), frame_index=0
    )

    assert frame.handover_fault == "rear warning"
    assert frame.teach_active_left is True
    assert frame.teach_active_right is False
    assert frame.valid_mask["handover_fault"] is True
    assert frame.valid_mask["teach_active_left"] is True
    assert frame.valid_mask["teach_active_right"] is True

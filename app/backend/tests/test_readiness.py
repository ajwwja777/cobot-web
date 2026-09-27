"""Health decisions for the shared Task5 ROS data plane."""

from __future__ import annotations

import numpy as np

from capture_core.readiness import evaluate_readiness
from capture_core.ros_cache import LatestMessageCache


def _snapshot(*, now: float = 10.0, camera_stamp: float = 9.95, mode_stamp: float = 9.5):
    cache = LatestMessageCache()
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    for key in ("camera_high", "camera_left", "camera_right"):
        cache.put(key, image, source_stamp=camera_stamp, arrival_stamp=camera_stamp)
    cache.put("handover_mode", "policy", source_stamp=mode_stamp, arrival_stamp=mode_stamp)
    cache.put("teach_left", False, source_stamp=mode_stamp, arrival_stamp=mode_stamp)
    cache.put("teach_right", False, source_stamp=mode_stamp, arrival_stamp=mode_stamp)
    return cache.snapshot(now)


def test_all_required_operator_inputs_are_ready():
    result = evaluate_readiness(
        {"state": "ready", "error_code": None},
        _snapshot(),
    )
    assert result == {"status": "ok", "error_code": None, "stale_keys": []}


def test_bridge_error_has_priority_over_cache_freshness():
    result = evaluate_readiness(
        {"state": "not_ready", "error_code": "ros_node_unregistered"},
        _snapshot(),
    )
    assert result["status"] == "not_ready"
    assert result["error_code"] == "ros_node_unregistered"


def test_any_stale_camera_fails_with_camera_code():
    result = evaluate_readiness(
        {"state": "ready", "error_code": None},
        _snapshot(camera_stamp=9.0),
    )
    assert result["status"] == "not_ready"
    assert result["error_code"] == "camera_stale"
    assert result["stale_keys"] == ["camera_high", "camera_left", "camera_right"]


def test_missing_or_stale_handover_inputs_fail_with_handover_code():
    result = evaluate_readiness(
        {"state": "ready", "error_code": None},
        _snapshot(mode_stamp=8.0),
    )
    assert result["status"] == "not_ready"
    assert result["error_code"] == "handover_stale"
    assert result["stale_keys"] == ["teach_left", "teach_right"]

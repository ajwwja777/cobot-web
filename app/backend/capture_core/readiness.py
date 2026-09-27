"""Stable health decisions for the Task5 ROS data plane."""

from __future__ import annotations

from typing import Dict, Mapping

from .ros_cache import CacheSnapshot
from .topics import CAMERA_KEYS

_TEACH_KEYS = frozenset(("teach_left", "teach_right"))


def evaluate_readiness(
    bridge_status: Mapping[str, object], snapshot: CacheSnapshot
) -> Dict[str, object]:
    """Combine ROS registration state with operator-critical input freshness."""
    if bridge_status.get("state") != "ready":
        return {
            "status": "not_ready",
            "error_code": bridge_status.get("error_code") or "ros_not_ready",
        }

    stale_cameras = sorted(key for key in CAMERA_KEYS if not snapshot.is_fresh(key))
    if stale_cameras:
        return {
            "status": "not_ready",
            "error_code": "camera_stale",
            "stale_keys": stale_cameras,
        }

    # Mode is latched and published on transitions, not a heartbeat.
    # The bridge verifies its publisher is live in the current ROS master.
    mode = snapshot.get("handover_mode")
    stale_handover = [key for key in _TEACH_KEYS if not snapshot.is_fresh(key)]
    if not isinstance(mode, str) or not mode:
        stale_handover.append("handover_mode")
    stale_handover.sort()
    if stale_handover:
        return {
            "status": "not_ready",
            "error_code": "handover_stale",
            "stale_keys": stale_handover,
        }

    return {"status": "ok", "error_code": None, "stale_keys": []}

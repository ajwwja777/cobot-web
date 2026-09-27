"""Task2 ROS topic names and freshness windows for the read-only recorder."""

from __future__ import annotations

REQUIRED_TOPICS: dict[str, str] = {
    "camera_high": "/camera_f/color/image_raw",
    "camera_left": "/camera_l/color/image_raw",
    "camera_right": "/camera_r/color/image_raw",
    "front_left": "/puppet/joint_left",
    "front_right": "/puppet/joint_right",
    "rear_left": "/task2/teach/rear_left/joint_states",
    "rear_right": "/task2/teach/rear_right/joint_states",
    "policy_left": "/task2/policy/joint_left",
    "policy_right": "/task2/policy/joint_right",
    "coordinator_left": "/master/joint_left",
    "coordinator_right": "/master/joint_right",
    "teach_left": "/task2/teach/rear_left/teach_active",
    "teach_right": "/task2/teach/rear_right/teach_active",
    "handover_mode": "/task2/teach_handover/mode",
    "handover_fault": "/task2/teach_handover/fault",
}

CAMERA_KEYS = frozenset(("camera_high", "camera_left", "camera_right"))
FRONT_KEYS = frozenset(("front_left", "front_right"))
REAR_KEYS = frozenset(("rear_left", "rear_right"))
POLICY_KEYS = frozenset(("policy_left", "policy_right"))
COORDINATOR_KEYS = frozenset(("coordinator_left", "coordinator_right"))
MODE_KEYS = frozenset(("handover_mode", "handover_fault", "teach_left", "teach_right"))

CAMERA_FRESHNESS_SECONDS = 0.20
JOINT_FEEDBACK_FRESHNESS_SECONDS = 0.10
COMMAND_FRESHNESS_SECONDS = 0.25
MODE_FRESHNESS_SECONDS = 1.0


def freshness_window_seconds(key: str) -> float:
    """Return the arrival-time freshness contract for one required stream."""
    if key in CAMERA_KEYS:
        return CAMERA_FRESHNESS_SECONDS
    if key in FRONT_KEYS or key in REAR_KEYS:
        return JOINT_FEEDBACK_FRESHNESS_SECONDS
    if key in POLICY_KEYS or key in COORDINATOR_KEYS:
        return COMMAND_FRESHNESS_SECONDS
    if key in MODE_KEYS:
        return MODE_FRESHNESS_SECONDS
    raise KeyError(f"unknown Task2 stream: {key}")

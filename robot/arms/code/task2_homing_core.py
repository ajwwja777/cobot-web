#!/usr/bin/env python3
"""Pure homing logic: pose validation and speed-limited interpolation.

No ROS, no hardware.  Everything here is decided offline so the two nodes that
actually move arms (the coordinator for the front pair, the rear driver for the
rear pair) only have to execute a list of waypoints.

Deliberately NOT here: any check on how far the target is from the arm.  A
distance limit was considered and rejected - refusing to move because the arm
is far from home just produces an error at the exact moment the operator wants
the arm to come home.  Speed is what keeps a long move safe, not a veto.
"""

import math
from typing import Iterable, List, Sequence, Tuple


JOINT_COUNT = 7
GRIPPER_INDEX = 6


class HomingError(ValueError):
    """A pose or motion parameter is unusable."""


def validate_pose(values: Iterable, label: str = "pose") -> Tuple[float, ...]:
    """Seven finite numbers, gripper last.

    This validates that a pose is a pose.  It says nothing about whether the
    arm can get there, and nothing about how far away it is.
    """
    try:
        items = list(values)
    except TypeError as exc:
        raise HomingError("{} must be a sequence of 7 numbers".format(label)) from exc
    if len(items) != JOINT_COUNT:
        raise HomingError(
            "{} must contain exactly {} values, got {}".format(
                label, JOINT_COUNT, len(items)
            )
        )
    out = []
    for index, value in enumerate(items):
        if isinstance(value, bool):
            raise HomingError("{}[{}] must be a number, not bool".format(label, index))
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise HomingError(
                "{}[{}] is not a number: {!r}".format(label, index, value)
            ) from exc
        if not math.isfinite(number):
            raise HomingError("{}[{}] is not finite: {!r}".format(label, index, value))
        out.append(number)
    return tuple(out)


def _positive(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HomingError("{} must be a number".format(label)) from exc
    if not math.isfinite(number) or number <= 0.0:
        raise HomingError("{} must be finite and greater than zero".format(label))
    return number


def step_limits(
    joint_rad_per_sec, gripper_m_per_sec, publish_rate_hz
) -> Tuple[float, float]:
    """Per-tick limits from a speed and a publish rate."""
    joint_speed = _positive(joint_rad_per_sec, "joint_rad_per_sec")
    gripper_speed = _positive(gripper_m_per_sec, "gripper_m_per_sec")
    rate = _positive(publish_rate_hz, "publish_rate_hz")
    return joint_speed / rate, gripper_speed / rate


def plan_ramp(
    start: Sequence[float],
    target: Sequence[float],
    max_joint_step: float,
    max_gripper_step: float,
) -> List[Tuple[float, ...]]:
    """Waypoints from `start` to `target`, no axis exceeding its per-tick step.

    All axes are scaled by the SAME number of ticks, so they start and arrive
    together instead of the fast ones finishing early and the arm walking to
    the pose one joint at a time.

    `start` itself is not emitted - the arm is already there.  The last
    waypoint is exactly `target`, so the move ends on the commanded pose and
    not on an interpolation artifact.  A zero-length move returns [].
    """
    begin = validate_pose(start, "start")
    end = validate_pose(target, "target")
    joint_step = _positive(max_joint_step, "max_joint_step")
    gripper_step = _positive(max_gripper_step, "max_gripper_step")

    ticks = 0
    for index, (a, b) in enumerate(zip(begin, end)):
        limit = gripper_step if index == GRIPPER_INDEX else joint_step
        needed = math.ceil(abs(b - a) / limit)
        ticks = max(ticks, int(needed))
    if ticks <= 0:
        return []

    waypoints = []
    for tick in range(1, ticks + 1):
        fraction = tick / float(ticks)
        waypoints.append(
            tuple(a + (b - a) * fraction for a, b in zip(begin, end))
        )
    waypoints[-1] = end
    return waypoints


def ramp_duration_sec(waypoint_count: int, publish_rate_hz) -> float:
    """How long the planned move will take, for logging and for timeouts."""
    rate = _positive(publish_rate_hz, "publish_rate_hz")
    return max(0, int(waypoint_count)) / rate


def resolve_pose_set(config: dict, pose_name: str) -> dict:
    """Pull one named pose out of a loaded home_poses config.

    Rear entries fall back to the matching front entry: the point of homing the
    rear arms is to put them where the front arms are, so writing the numbers
    twice is a chance to have them disagree.  Override only when they must
    genuinely differ.
    """
    if not isinstance(config, dict):
        raise HomingError("home pose config must be a mapping")
    poses = config.get("poses")
    if not isinstance(poses, dict) or not poses:
        raise HomingError("home pose config has no 'poses' section")
    if pose_name not in poses:
        raise HomingError(
            "unknown pose {!r}; available: {}".format(
                pose_name, ", ".join(sorted(poses))
            )
        )
    entry = poses[pose_name]
    if not isinstance(entry, dict):
        raise HomingError("pose {!r} must be a mapping of arm -> 7 values".format(pose_name))

    resolved = {}
    for side in ("left", "right"):
        front_key = "front_" + side
        if front_key not in entry:
            raise HomingError(
                "pose {!r} is missing {}".format(pose_name, front_key)
            )
        resolved[front_key] = validate_pose(entry[front_key], front_key)
        rear_key = "rear_" + side
        if rear_key in entry and entry[rear_key] is not None:
            resolved[rear_key] = validate_pose(entry[rear_key], rear_key)
        else:
            resolved[rear_key] = resolved[front_key]
    return resolved


def speed_settings(config: dict) -> Tuple[float, float, float]:
    """(joint_rad_per_sec, gripper_m_per_sec, publish_rate_hz) with defaults."""
    speed = {}
    if isinstance(config, dict) and isinstance(config.get("speed"), dict):
        speed = config["speed"]
    return (
        _positive(speed.get("joint_rad_per_sec", 0.3), "joint_rad_per_sec"),
        _positive(speed.get("gripper_m_per_sec", 0.05), "gripper_m_per_sec"),
        _positive(speed.get("publish_rate_hz", 50.0), "publish_rate_hz"),
    )

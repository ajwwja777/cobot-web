#!/usr/bin/env python3
"""ROS-independent safety primitives for Task2 policy/manual handover."""

from dataclasses import dataclass
from enum import Enum
from collections import deque
import math
from typing import Dict, Optional, Sequence, Tuple


JOINT_NAMES = tuple("joint{}".format(index) for index in range(7))
_EPSILON = 1e-9


class ValidationError(ValueError):
    """A command or feedback sample is malformed or stale."""


class PairingError(RuntimeError):
    """Left/right samples cannot form one safe atomic pair."""


class TransitionError(RuntimeError):
    """A handover state transition was requested from an invalid mode."""


class Mode(Enum):
    PAUSED = "paused"
    ARMING = "arming"
    POLICY = "policy"
    TO_MANUAL = "to_manual"
    MANUAL = "manual"
    TO_POLICY = "to_policy"
    RESUMING = "resuming"
    FAULT = "fault"


@dataclass(frozen=True)
class ValidatedJoint:
    positions: Tuple[float, ...]
    stamp_sec: float
    arrival_monotonic: float


@dataclass(frozen=True)
class CommandPair:
    left: ValidatedJoint
    right: ValidatedJoint


def _finite_number(value, label: str) -> float:
    if isinstance(value, bool):
        raise ValidationError("{} must be a finite number, not bool".format(label))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("{} must be a finite number".format(label)) from exc
    if not math.isfinite(number):
        raise ValidationError("{} must be finite".format(label))
    return number


def _positive_finite(value, label: str) -> float:
    number = _finite_number(value, label)
    if number <= 0.0:
        raise ValidationError("{} must be greater than zero".format(label))
    return number


def validate_joint(
    names: Sequence[str],
    positions: Sequence[float],
    stamp_sec: float,
    arrival_monotonic: float,
    now_wall: float,
    now_monotonic: float,
    max_age_sec: float,
) -> ValidatedJoint:
    """Validate one seven-axis sample at its ROS callback boundary."""
    if tuple(names) != JOINT_NAMES:
        raise ValidationError("joint names must be {}".format(JOINT_NAMES))
    if len(positions) != 7:
        raise ValidationError("joint positions must contain exactly 7 values")

    normalized = tuple(
        _finite_number(value, "position[{}]".format(index))
        for index, value in enumerate(positions)
    )
    stamp = _positive_finite(stamp_sec, "stamp_sec")
    arrival = _finite_number(arrival_monotonic, "arrival_monotonic")
    wall_now = _finite_number(now_wall, "now_wall")
    monotonic_now = _finite_number(now_monotonic, "now_monotonic")
    max_age = _positive_finite(max_age_sec, "max_age_sec")

    wall_age = wall_now - stamp
    callback_age = monotonic_now - arrival
    if wall_age < -_EPSILON:
        raise ValidationError("joint timestamp is in the future")
    if callback_age < -_EPSILON:
        raise ValidationError("callback arrival time is in the future")
    if wall_age > max_age + _EPSILON:
        raise ValidationError("joint timestamp is stale")
    if callback_age > max_age + _EPSILON:
        raise ValidationError("joint callback is stale")

    return ValidatedJoint(
        positions=normalized,
        stamp_sec=stamp,
        arrival_monotonic=arrival,
    )


class PairBuffer:
    """Build each left/right command pair once, or fail closed."""

    def __init__(self, max_skew_sec: float, max_age_sec: float) -> None:
        self.max_skew_sec = _positive_finite(max_skew_sec, "max_skew_sec")
        self.max_age_sec = _positive_finite(max_age_sec, "max_age_sec")
        self._pending: Dict[str, ValidatedJoint] = {}
        # Keep recent objects alive so Python cannot recycle their ids and so
        # replay detection stays bounded during long-running control loops.
        self._consumed = deque(maxlen=256)

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def clear(self) -> None:
        self._pending.clear()

    def _is_stale(self, joint: ValidatedJoint, now_monotonic: float) -> bool:
        age = now_monotonic - joint.arrival_monotonic
        return age < -_EPSILON or age > self.max_age_sec + _EPSILON

    def offer(
        self, side: str, joint: ValidatedJoint, now_monotonic: float
    ) -> Optional[CommandPair]:
        if side not in ("left", "right"):
            raise PairingError("side must be left or right")
        if not isinstance(joint, ValidatedJoint):
            raise PairingError("joint must be a ValidatedJoint")
        try:
            now = _finite_number(now_monotonic, "now_monotonic")
        except ValidationError as exc:
            raise PairingError(str(exc)) from exc
        if any(consumed is joint for consumed in self._consumed):
            raise PairingError("joint message was already consumed")
        if side in self._pending:
            raise PairingError("duplicate {} message before pair completion".format(side))
        if self._is_stale(joint, now):
            self.clear()
            raise PairingError("{} message is stale".format(side))

        self._pending[side] = joint
        other_side = "right" if side == "left" else "left"
        if other_side not in self._pending:
            return None

        left = self._pending["left"]
        right = self._pending["right"]
        if self._is_stale(left, now) or self._is_stale(right, now):
            self._consumed.extend((left, right))
            self.clear()
            raise PairingError("pending command pair became stale")
        if abs(left.stamp_sec - right.stamp_sec) > self.max_skew_sec + _EPSILON:
            self._consumed.extend((left, right))
            self.clear()
            raise PairingError("left/right command skew exceeds limit")

        self._consumed.extend((left, right))
        self.clear()
        return CommandPair(left=left, right=right)


def _feedback_is_fresh(
    joint: ValidatedJoint, now_monotonic: float, max_age_sec: float
) -> bool:
    age = now_monotonic - joint.arrival_monotonic
    return -_EPSILON <= age <= max_age_sec + _EPSILON


def arms_are_synchronized(
    front_left: ValidatedJoint,
    front_right: ValidatedJoint,
    rear_left: ValidatedJoint,
    rear_right: ValidatedJoint,
    now_monotonic: float,
    joint_tolerance_rad: float,
    gripper_tolerance_m: float,
    max_feedback_age_sec: float,
) -> bool:
    """Return whether both front/rear pairs are fresh and position-aligned."""
    samples = (front_left, front_right, rear_left, rear_right)
    if any(not isinstance(sample, ValidatedJoint) for sample in samples):
        return False
    try:
        now = _finite_number(now_monotonic, "now_monotonic")
        joint_limit = _positive_finite(joint_tolerance_rad, "joint_tolerance_rad")
        gripper_limit = _positive_finite(
            gripper_tolerance_m, "gripper_tolerance_m"
        )
        age_limit = _positive_finite(
            max_feedback_age_sec, "max_feedback_age_sec"
        )
    except ValidationError:
        return False
    if any(not _feedback_is_fresh(sample, now, age_limit) for sample in samples):
        return False

    for front, rear in ((front_left, rear_left), (front_right, rear_right)):
        if any(
            abs(front.positions[index] - rear.positions[index])
            > joint_limit + _EPSILON
            for index in range(6)
        ):
            return False
        if abs(front.positions[6] - rear.positions[6]) > gripper_limit + _EPSILON:
            return False
    return True


class HandoverState:
    """Explicit fail-closed state machine for Task2 control ownership."""

    def __init__(self) -> None:
        self.mode = Mode.PAUSED
        self.fault_reason = ""
        self.policy_pause_confirmed = False

    def _require(self, *allowed: Mode) -> None:
        if self.mode not in allowed:
            raise TransitionError(
                "transition is not legal from {}".format(self.mode.value)
            )

    def begin_arming(self) -> None:
        self._require(Mode.PAUSED)
        self.mode = Mode.ARMING

    def begin_manual(self) -> None:
        self._require(Mode.PAUSED, Mode.ARMING, Mode.POLICY, Mode.RESUMING)
        self.mode = Mode.TO_MANUAL

    def complete_manual(self, policy_pause_confirmed: bool = False) -> None:
        self._require(Mode.TO_MANUAL)
        self.policy_pause_confirmed = bool(policy_pause_confirmed)
        self.mode = Mode.MANUAL

    def begin_policy(self) -> None:
        self._require(Mode.PAUSED, Mode.MANUAL)
        self.mode = Mode.TO_POLICY

    def begin_resuming(self) -> None:
        self._require(Mode.TO_POLICY)
        self.mode = Mode.RESUMING

    def complete_policy(self) -> None:
        self._require(Mode.RESUMING)
        self.policy_pause_confirmed = False
        self.mode = Mode.POLICY

    def pause(self) -> None:
        self._require(
            Mode.ARMING,
            Mode.POLICY,
            Mode.TO_MANUAL,
            Mode.MANUAL,
            Mode.TO_POLICY,
            Mode.RESUMING,
        )
        self.mode = Mode.PAUSED

    def fault(self, reason: str) -> None:
        self.mode = Mode.FAULT
        self.fault_reason = str(reason)

    def reset_fault(self) -> None:
        self._require(Mode.FAULT)
        self.mode = Mode.PAUSED
        self.fault_reason = ""
        self.policy_pause_confirmed = False

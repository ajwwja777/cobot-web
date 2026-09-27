#!/usr/bin/env python3
"""ROS-independent logic for Task2 rear Piper arms held permanently as slaves.

Unlike the dynamic-role driver, this module never writes the master/slave
identity.  The rear arms stay motion-output (slave) arms for their whole life;
manual takeover happens because the operator presses the physical drag-teach
button, which the firmware reports as ctrl_mode 0x02 + teach_status 0x01.

Hardware behaviour this module encodes, measured on firmware S-V1.7-3:

  * Entering drag teaching sets ctrl_mode=0x02 and teach_status=0x01, and the
    arm keeps publishing real joint feedback the whole time.
  * Leaving drag teaching only flips teach_status to 0x02.  ctrl_mode STAYS
    0x02 and teach_status stays sticky until MotionCtrl_1(0x02, 0, 0) is sent.
  * MotionCtrl_2(0x01, ...) is silently ignored while that residue is present,
    so the reset frame is mandatory before position control can be re-entered.
"""

from enum import Enum
import math
import numbers
from typing import Any, Optional, Sequence, Tuple


JOINT_RAD_TO_MILLI_DEG = 57324.840764
FEEDBACK_MILLI_DEG_TO_RAD = 0.017444 / 1000.0
MAX_GRIPPER_METERS = 0.08
MAX_FUTURE_CLOCK_SKEW_SEC = 0.05
ALLOWED_CAN_PORTS = frozenset(("can_rear_left", "can_rear_right"))

# PiperStatusMsg.ctrl_mode
STANDBY_MODE = 0x00
CAN_CONTROL_MODE = 0x01
TEACHING_MODE = 0x02
LINKAGE_TEACHING_INPUT_MODE = 0x06

# PiperStatusMsg.teach_status
TEACH_IDLE = 0x00
TEACH_START_RECORDING = 0x01
TEACH_STOP_RECORDING = 0x02


class CommandValidationError(ValueError):
    """A command is malformed or cannot be safely interpreted."""


class StaleCommandError(CommandValidationError):
    """A command is older than the configured command timeout."""


class TeachStateError(RuntimeError):
    """Drag-teach state could not be determined from fresh hardware feedback."""


class MotionGateError(RuntimeError):
    """A joint command arrived while the motion gate is closed."""


class GateState(Enum):
    """Why the driver will or will not forward a joint command right now."""

    UNKNOWN = "unknown"          # no fresh status yet; fail closed
    TEACHING = "teaching"        # operator owns the arm; never command it
    NOT_READY = "not_ready"      # left teaching, still needs a hold to re-arm
    READY = "ready"              # position control confirmed by fresh feedback
    FAULT = "fault"              # latched; only a new successful re-arm clears


def validate_can_port(can_port: str) -> str:
    if can_port not in ALLOWED_CAN_PORTS:
        allowed = ", ".join(sorted(ALLOWED_CAN_PORTS))
        raise ValueError(
            "Task2 rear teach driver refuses CAN port {!r}; allowed: {}".format(
                can_port, allowed
            )
        )
    return can_port


def parse_bool_param(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "yes", "1"):
            return True
        if normalized in ("false", "no", "0"):
            return False
    raise ValueError(
        "{} must be a boolean or one of true/false, yes/no, 1/0".format(field)
    )


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise CommandValidationError("{} must be a real number".format(field))
    result = float(value)
    if not math.isfinite(result):
        raise CommandValidationError("{} must be finite".format(field))
    return result


def joint_positions_to_piper(positions: Sequence[float]) -> Tuple[int, ...]:
    if positions is None or len(positions) != 7:
        raise CommandValidationError(
            "JointState.position must contain exactly 7 values"
        )
    values = [
        _finite_number(value, "position[{}]".format(index))
        for index, value in enumerate(positions)
    ]
    joints = tuple(round(value * JOINT_RAD_TO_MILLI_DEG) for value in values[:6])
    gripper_meters = min(MAX_GRIPPER_METERS, max(0.0, values[6]))
    return joints + (round(gripper_meters * 1_000_000),)


def feedback_to_joint_positions(
    joint_milli_degrees: Sequence[int], gripper_micrometers: int
) -> Tuple[float, ...]:
    if joint_milli_degrees is None or len(joint_milli_degrees) < 6:
        raise CommandValidationError("joint feedback must contain at least 6 values")
    joints = tuple(
        _finite_number(value, "joint_feedback[{}]".format(index))
        * FEEDBACK_MILLI_DEG_TO_RAD
        for index, value in enumerate(joint_milli_degrees[:6])
    )
    gripper = _finite_number(gripper_micrometers, "gripper_feedback") / 1_000_000.0
    return joints + (gripper,)


def max_joint_delta(left: Sequence[float], right: Sequence[float]) -> float:
    """Largest absolute difference across the six revolute joints."""
    if len(left) < 6 or len(right) < 6:
        raise CommandValidationError("both poses must contain at least 6 joints")
    return max(
        abs(_finite_number(left[index], "pose_a[{}]".format(index))
            - _finite_number(right[index], "pose_b[{}]".format(index)))
        for index in range(6)
    )


class TeachStateTracker:
    """Debounce the physical drag-teach button into a stable active/inactive flag.

    A single frame flipping is not enough to change the answer: the raw reading
    must hold for `stability_window_sec` before it is believed.  Samples older
    than `max_sample_age_sec` make the tracker report "not fresh", and callers
    must then fail closed rather than reuse the last answer.
    """

    def __init__(
        self,
        stability_window_sec: float = 0.2,
        max_sample_age_sec: float = 0.25,
    ) -> None:
        for field, value in (
            ("stability_window_sec", stability_window_sec),
            ("max_sample_age_sec", max_sample_age_sec),
        ):
            number = _finite_number(value, field)
            if number <= 0.0:
                raise ValueError("{} must be greater than zero".format(field))
        self.stability_window_sec = float(stability_window_sec)
        self.max_sample_age_sec = float(max_sample_age_sec)
        self._active: Optional[bool] = None
        self._candidate: Optional[bool] = None
        self._candidate_since: Optional[float] = None
        self._last_sample: Optional[float] = None

    @staticmethod
    def raw_is_teaching(ctrl_mode: Any, teach_status: Any) -> bool:
        try:
            mode = int(ctrl_mode)
            teach = int(teach_status)
        except (TypeError, ValueError):
            return False
        return mode == TEACHING_MODE and teach == TEACH_START_RECORDING

    def offer(
        self, ctrl_mode: Any, teach_status: Any, now_monotonic: float
    ) -> Optional[bool]:
        """Feed one status sample; return the debounced state, or None if unknown."""
        now = _finite_number(now_monotonic, "now_monotonic")
        raw = self.raw_is_teaching(ctrl_mode, teach_status)
        self._last_sample = now
        if self._candidate is None or raw != self._candidate:
            self._candidate = raw
            self._candidate_since = now
        # The very first sample settles immediately: there is no previous
        # answer to protect, and refusing to settle would deadlock startup.
        if self._active is None:
            self._active = raw
            return self._active
        if raw != self._active:
            since = self._candidate_since
            if since is not None and now - since >= self.stability_window_sec:
                self._active = raw
        return self._active

    def is_fresh(self, now_monotonic: float) -> bool:
        if self._last_sample is None:
            return False
        age = _finite_number(now_monotonic, "now_monotonic") - self._last_sample
        return -MAX_FUTURE_CLOCK_SKEW_SEC <= age <= self.max_sample_age_sec

    def state(self, now_monotonic: float) -> Optional[bool]:
        """Debounced state, or None when it cannot be trusted."""
        if self._active is None or not self.is_fresh(now_monotonic):
            return None
        return self._active

    def invalidate(self) -> None:
        self._active = None
        self._candidate = None
        self._candidate_since = None
        self._last_sample = None


class RearTeachController:
    """Decide whether a joint command may reach the arm, and encode it."""

    def __init__(
        self,
        command_timeout_sec: float = 0.25,
        max_hold_delta_rad: float = 0.10,
    ) -> None:
        timeout = _finite_number(command_timeout_sec, "command_timeout_sec")
        if timeout <= 0.0:
            raise ValueError("command_timeout_sec must be greater than zero")
        delta = _finite_number(max_hold_delta_rad, "max_hold_delta_rad")
        if delta <= 0.0:
            raise ValueError("max_hold_delta_rad must be greater than zero")
        self._command_timeout_sec = timeout
        self._max_hold_delta_rad = delta
        self._gate = GateState.UNKNOWN

    @property
    def gate(self) -> GateState:
        return self._gate

    @property
    def motion_ready(self) -> bool:
        return self._gate is GateState.READY

    def set_teaching(self) -> None:
        """Operator owns the arm; drop any readiness immediately."""
        self._gate = GateState.TEACHING

    def set_not_ready(self) -> None:
        """Left teaching, or never armed: a hold must re-arm the arm first."""
        if self._gate is not GateState.FAULT:
            self._gate = GateState.NOT_READY

    def set_ready(self) -> None:
        self._gate = GateState.READY

    def set_unknown(self) -> None:
        if self._gate is not GateState.FAULT:
            self._gate = GateState.UNKNOWN

    def latch_fault(self) -> None:
        self._gate = GateState.FAULT

    def clear_fault(self) -> None:
        if self._gate is GateState.FAULT:
            self._gate = GateState.NOT_READY

    def check_hold_is_reachable(
        self, hold_positions: Sequence[float], measured_positions: Sequence[float]
    ) -> None:
        """Refuse a re-arm target that would command a large unplanned motion.

        After manual takeover the front arms have been following the rear arms,
        so the hold pose the coordinator sends must already be close to what
        this arm measures.  A large delta means the four arms are not actually
        synchronized and entering position control would slam the arm across.
        """
        delta = max_joint_delta(hold_positions, measured_positions)
        if delta > self._max_hold_delta_rad:
            raise MotionGateError(
                "hold target is {:.4f} rad from the measured pose, limit {:.4f}".format(
                    delta, self._max_hold_delta_rad
                )
            )

    def encode_command(
        self,
        positions: Sequence[float],
        now_sec: float,
        stamp_sec: Optional[float],
    ) -> Tuple[int, ...]:
        if self._gate is GateState.TEACHING:
            raise MotionGateError(
                "rear arm is in physical drag teaching; commands are refused"
            )
        if self._gate is not GateState.READY:
            raise MotionGateError(
                "rear arm motion gate is {}; commands are refused".format(
                    self._gate.value
                )
            )
        now = _finite_number(now_sec, "now_sec")
        if now < 0.0:
            raise CommandValidationError("now_sec cannot be negative")
        stamp = now if stamp_sec is None else _finite_number(stamp_sec, "stamp_sec")
        if stamp < 0.0:
            raise CommandValidationError("stamp_sec cannot be negative")
        age = now - stamp
        if age < -MAX_FUTURE_CLOCK_SKEW_SEC:
            raise CommandValidationError(
                "joint command timestamp is {:.3f}s in the future".format(-age)
            )
        if age > self._command_timeout_sec:
            raise StaleCommandError(
                "joint command age {:.3f}s exceeds {:.3f}s timeout".format(
                    age, self._command_timeout_sec
                )
            )
        return joint_positions_to_piper(positions)

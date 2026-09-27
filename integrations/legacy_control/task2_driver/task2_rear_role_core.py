#!/usr/bin/env python3
"""ROS-independent safety and conversion logic for Task2 rear Piper arms."""

from enum import Enum
import math
import numbers
from typing import Any, Callable, Optional, Sequence, Tuple


MASTER_CONFIG = 0xFA
SLAVE_CONFIG = 0xFC
JOINT_RAD_TO_MILLI_DEG = 57324.840764
FEEDBACK_MILLI_DEG_TO_RAD = 0.017444 / 1000.0
MAX_GRIPPER_METERS = 0.08
MAX_FUTURE_CLOCK_SKEW_SEC = 0.05
ALLOWED_CAN_PORTS = frozenset(("can_rear_left", "can_rear_right"))


class CommandValidationError(ValueError):
    """A command is malformed or cannot be safely interpreted."""


class StaleCommandError(CommandValidationError):
    """A command is older than the configured command timeout."""


class RoleError(RuntimeError):
    """The requested operation is not allowed in the current arm role."""


class RoleVerificationError(RoleError):
    """Hardware feedback did not confirm a requested role transition."""


class Role(Enum):
    MASTER = "master"
    SLAVE = "slave"


def validate_can_port(can_port: str) -> str:
    if can_port not in ALLOWED_CAN_PORTS:
        allowed = ", ".join(sorted(ALLOWED_CAN_PORTS))
        raise ValueError(
            "Task2 rear-arm driver refuses CAN port {!r}; allowed: {}".format(
                can_port, allowed
            )
        )
    return can_port


def normalize_role(role: Any) -> Role:
    if isinstance(role, Role):
        return role
    if not isinstance(role, str):
        raise ValueError("role must be 'master' or 'slave'")
    normalized = role.strip().lower()
    for candidate in Role:
        if candidate.value == normalized:
            return candidate
    raise ValueError("invalid role {!r}; expected 'master' or 'slave'".format(role))


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
        raise CommandValidationError("JointState.position must contain exactly 7 values")
    values = [_finite_number(value, "position[{}]".format(index)) for index, value in enumerate(positions)]
    joints = tuple(round(value * JOINT_RAD_TO_MILLI_DEG) for value in values[:6])
    gripper_meters = min(MAX_GRIPPER_METERS, max(0.0, values[6]))
    gripper_micrometers = round(gripper_meters * 1_000_000)
    return joints + (gripper_micrometers,)


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


class RearRoleController:
    """Own role state and validate commands before the ROS adapter sends them."""

    def __init__(self, piper: Any, command_timeout_sec: float = 0.25) -> None:
        timeout = _finite_number(command_timeout_sec, "command_timeout_sec")
        if timeout <= 0.0:
            raise ValueError("command_timeout_sec must be greater than zero")
        self._piper = piper
        self._command_timeout_sec = timeout
        self._role: Optional[Role] = None

    @property
    def role(self) -> Optional[Role]:
        return self._role

    def switch_role(
        self,
        role: Any,
        verifier: Optional[Callable[[Role], bool]] = None,
    ) -> Role:
        target = normalize_role(role)
        config = MASTER_CONFIG if target is Role.MASTER else SLAVE_CONFIG
        # None is the fail-closed TRANSITIONING/FAULT state.  It is set before
        # touching hardware so commands cannot be accepted if the SDK call or
        # feedback verification fails.
        self._role = None
        self._piper.MasterSlaveConfig(config, 0x00, 0x00, 0x00)
        if verifier is not None and not bool(verifier(target)):
            raise RoleVerificationError(
                "{} role not confirmed by hardware feedback".format(target.value)
            )
        self._role = target
        return target

    def latch_fault(self) -> None:
        """Close the command gate after an uncertain hardware operation."""
        self._role = None

    def encode_command(
        self,
        positions: Sequence[float],
        now_sec: float,
        stamp_sec: Optional[float],
    ) -> Tuple[int, ...]:
        if self._role is not Role.SLAVE:
            raise RoleError("rear arm accepts joint commands only in slave role")
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

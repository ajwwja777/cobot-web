#!/usr/bin/env python3
"""State machine for Task2 handover driven by the physical drag-teach button.

The validated-sample, pairing and synchronization primitives are imported from
`task2_handover_core` rather than copied: they are already covered by the
Task2 offline test suite, and a single source of truth means a fix to the
safety checks applies to both coordinators.

What is new here is the mode set.  Role switching is gone; instead the
coordinator waits for the operator to press both rear teach buttons, and waits
again for them to leave teaching before it re-arms the rear arms.
"""

from enum import Enum
from typing import Dict, Optional

from task2_handover_core import (  # noqa: F401  (re-exported for the node)
    CommandPair,
    PairBuffer,
    PairingError,
    TransitionError,
    ValidatedJoint,
    ValidationError,
    arms_are_synchronized,
    validate_joint,
)


class TeachMode(Enum):
    PAUSED = "paused"
    ARMING_POLICY = "arming_policy"
    POLICY = "policy"
    WAITING_TEACH_ENTER = "waiting_teach_enter"
    MANUAL = "manual"
    WAITING_TEACH_EXIT = "waiting_teach_exit"
    RESUMING = "resuming"
    FAULT = "fault"


PROMPT_BY_MODE = {
    TeachMode.PAUSED: "PAUSED",
    TeachMode.ARMING_POLICY: "ARMING REAR ARMS",
    TeachMode.POLICY: "POLICY RUNNING",
    TeachMode.WAITING_TEACH_ENTER: "PRESS BOTH REAR TEACH BUTTONS TO ENTER",
    TeachMode.MANUAL: "MANUAL ACTIVE",
    TeachMode.WAITING_TEACH_EXIT: "CONFIRMING BOTH REAR ARMS LEFT TEACHING",
    TeachMode.RESUMING: "WAITING FOR FRESH POLICY",
    TeachMode.FAULT: "FAULT",
}


class TeachHandoverState:
    """Explicit fail-closed ownership state for the four commanded arms."""

    def __init__(self) -> None:
        self.mode = TeachMode.PAUSED
        self.fault_reason = ""
        self.policy_pause_confirmed = False

    def _require(self, *allowed: TeachMode) -> None:
        if self.mode not in allowed:
            raise TransitionError(
                "transition is not legal from {}".format(self.mode.value)
            )

    def begin_waiting_teach_enter(self) -> None:
        # M is accepted from any live mode so the operator can always take over.
        self._require(
            TeachMode.PAUSED,
            TeachMode.ARMING_POLICY,
            TeachMode.POLICY,
            TeachMode.RESUMING,
        )
        self.mode = TeachMode.WAITING_TEACH_ENTER

    def complete_manual(self, policy_pause_confirmed: bool = False) -> None:
        self._require(TeachMode.WAITING_TEACH_ENTER)
        self.policy_pause_confirmed = bool(policy_pause_confirmed)
        self.mode = TeachMode.MANUAL

    def begin_waiting_teach_exit(self) -> None:
        # PAUSED is included so the very first S also arms the rear arms: the
        # exit wait is satisfied immediately when nobody is holding a button.
        self._require(
            TeachMode.PAUSED, TeachMode.MANUAL, TeachMode.WAITING_TEACH_ENTER
        )
        self.mode = TeachMode.WAITING_TEACH_EXIT

    def begin_arming_policy(self) -> None:
        self._require(TeachMode.PAUSED, TeachMode.WAITING_TEACH_EXIT)
        self.mode = TeachMode.ARMING_POLICY

    def begin_resuming(self) -> None:
        self._require(TeachMode.ARMING_POLICY)
        self.mode = TeachMode.RESUMING

    def complete_policy(self) -> None:
        self._require(TeachMode.RESUMING)
        self.policy_pause_confirmed = False
        self.mode = TeachMode.POLICY

    def fault(self, reason: str) -> None:
        self.mode = TeachMode.FAULT
        self.fault_reason = str(reason)

    def reset_fault(self) -> None:
        self._require(TeachMode.FAULT)
        self.mode = TeachMode.PAUSED
        self.fault_reason = ""
        self.policy_pause_confirmed = False

    @property
    def prompt(self) -> str:
        if self.mode is TeachMode.FAULT:
            return "FAULT: {}".format(self.fault_reason or "unknown")
        return PROMPT_BY_MODE[self.mode]


class TeachSideTracker:
    """Track both rear arms' debounced teach flags with per-side freshness.

    `both_active` / `both_inactive` answer only when both sides have reported
    since the cutoff of the current wait, so a stale latched value from before
    the operator was asked to press cannot satisfy the transition.
    """

    def __init__(self) -> None:
        self._active: Dict[str, Optional[bool]] = {"left": None, "right": None}
        self._arrival: Dict[str, Optional[float]] = {"left": None, "right": None}

    def update(self, side: str, active: bool, arrival_monotonic: float) -> None:
        if side not in ("left", "right"):
            raise ValueError("side must be left or right")
        self._active[side] = bool(active)
        self._arrival[side] = float(arrival_monotonic)

    def get(self, side: str) -> Optional[bool]:
        return self._active[side]

    def reset_wait(self) -> None:
        """Forget arrival times so the next check needs new reports."""
        self._arrival = {"left": None, "right": None}

    def _both(self, expected: bool, cutoff: float) -> bool:
        for side in ("left", "right"):
            arrival = self._arrival[side]
            if arrival is None or arrival < cutoff:
                return False
            if self._active[side] is not expected:
                return False
        return True

    def both_active(self, cutoff: float) -> bool:
        return self._both(True, cutoff)

    def both_inactive(self, cutoff: float) -> bool:
        return self._both(False, cutoff)

    def both_reported(self, cutoff: float) -> bool:
        """Whether both sides have reported since `cutoff`, whatever the value.

        Separating "did the drivers answer" from "what did they answer" lets S
        distinguish a dead driver (a fault) from the operator still being in
        drag teaching (a soft rejection they can fix and retry).
        """
        return all(
            self._arrival[side] is not None and self._arrival[side] >= cutoff
            for side in ("left", "right")
        )

    def any_active(self) -> bool:
        return any(self._active[side] is True for side in ("left", "right"))

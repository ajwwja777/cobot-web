"""Pure, per-side classification of Task2 coordinator control modes."""

from __future__ import annotations

from dataclasses import dataclass

from .schema import ControlSource


@dataclass(frozen=True)
class SourcePair:
    """Authoritative control source for the left and right front arms."""

    left: ControlSource
    right: ControlSource


@dataclass(frozen=True)
class InterventionFrame:
    """Per-sample intervention metadata derived from a :class:`SourcePair`."""

    timestamp: float
    control_source_left: ControlSource
    control_source_right: ControlSource
    is_intervention_left: bool
    is_intervention_right: bool
    intervention_id_left: int
    intervention_id_right: int


_MODE_SOURCES: dict[str, SourcePair] = {
    "policy": SourcePair(ControlSource.POLICY, ControlSource.POLICY),
    "manual:left": SourcePair(ControlSource.HUMAN, ControlSource.HOLD),
    "manual:right": SourcePair(ControlSource.HOLD, ControlSource.HUMAN),
    "manual:left+right": SourcePair(ControlSource.HUMAN, ControlSource.HUMAN),
    "fault": SourcePair(ControlSource.SAFETY, ControlSource.SAFETY),
}
_UNKNOWN_SOURCES = SourcePair(ControlSource.UNKNOWN, ControlSource.UNKNOWN)


def parse_handover_mode(mode: str | None, *, is_fresh: bool = True) -> SourcePair:
    """Map a valid coordinator mode to independent arm sources.

    Task2 publishes mode as latched state, so callers pass whether that state
    has been received, rather than applying a heartbeat timeout.  Physical
    ``teach_active`` signals intentionally do not appear here: they are
    diagnostic telemetry, not an authority source.
    """
    if not is_fresh or not isinstance(mode, str):
        return _UNKNOWN_SOURCES
    return _MODE_SOURCES.get(mode, _UNKNOWN_SOURCES)


class InterventionTracker:
    """Assign monotonically increasing intervention IDs independently per arm."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Start a new episode with independent per-side intervention IDs."""
        self._left_id = 0
        self._right_id = 0
        self._left_was_human = False
        self._right_was_human = False

    def update(self, timestamp: float, pair: SourcePair) -> InterventionFrame:
        """Record one source transition and return its per-side metadata."""
        left_is_human = pair.left is ControlSource.HUMAN
        right_is_human = pair.right is ControlSource.HUMAN
        if left_is_human and not self._left_was_human:
            self._left_id += 1
        if right_is_human and not self._right_was_human:
            self._right_id += 1

        self._left_was_human = left_is_human
        self._right_was_human = right_is_human
        return InterventionFrame(
            timestamp=timestamp,
            control_source_left=pair.left,
            control_source_right=pair.right,
            is_intervention_left=left_is_human,
            is_intervention_right=right_is_human,
            intervention_id_left=self._left_id,
            intervention_id_right=self._right_id,
        )

"""Strict adapter from Task2 coordinator modes to per-arm teach edges."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .state import TeachMask


class TeachModeError(ValueError):
    """The coordinator mode cannot safely drive capture state."""


@dataclass(frozen=True)
class TeachEdge:
    side: str
    entered: bool


_MODE_MASKS = {
    "policy": TeachMask(False, False),
    "manual:left": TeachMask(True, False),
    "manual:right": TeachMask(False, True),
    "manual:left+right": TeachMask(True, True),
}


def mode_to_mask(mode: Optional[str]) -> TeachMask:
    try:
        return _MODE_MASKS[mode]
    except (KeyError, TypeError) as error:
        raise TeachModeError("unknown_or_fault_handover_mode") from error


class TeachModeAdapter:
    def __init__(self, initial_mode: str) -> None:
        self._mask = mode_to_mask(initial_mode)

    @property
    def mask(self) -> TeachMask:
        return self._mask

    def observe(self, mode: str) -> Tuple[TeachEdge, ...]:
        next_mask = mode_to_mask(mode)
        if next_mask == self._mask:
            return ()
        edges = []
        for side in ("left", "right"):
            before = self._mask.left if side == "left" else self._mask.right
            after = next_mask.left if side == "left" else next_mask.right
            if before != after:
                edges.append(TeachEdge(side, entered=after))
        self._mask = next_mask
        return tuple(edges)

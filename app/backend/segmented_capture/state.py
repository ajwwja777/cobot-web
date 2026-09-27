"""Pure state machine for segmented teach capture.

The reducer intentionally knows nothing about ROS, HTTP, HDF5, or robot control.
Every accepted event produces an immutable snapshot suitable for persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional, Tuple


class InvalidCaptureEvent(RuntimeError):
    """Raised when an operator event is invalid for the current state."""


class CaptureState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    FINALIZING = "finalizing"
    COMMITTED = "committed"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True)
class TeachMask:
    left: bool
    right: bool

    @property
    def any_active(self) -> bool:
        return self.left or self.right


@dataclass(frozen=True)
class SyncedSnapshot:
    """Stable reference to the synchronized sample used for one node."""

    sample_timestamp: float
    frame_index: int
    keyframe_ref: Optional[str] = None

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")


@dataclass(frozen=True)
class CaptureNode:
    node_id: int
    kind: str
    capture_state: CaptureState
    frame_index: int
    sample_timestamp: float
    primary_trigger: str
    teach_mask: TeachMask
    keyframe_ref: Optional[str] = None
    merged_events: Tuple[str, ...] = ()
    merged_sides: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CaptureSnapshot:
    episode_uuid: str
    capture_state: CaptureState
    teach_mask: TeachMask
    generation: int
    nodes: Tuple[CaptureNode, ...]


class SegmentedCaptureReducer:
    """Serialize directional teach/UI events into capture nodes."""

    def __init__(self, *, episode_uuid: str) -> None:
        if not episode_uuid:
            raise ValueError("episode_uuid must not be empty")
        self._episode_uuid = episode_uuid
        self._state = CaptureState.IDLE
        self._teach_mask = TeachMask(False, False)
        self._generation = 0
        self._nodes: Tuple[CaptureNode, ...] = ()
        self._capture_anchor_index: Optional[int] = None

    def snapshot(self) -> CaptureSnapshot:
        return CaptureSnapshot(
            episode_uuid=self._episode_uuid,
            capture_state=self._state,
            teach_mask=self._teach_mask,
            generation=self._generation,
            nodes=self._nodes,
        )

    def _require_active(self) -> None:
        if self._state in {
            CaptureState.FINALIZING,
            CaptureState.COMMITTED,
            CaptureState.RECOVERY_REQUIRED,
        }:
            raise InvalidCaptureEvent("episode_is_sealed")
        if self._state is CaptureState.IDLE:
            raise InvalidCaptureEvent("episode_not_started")

    def _new_node(
        self,
        *,
        kind: str,
        target: CaptureState,
        trigger: str,
        snapshot: SyncedSnapshot,
        side: Optional[str] = None,
        capture_anchor: bool = False,
    ) -> None:
        event = trigger if side is None else f"{trigger}:{side}"
        node = CaptureNode(
            node_id=len(self._nodes) + 1,
            kind=kind,
            capture_state=target,
            frame_index=snapshot.frame_index,
            sample_timestamp=snapshot.sample_timestamp,
            primary_trigger=trigger,
            teach_mask=self._teach_mask,
            keyframe_ref=snapshot.keyframe_ref,
            merged_events=(event,),
            merged_sides=() if side is None else (side,),
        )
        self._nodes = self._nodes + (node,)
        if capture_anchor:
            self._capture_anchor_index = len(self._nodes) - 1

    def _merge_with_capture_anchor(
        self,
        trigger: str,
        side: Optional[str],
        *,
        target: Optional[CaptureState] = None,
        snapshot: Optional[SyncedSnapshot] = None,
    ) -> None:
        if self._capture_anchor_index is None:
            raise InvalidCaptureEvent("capture_anchor_missing")
        index = self._capture_anchor_index
        current = self._nodes[index]
        event = trigger if side is None else f"{trigger}:{side}"
        sides = current.merged_sides
        if side is not None and side not in sides:
            sides = sides + (side,)
        updated = replace(
            current,
            capture_state=current.capture_state if target is None else target,
            frame_index=current.frame_index if snapshot is None else snapshot.frame_index,
            sample_timestamp=(
                current.sample_timestamp
                if snapshot is None
                else snapshot.sample_timestamp
            ),
            keyframe_ref=(
                current.keyframe_ref if snapshot is None else snapshot.keyframe_ref
            ),
            teach_mask=self._teach_mask,
            merged_events=current.merged_events + (event,),
            merged_sides=sides,
        )
        self._nodes = self._nodes[:index] + (updated,) + self._nodes[index + 1 :]

    def start(
        self, teach_mask: TeachMask, snapshot: SyncedSnapshot
    ) -> CaptureSnapshot:
        if self._state is not CaptureState.IDLE:
            raise InvalidCaptureEvent("episode_already_started")
        self._teach_mask = teach_mask
        self._state = (
            CaptureState.RECORDING if teach_mask.any_active else CaptureState.PAUSED
        )
        self._generation = 1
        self._new_node(
            kind="start",
            target=self._state,
            trigger="start",
            snapshot=snapshot,
            capture_anchor=True,
        )
        return self.snapshot()

    def _request_capture(
        self,
        target: CaptureState,
        trigger: str,
        side: Optional[str],
        snapshot: SyncedSnapshot,
        *,
        reject_same: bool,
    ) -> CaptureSnapshot:
        self._require_active()
        if self._state is target:
            if reject_same:
                code = (
                    "already_paused"
                    if target is CaptureState.PAUSED
                    else "already_recording"
                )
                raise InvalidCaptureEvent(code)
            self._merge_with_capture_anchor(trigger, side)
        elif target is CaptureState.RECORDING and self._capture_anchor_index is not None:
            anchor = self._nodes[self._capture_anchor_index]
            replace_start_snapshot = anchor.kind == "start" and len(self._nodes) == 1
            self._state = target
            self._merge_with_capture_anchor(
                trigger,
                side,
                target=target,
                snapshot=snapshot if replace_start_snapshot else None,
            )
        else:
            self._state = target
            self._new_node(
                kind="transition",
                target=target,
                trigger=trigger,
                side=side,
                snapshot=snapshot,
                capture_anchor=True,
            )
        self._generation += 1
        return self.snapshot()

    @staticmethod
    def _side_value(mask: TeachMask, side: str) -> bool:
        if side not in {"left", "right"}:
            raise ValueError("side must be left or right")
        return mask.left if side == "left" else mask.right

    def _set_side(self, side: str, active: bool) -> None:
        self._side_value(self._teach_mask, side)
        self._teach_mask = TeachMask(
            active if side == "left" else self._teach_mask.left,
            active if side == "right" else self._teach_mask.right,
        )

    def teach_exit(self, side: str, snapshot: SyncedSnapshot) -> CaptureSnapshot:
        if not self._side_value(self._teach_mask, side):
            return self.snapshot()
        self._set_side(side, False)
        return self._request_capture(
            CaptureState.PAUSED,
            "teach_exit",
            side,
            snapshot,
            reject_same=False,
        )

    def teach_enter(self, side: str, snapshot: SyncedSnapshot) -> CaptureSnapshot:
        if self._side_value(self._teach_mask, side):
            return self.snapshot()
        self._set_side(side, True)
        return self._request_capture(
            CaptureState.RECORDING,
            "teach_enter",
            side,
            snapshot,
            reject_same=False,
        )

    def observe_teach_mask(
        self, teach_mask: TeachMask, snapshot: SyncedSnapshot
    ) -> CaptureSnapshot:
        self._require_active()
        if teach_mask == self._teach_mask:
            return self.snapshot()
        old = self._teach_mask
        for side in ("left", "right"):
            if self._side_value(old, side) and not self._side_value(teach_mask, side):
                self.teach_exit(side, snapshot)
        for side in ("left", "right"):
            if not self._side_value(old, side) and self._side_value(teach_mask, side):
                self.teach_enter(side, snapshot)
        return self.snapshot()

    def ui_pause(self, snapshot: SyncedSnapshot) -> CaptureSnapshot:
        return self._request_capture(
            CaptureState.PAUSED,
            "ui_pause",
            None,
            snapshot,
            reject_same=True,
        )

    def ui_resume(self, snapshot: SyncedSnapshot) -> CaptureSnapshot:
        return self._request_capture(
            CaptureState.RECORDING,
            "ui_resume",
            None,
            snapshot,
            reject_same=True,
        )

    def marker(self, snapshot: SyncedSnapshot) -> CaptureSnapshot:
        self._require_active()
        if self._state is not CaptureState.RECORDING:
            raise InvalidCaptureEvent("marker_requires_recording")
        self._new_node(
            kind="marker",
            target=self._state,
            trigger="ui_marker",
            snapshot=snapshot,
        )
        self._generation += 1
        return self.snapshot()

    def stop(self, snapshot: SyncedSnapshot) -> CaptureSnapshot:
        self._require_active()
        if self._state is CaptureState.PAUSED and self._capture_anchor_index is not None:
            index = self._capture_anchor_index
            current = self._nodes[index]
            if current.kind == "transition":
                self._nodes = self._nodes[:index] + (
                    replace(
                        current,
                        kind="end",
                        capture_state=CaptureState.COMMITTED,
                        merged_events=current.merged_events + ("stop",),
                    ),
                ) + self._nodes[index + 1 :]
            else:
                self._new_node(
                    kind="end",
                    target=CaptureState.COMMITTED,
                    trigger="stop",
                    snapshot=snapshot,
                )
        else:
            self._state = CaptureState.FINALIZING
            self._new_node(
                kind="end",
                target=CaptureState.COMMITTED,
                trigger="stop",
                snapshot=snapshot,
            )
        self._state = CaptureState.COMMITTED
        self._generation += 1
        return self.snapshot()

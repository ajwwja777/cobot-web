"""Serialization and validation for task5-segmented-teach-v1 sidecars."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Optional

from .state import CaptureNode, CaptureSnapshot, CaptureState

SCHEMA_VERSION = "task5-segmented-teach-v1"


class SidecarValidationError(ValueError):
    """Raised when persisted capture metadata violates the schema contract."""


def _safe_relative_path(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SidecarValidationError("keyframe_ref must stay inside episode root")
    return path.as_posix()


def node_to_dict(node: CaptureNode) -> Dict[str, object]:
    return {
        "node_id": node.node_id,
        "kind": node.kind,
        "capture_state": node.capture_state.value,
        "frame_index": node.frame_index,
        "sample_timestamp": node.sample_timestamp,
        "primary_trigger": node.primary_trigger,
        "teach_mask": {
            "left": node.teach_mask.left,
            "right": node.teach_mask.right,
        },
        "keyframe_ref": _safe_relative_path(node.keyframe_ref),
        "merged_events": list(node.merged_events),
        "merged_sides": list(node.merged_sides),
    }


def active_segments(nodes: List[Mapping[str, object]]) -> List[Dict[str, int]]:
    result: List[Dict[str, int]] = []
    active_start: Optional[int] = None
    active_node: Optional[int] = None
    for node in nodes:
        kind = str(node["kind"])
        state = str(node["capture_state"])
        frame = int(node["frame_index"])
        node_id = int(node["node_id"])
        events = tuple(str(value) for value in node.get("merged_events", ()))
        merged_pause_resume = (
            any(value == "ui_pause" or value.startswith("teach_exit:") for value in events)
            and any(value == "ui_resume" or value.startswith("teach_enter:") for value in events)
        )
        if kind in {"start", "transition"}:
            if merged_pause_resume and active_start is not None:
                result.append(
                    {
                        "segment_id": len(result) + 1,
                        "start_node_id": int(active_node),
                        "end_node_id": node_id,
                        "start_frame": active_start,
                        "end_frame_exclusive": frame,
                    }
                )
                active_start = frame
                active_node = node_id
            elif state == CaptureState.RECORDING.value and active_start is None:
                active_start = frame
                active_node = node_id
            elif state == CaptureState.PAUSED.value and active_start is not None:
                result.append(
                    {
                        "segment_id": len(result) + 1,
                        "start_node_id": int(active_node),
                        "end_node_id": node_id,
                        "start_frame": active_start,
                        "end_frame_exclusive": frame,
                    }
                )
                active_start = None
                active_node = None
        elif kind == "end" and active_start is not None:
            result.append(
                {
                    "segment_id": len(result) + 1,
                    "start_node_id": int(active_node),
                    "end_node_id": node_id,
                    "start_frame": active_start,
                    "end_frame_exclusive": frame,
                }
            )
            active_start = None
            active_node = None
    return result


def training_intervals(nodes: List[Mapping[str, object]]) -> List[Dict[str, int]]:
    """Return non-empty recording spans between adjacent node boundaries.

    Unlike ``active_segments``, a marker splits an interval.  These are the
    smallest units an operator may approve for training.
    """
    result: List[Dict[str, int]] = []
    for before, after in zip(nodes, nodes[1:]):
        if str(before["capture_state"]) != CaptureState.RECORDING.value:
            continue
        start = int(before["frame_index"])
        end = int(after["frame_index"])
        if end <= start:
            continue
        result.append(
            {
                "interval_id": len(result) + 1,
                "start_node_id": int(before["node_id"]),
                "end_node_id": int(after["node_id"]),
                "start_frame": start,
                "end_frame_exclusive": end,
            }
        )
    return result


def snapshot_payload(
    snapshot: CaptureSnapshot,
    *,
    training_frame_count: int,
    commit_state: str,
    metadata: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    nodes = [node_to_dict(node) for node in snapshot.nodes]
    validate_nodes(nodes, training_frame_count=training_frame_count)
    payload: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "episode_uuid": snapshot.episode_uuid,
        "commit_state": commit_state,
        "capture_state": snapshot.capture_state.value,
        "generation": snapshot.generation,
        "training_frame_count": training_frame_count,
        "teach_mask": {
            "left": snapshot.teach_mask.left,
            "right": snapshot.teach_mask.right,
        },
        "nodes": nodes,
        "active_segments": active_segments(nodes),
        "training_intervals": training_intervals(nodes),
    }
    if metadata:
        reserved = set(payload).intersection(metadata)
        if reserved:
            raise SidecarValidationError("metadata collides with sidecar fields")
        source = metadata.get("source_hdf5_relative")
        if source is not None:
            _safe_relative_path(str(source))
        payload.update(metadata)
    return payload


def validate_nodes(
    nodes: List[Mapping[str, object]], *, training_frame_count: int
) -> None:
    if training_frame_count < 0:
        raise SidecarValidationError("training_frame_count must be non-negative")
    if not nodes:
        raise SidecarValidationError("at least one node is required")
    ids = [int(node["node_id"]) for node in nodes]
    if ids != list(range(1, len(ids) + 1)):
        raise SidecarValidationError("node IDs must be consecutive from one")
    if nodes[0]["kind"] != "start":
        raise SidecarValidationError("node1 must be start")
    for node in nodes:
        frame = int(node["frame_index"])
        if frame < 0 or frame > training_frame_count:
            raise SidecarValidationError("node frame_index is outside training frames")
        _safe_relative_path(node.get("keyframe_ref"))
    segments = active_segments(nodes)
    previous_end = 0
    for segment in segments:
        start = segment["start_frame"]
        end = segment["end_frame_exclusive"]
        if start < previous_end or end < start or end > training_frame_count:
            raise SidecarValidationError("active segment frame range is invalid")
        previous_end = end

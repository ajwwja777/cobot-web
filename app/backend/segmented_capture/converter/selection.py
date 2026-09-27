"""Load only operator-reviewed node intervals without crossing boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import h5py

from segmented_capture.review import REVIEW_SCHEMA_VERSION, SegmentReviewError
from segmented_capture.schema import SCHEMA_VERSION, training_intervals


@dataclass(frozen=True)
class SelectedSequence:
    source_episode_uuid: str
    interval_id: int
    node_before: int
    node_after: int
    frame_indices: Tuple[int, ...]


@dataclass(frozen=True)
class TrainingWindow:
    source_episode_uuid: str
    interval_id: int
    frame_indices: Tuple[int, ...]


@dataclass(frozen=True)
class SelectedTrainingView:
    source_episode_uuid: str
    sequences: Tuple[SelectedSequence, ...]

    def windows(self, *, horizon: int, stride: int = 1) -> List[TrainingWindow]:
        if horizon <= 0 or stride <= 0:
            raise ValueError("horizon and stride must be positive")
        result: List[TrainingWindow] = []
        for sequence in self.sequences:
            frames = sequence.frame_indices
            for start in range(0, len(frames) - horizon + 1, stride):
                result.append(
                    TrainingWindow(
                        source_episode_uuid=sequence.source_episode_uuid,
                        interval_id=sequence.interval_id,
                        frame_indices=frames[start : start + horizon],
                    )
                )
        return result


def _load(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise SegmentReviewError("invalid_json")
    return value


def _discover_hdf5(episode_root: Path, sidecar) -> Path:
    relative = sidecar.get("source_hdf5_relative")
    if not isinstance(relative, str) or not relative:
        raise SegmentReviewError("source_hdf5_lineage_missing")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise SegmentReviewError("source_hdf5_lineage_invalid")
    segments_root = next(
        (parent for parent in episode_root.parents if parent.name == ".segments"),
        None,
    )
    if segments_root is None:
        raise SegmentReviewError("segments_root_not_found")
    data_root = segments_root.parent.resolve()
    resolved = (data_root / relative_path).resolve()
    try:
        resolved.relative_to(data_root)
    except ValueError as error:
        raise SegmentReviewError("source_hdf5_lineage_invalid") from error
    if not resolved.is_file():
        raise SegmentReviewError("source_hdf5_missing")
    return resolved


def load_selected_training_view(
    episode_root: Path, *, hdf5_path: Optional[Path] = None
) -> SelectedTrainingView:
    episode_root = Path(episode_root)
    sidecar = _load(episode_root / "sidecar.json")
    review = _load(episode_root / "segment-review.json")
    if sidecar.get("schema_version") != SCHEMA_VERSION or sidecar.get("commit_state") != "complete":
        raise SegmentReviewError("episode_not_complete")
    if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise SegmentReviewError("unsupported_review_schema")
    episode_uuid = str(sidecar.get("episode_uuid"))
    if review.get("episode_uuid") != episode_uuid:
        raise SegmentReviewError("episode_uuid_mismatch")
    intervals = training_intervals(list(sidecar.get("nodes", [])))
    by_id = {int(item["interval_id"]): item for item in intervals}
    selected = list(review.get("selected_interval_ids", []))
    if not selected:
        raise SegmentReviewError("no_reviewed_intervals")
    if not set(selected).issubset(by_id):
        raise SegmentReviewError("unknown_interval")
    hdf5_path = (
        Path(hdf5_path)
        if hdf5_path is not None
        else _discover_hdf5(episode_root, sidecar)
    )
    with h5py.File(hdf5_path, "r") as handle:
        if str(handle.attrs.get("episode_uuid", "")) != episode_uuid:
            raise SegmentReviewError("hdf5_episode_uuid_mismatch")
        if "action" not in handle or handle["action"].ndim != 2 or handle["action"].shape[1] != 14:
            raise SegmentReviewError("hdf5_action_schema_mismatch")
        frame_count = int(handle["action"].shape[0])
    if frame_count != int(sidecar.get("training_frame_count", -1)):
        raise SegmentReviewError("hdf5_frame_count_mismatch")
    sequences = []
    for interval_id in sorted(set(selected)):
        interval = by_id[int(interval_id)]
        start = int(interval["start_frame"])
        end = int(interval["end_frame_exclusive"])
        if not (0 <= start < end <= frame_count):
            raise SegmentReviewError("interval_frame_range_invalid")
        sequences.append(
            SelectedSequence(
                source_episode_uuid=episode_uuid,
                interval_id=int(interval_id),
                node_before=int(interval["start_node_id"]),
                node_after=int(interval["end_node_id"]),
                frame_indices=tuple(range(start, end)),
            )
        )
    return SelectedTrainingView(episode_uuid, tuple(sequences))


__all__ = [
    "SelectedSequence",
    "SelectedTrainingView",
    "TrainingWindow",
    "load_selected_training_view",
]

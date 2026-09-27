"""Model-independent normalized episode records used by all converters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from capture_core.schema import ControlSource


@dataclass(frozen=True)
class EpisodeMetadata:
    path: Path
    source_schema: str
    frame_count: int
    fps: float
    dt: float
    task_id: str | None = None
    model_id: str | None = None
    checkpoint_id: str | None = None
    dataset_round: str | None = None
    episode_index: int | None = None
    episode_uuid: str | None = None
    termination_reason: str | None = None


@dataclass(frozen=True)
class NormalizedFrame:
    frame_index: int
    sample_timestamp: float
    images: Mapping[str, NDArray[np.uint8]]
    qpos: NDArray[np.float32]
    qvel: NDArray[np.float32]
    effort: NDArray[np.float32]
    action: NDArray[np.float32]
    base_action: NDArray[np.float32]
    policy_command: NDArray[np.float32]
    coordinator_command: NDArray[np.float32]
    front_observation: NDArray[np.float32]
    rear_observation: NDArray[np.float32]
    control_source_left: ControlSource
    control_source_right: ControlSource
    intervention_id_left: int
    intervention_id_right: int
    is_intervention_left: bool
    is_intervention_right: bool
    teach_active_left: bool
    teach_active_right: bool
    handover_mode: str
    handover_fault: str
    valid: Mapping[str, bool]
    source_timestamps: Mapping[str, float]
    arrival_timestamps: Mapping[str, float]


class EpisodeSource(ABC):
    """Validated immutable view over one HDF5 episode."""

    metadata: EpisodeMetadata

    @abstractmethod
    def iter_frames(self) -> Iterator[NormalizedFrame]:
        """Yield detached frame arrays while opening the source read-only."""

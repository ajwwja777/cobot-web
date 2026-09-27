"""Read-only adapter for the immutable Task5 rollout schema v1."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import h5py
import numpy as np

from capture_core.hdf5_writer import ROLLOUT_VALIDITY_KEYS
from capture_core.schema import ControlSource
from capture_core.topics import REQUIRED_TOPICS

from .legacy_hdf5 import CAMERAS
from .source_types import EpisodeMetadata, EpisodeSource, NormalizedFrame


class RolloutV1Hdf5Source(EpisodeSource):
    def __init__(self, path: Path, metadata: EpisodeMetadata) -> None:
        self.path = path
        self.metadata = metadata

    def iter_frames(self) -> Iterator[NormalizedFrame]:
        with h5py.File(self.path, "r") as handle:
            handover_modes = handle["rollout/handover_mode"].asstr()
            handover_faults = handle["rollout/handover_fault"].asstr()
            for index in range(self.metadata.frame_count):
                yield NormalizedFrame(
                    frame_index=int(handle["rollout/frame_index"][index]),
                    sample_timestamp=float(handle["rollout/sample_timestamp"][index]),
                    images={
                        name: np.asarray(
                            handle[f"observations/images/{name}"][index],
                            dtype=np.uint8,
                        ).copy()
                        for name in CAMERAS
                    },
                    qpos=np.asarray(
                        handle["observations/qpos"][index], dtype=np.float32
                    ).copy(),
                    qvel=np.asarray(
                        handle["observations/qvel"][index], dtype=np.float32
                    ).copy(),
                    effort=np.asarray(
                        handle["observations/effort"][index], dtype=np.float32
                    ).copy(),
                    action=np.asarray(handle["action"][index], dtype=np.float32).copy(),
                    base_action=np.asarray(
                        handle["base_action"][index], dtype=np.float32
                    ).copy(),
                    policy_command=np.asarray(
                        handle["rollout/policy_command_submitted"][index],
                        dtype=np.float32,
                    ).copy(),
                    coordinator_command=np.asarray(
                        handle["rollout/coordinator_command"][index], dtype=np.float32
                    ).copy(),
                    front_observation=np.asarray(
                        handle["rollout/front_observation"][index], dtype=np.float32
                    ).copy(),
                    rear_observation=np.asarray(
                        handle["rollout/rear_observation"][index], dtype=np.float32
                    ).copy(),
                    control_source_left=ControlSource(
                        int(handle["rollout/control_source_left"][index])
                    ),
                    control_source_right=ControlSource(
                        int(handle["rollout/control_source_right"][index])
                    ),
                    intervention_id_left=int(
                        handle["rollout/intervention_id_left"][index]
                    ),
                    intervention_id_right=int(
                        handle["rollout/intervention_id_right"][index]
                    ),
                    is_intervention_left=bool(
                        handle["rollout/is_intervention_left"][index]
                    ),
                    is_intervention_right=bool(
                        handle["rollout/is_intervention_right"][index]
                    ),
                    teach_active_left=bool(handle["rollout/teach_active_left"][index]),
                    teach_active_right=bool(
                        handle["rollout/teach_active_right"][index]
                    ),
                    handover_mode=str(handover_modes[index]),
                    handover_fault=str(handover_faults[index]),
                    valid={
                        key: bool(handle[f"rollout/valid_mask/{key}"][index])
                        for key in ROLLOUT_VALIDITY_KEYS
                    },
                    source_timestamps={
                        key: float(handle[f"rollout/topic_timestamp/{key}"][index])
                        for key in REQUIRED_TOPICS
                    },
                    arrival_timestamps={
                        key: float(handle[f"rollout/arrival_timestamp/{key}"][index])
                        for key in REQUIRED_TOPICS
                    },
                )

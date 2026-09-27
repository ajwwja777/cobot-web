"""Read-only adapter for the original Cobot Station HDF5 contract."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import h5py
import numpy as np

from capture_core.schema import ControlSource

from .source_types import EpisodeMetadata, EpisodeSource, NormalizedFrame

CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")


class LegacyHdf5Source(EpisodeSource):
    def __init__(self, path: Path, metadata: EpisodeMetadata) -> None:
        self.path = path
        self.metadata = metadata

    def iter_frames(self) -> Iterator[NormalizedFrame]:
        zeros14 = np.zeros(14, dtype=np.float32)
        with h5py.File(self.path, "r") as handle:
            for index in range(self.metadata.frame_count):
                yield NormalizedFrame(
                    frame_index=index,
                    sample_timestamp=index * self.metadata.dt,
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
                    policy_command=zeros14.copy(),
                    coordinator_command=zeros14.copy(),
                    front_observation=zeros14.copy(),
                    rear_observation=zeros14.copy(),
                    control_source_left=ControlSource.UNKNOWN,
                    control_source_right=ControlSource.UNKNOWN,
                    intervention_id_left=0,
                    intervention_id_right=0,
                    is_intervention_left=False,
                    is_intervention_right=False,
                    teach_active_left=False,
                    teach_active_right=False,
                    handover_mode="legacy_demo",
                    handover_fault="",
                    valid={
                        "qpos": True,
                        "qvel": True,
                        "effort": True,
                        "action": True,
                        "base_action": True,
                        **{f"camera_{name}": True for name in CAMERAS},
                    },
                    source_timestamps={},
                    arrival_timestamps={},
                )

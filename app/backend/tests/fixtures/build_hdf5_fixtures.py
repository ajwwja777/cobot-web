from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from capture_core.hdf5_writer import (
    COLLECTOR_VERSION,
    PROJECT_ID,
    ROLLOUT_SCALAR_DTYPES,
    ROLLOUT_VALIDITY_KEYS,
    ROLLOUT_VECTOR_FIELDS,
)
from capture_core.schema import ControlSource
from capture_core.topics import REQUIRED_TOPICS

CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")


def _standard_arrays(handle: h5py.File, frames: int) -> None:
    base = np.arange(frames * 14, dtype=np.float32).reshape(frames, 14)
    observations = handle.create_group("observations")
    observations.create_dataset("qpos", data=base / 100)
    observations.create_dataset("qvel", data=base / 200)
    observations.create_dataset("effort", data=base / 300)
    images = observations.create_group("images")
    for camera_index, camera in enumerate(CAMERAS):
        value = np.empty((frames, 8, 8, 3), dtype=np.uint8)
        for frame in range(frames):
            value[frame].fill(camera_index * 40 + frame)
        images.create_dataset(camera, data=value)
    handle.create_dataset("action", data=base / 50)
    handle.create_dataset(
        "base_action", data=np.arange(frames * 2, dtype=np.float32).reshape(frames, 2)
    )


def build_legacy_fixture(path: Path, *, frames: int = 6) -> Path:
    with h5py.File(path, "x") as handle:
        handle.attrs.update(
            {
                "DT": 1 / 30,
                "collector": "cobot-station",
                "collector_mode": "ros_readonly",
                "compress": False,
                "fps": 30,
                "sim": False,
            }
        )
        _standard_arrays(handle, frames)
    return path


def build_rollout_fixture(path: Path, *, frames: int = 12) -> Path:
    if frames != 12:
        raise ValueError("the rollout scenario is defined for exactly 12 frames")
    with h5py.File(path, "x") as handle:
        handle.attrs.update(
            {
                "project_id": PROJECT_ID,
                "collector_version": COLLECTOR_VERSION,
                "rollout_schema_version": 1,
                "fps": 30.0,
                "DT": 1 / 30,
                "task_id": "in_the_pot",
                "model_id": "pi05",
                "checkpoint_id": "2000",
                "dataset_round": "round_001",
                "episode_index": 3,
                "episode_uuid": "12345678-1234-5678-1234-567812345678",
                "start_timestamp": 1000.0,
                "end_timestamp": 1000.4,
                "completion_state": "complete",
                "termination_reason": "operator_stop",
            }
        )
        _standard_arrays(handle, frames)
        rollout = handle.create_group("rollout")
        for name in ROLLOUT_VECTOR_FIELDS:
            rollout.create_dataset(
                name,
                data=np.arange(frames * 14, dtype=np.float32).reshape(frames, 14),
            )

        # policy 0:2, left 2:5, right 5:8, bilateral 8:10, fault 10:12
        left = np.array([1, 1, 2, 2, 2, 3, 3, 3, 2, 2, 0, 0], dtype=np.uint8)
        right = np.array([1, 1, 3, 3, 3, 2, 2, 2, 2, 2, 0, 0], dtype=np.uint8)
        values: dict[str, np.ndarray] = {
            "teach_active_left": left == int(ControlSource.HUMAN),
            "teach_active_right": right == int(ControlSource.HUMAN),
            "control_source_left": left,
            "control_source_right": right,
            "is_intervention_left": left == int(ControlSource.HUMAN),
            "is_intervention_right": right == int(ControlSource.HUMAN),
            "intervention_id_left": np.array(
                [0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2], dtype=np.uint64
            ),
            "intervention_id_right": np.array(
                [0, 0, 0, 0, 0, 1, 1, 1, 2, 2, 2, 2], dtype=np.uint64
            ),
            "sample_timestamp": 1000.0 + np.arange(frames, dtype=np.float64) / 30.0,
            "frame_index": np.arange(frames, dtype=np.uint64),
        }
        for name, dtype in ROLLOUT_SCALAR_DTYPES.items():
            rollout.create_dataset(name, data=values[name].astype(dtype))
        text_dtype = h5py.string_dtype("utf-8")
        modes = np.array(
            [
                "policy",
                "policy",
                "manual:left",
                "manual:left",
                "manual:left",
                "manual:right",
                "manual:right",
                "manual:right",
                "manual:both",
                "manual:both",
                "fault",
                "fault",
            ],
            dtype=object,
        )
        rollout.create_dataset("handover_mode", data=modes, dtype=text_dtype)
        rollout.create_dataset(
            "handover_fault",
            data=np.array([""] * 10 + ["watchdog", "watchdog"], dtype=object),
            dtype=text_dtype,
        )
        timestamps = rollout.create_group("topic_timestamp")
        arrivals = rollout.create_group("arrival_timestamp")
        for key in REQUIRED_TOPICS:
            sequence = 1000.0 + np.arange(frames, dtype=np.float64) / 30.0
            timestamps.create_dataset(key, data=sequence)
            arrivals.create_dataset(key, data=sequence + 0.001)
        valid = rollout.create_group("valid_mask")
        for key in sorted(ROLLOUT_VALIDITY_KEYS):
            values = np.ones(frames, dtype=np.bool_)
            if key in {"action", "coordinator_command"}:
                values[10:] = False
            valid.create_dataset(key, data=values)
        rollout["coordinator_command"][:] = handle["action"][:]
    return path

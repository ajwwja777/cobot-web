"""Preallocated, streaming HDF5 episode writer with atomic publication."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path
from threading import Event, Lock
from typing import Any

import h5py
import numpy as np

from .schema import EpisodeIdentity, FrameSample
from .topics import REQUIRED_TOPICS

ROLLOUT_SCHEMA_VERSION = 1
PROJECT_ID = "task5_jiaan_hil_realworld_rl"
COLLECTOR_VERSION = "v1"
SUMMARY_ATTRIBUTE_ALLOWLIST = frozenset({"termination_reason"})
IMAGE_DATASET_NAMES = ("cam_high", "cam_left_wrist", "cam_right_wrist")
IMAGE_VALIDITY_KEYS = {
    "cam_high": "camera_high",
    "cam_left_wrist": "camera_left",
    "cam_right_wrist": "camera_right",
}
STANDARD_VECTOR_DATASETS = (
    "observations/qpos",
    "observations/qvel",
    "observations/effort",
    "action",
)
ROLLOUT_VECTOR_FIELDS = (
    "policy_command_submitted",
    "coordinator_command",
    "front_observation",
    "rear_observation",
)
ROLLOUT_SCALAR_DTYPES: dict[str, np.dtype[Any]] = {
    "teach_active_left": np.dtype(np.bool_),
    "teach_active_right": np.dtype(np.bool_),
    "control_source_left": np.dtype(np.uint8),
    "control_source_right": np.dtype(np.uint8),
    "is_intervention_left": np.dtype(np.bool_),
    "is_intervention_right": np.dtype(np.bool_),
    "intervention_id_left": np.dtype(np.uint64),
    "intervention_id_right": np.dtype(np.uint64),
    "sample_timestamp": np.dtype(np.float64),
    "frame_index": np.dtype(np.uint64),
}
ROLLOUT_VALIDITY_KEYS = frozenset(REQUIRED_TOPICS) | {
    "qpos",
    "qvel",
    "effort",
    "front_observation",
    "rear_observation",
    "policy_command_submitted",
    "coordinator_command",
    "action",
    "teach_active_left",
    "teach_active_right",
}


class Hdf5EpisodeWriter:
    """Write one frame at a time and publish only an entirely closed episode."""

    def __init__(
        self,
        data_root: Path | str,
        identity: EpisodeIdentity,
        max_timesteps: int,
        *,
        fps: float = 30.0,
        collector_version: str = COLLECTOR_VERSION,
        wall_clock: Any = time.time,
    ) -> None:
        if isinstance(max_timesteps, bool) or not isinstance(max_timesteps, int):
            raise TypeError("max_timesteps must be an integer")
        if max_timesteps <= 0:
            raise ValueError("max_timesteps must be positive")
        if not np.isfinite(fps) or fps <= 0:
            raise ValueError("fps must be finite and positive")

        self.identity = identity
        self.max_timesteps = max_timesteps
        self.fps = float(fps)
        self._wall_clock = wall_clock
        self._start_timestamp = float(wall_clock())
        self._count = 0
        self._failed = False
        self._failure_reason: str | None = None
        self._finalized = False
        self._aborted = False
        self._cancel_requested = Event()
        self._cancel_reason: str | None = None
        self._publication_lock = Lock()
        self._publication_status = "open"
        self._image_shapes: dict[str, tuple[int, ...]] | None = None

        root = Path(data_root).expanduser().resolve()
        parent = root if identity.storage_layout == "flat" else root / identity.task_id / identity.model_id / identity.dataset_round
        parent.mkdir(parents=True, exist_ok=True)
        parent = parent.resolve()
        try:
            parent.relative_to(root)
        except ValueError as error:
            raise ValueError("episode path escapes data_root") from error
        stem = f"episode_{identity.episode_index:06d}.hdf5"
        self.final_path = parent / stem
        self.incomplete_path = parent / f"{stem}.incomplete"
        if self.final_path.exists() or self.incomplete_path.exists():
            raise FileExistsError(f"episode path already exists: {self.final_path}")

        self._file: h5py.File | None = h5py.File(
            self.incomplete_path, "x", libver="latest"
        )
        self._set_initial_attributes(collector_version)
        self._flush_and_fsync()

    @property
    def frame_count(self) -> int:
        return self._count

    def request_cancel(self, reason: str) -> bool:
        """Publish cancel intent first, then try to confirm revocation."""
        self._cancel_reason = str(reason)
        self._cancel_requested.set()
        return self.try_revoke_publication()

    def try_revoke_publication(self) -> bool:
        """Confirm pending cancellation without waiting on an active commit."""
        if not self._publication_lock.acquire(blocking=False):
            return False
        try:
            if self._publication_status == "revoked":
                return True
            if self._finalized or self._publication_status in {
                "commit_in_progress",
                "committed",
                "failed",
            }:
                return False
            if not self._cancel_requested.is_set():
                return False
            self._publication_status = "revoked"
            return True
        finally:
            self._publication_lock.release()

    @property
    def publication_status(self) -> str:
        """Return the latest monotonic publication phase without blocking."""
        return self._publication_status

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise RuntimeError(f"writer_cancelled: {self._cancel_reason}")

    def _set_initial_attributes(self, collector_version: str) -> None:
        assert self._file is not None
        attrs = self._file.attrs
        attrs["project_id"] = PROJECT_ID
        attrs["collector_version"] = collector_version
        attrs["rollout_schema_version"] = ROLLOUT_SCHEMA_VERSION
        attrs["fps"] = self.fps
        attrs["DT"] = 1.0 / self.fps
        attrs["task_id"] = self.identity.task_id
        attrs["model_id"] = self.identity.model_id
        attrs["checkpoint_id"] = self.identity.checkpoint_id
        attrs["dataset_round"] = self.identity.dataset_round
        attrs["storage_layout"] = self.identity.storage_layout
        attrs["episode_index"] = self.identity.episode_index
        attrs["episode_uuid"] = str(self.identity.episode_uuid)
        attrs["start_timestamp"] = self._start_timestamp
        attrs["end_timestamp"] = np.nan
        attrs["completion_state"] = "recording"
        attrs["termination_reason"] = "unknown"

    def _create_dataset(
        self, path: str, tail_shape: tuple[int, ...], dtype: Any
    ) -> h5py.Dataset:
        assert self._file is not None
        shape = (self.max_timesteps, *tail_shape)
        chunk_length = 1 if tail_shape else min(self.max_timesteps, 256)
        chunks = (chunk_length, *tail_shape)
        return self._file.create_dataset(
            path,
            shape=shape,
            maxshape=shape,
            chunks=chunks,
            dtype=dtype,
        )

    def _initialize_datasets(self, frame: FrameSample) -> None:
        assert self._file is not None
        images = {
            "cam_high": frame.camera_high_rgb,
            "cam_left_wrist": frame.camera_left_rgb,
            "cam_right_wrist": frame.camera_right_rgb,
        }
        self._image_shapes = {name: value.shape for name, value in images.items()}
        for name, value in images.items():
            self._create_dataset(f"observations/images/{name}", value.shape, np.uint8)
        for name in ("qpos", "qvel", "effort"):
            self._create_dataset(f"observations/{name}", (14,), np.float32)
        self._create_dataset("action", (14,), np.float32)
        self._create_dataset("base_action", (2,), np.float32)
        for name in ROLLOUT_VECTOR_FIELDS:
            self._create_dataset(f"rollout/{name}", (14,), np.float32)
        for name, dtype in ROLLOUT_SCALAR_DTYPES.items():
            self._create_dataset(f"rollout/{name}", (), dtype)
        self._create_dataset(
            "rollout/handover_mode", (), h5py.string_dtype(encoding="utf-8")
        )
        self._create_dataset(
            "rollout/handover_fault", (), h5py.string_dtype(encoding="utf-8")
        )
        for key in REQUIRED_TOPICS:
            self._create_dataset(f"rollout/topic_timestamp/{key}", (), np.float64)
            self._create_dataset(f"rollout/arrival_timestamp/{key}", (), np.float64)
        for key in sorted(ROLLOUT_VALIDITY_KEYS):
            self._create_dataset(f"rollout/valid_mask/{key}", (), np.bool_)

    def append(self, frame: FrameSample) -> None:
        if self._finalized or self._aborted:
            raise RuntimeError("writer is closed")
        if self._failed:
            raise RuntimeError("writer has failed")
        if frame.identity != self.identity:
            raise ValueError("frame identity does not match episode identity")
        if self._count >= self.max_timesteps:
            raise ValueError("max_timesteps exceeded")
        if frame.frame_index != self._count:
            raise ValueError(
                f"frame_index must be contiguous: expected {self._count}, "
                f"got {frame.frame_index}"
            )
        try:
            self._raise_if_cancelled()
            if self._count == 0:
                self._initialize_datasets(frame)
            self._validate_frame(frame)
            self._write_frame(frame)
            self._raise_if_cancelled()
            self._count += 1
        except Exception as error:
            self._seal_failed(str(error))
            raise

    def _validate_frame(self, frame: FrameSample) -> None:
        expected = self._image_shapes
        assert expected is not None
        images = {
            "cam_high": frame.camera_high_rgb,
            "cam_left_wrist": frame.camera_left_rgb,
            "cam_right_wrist": frame.camera_right_rgb,
        }
        actual = {name: value.shape for name, value in images.items()}
        for name, shape in actual.items():
            validity_key = IMAGE_VALIDITY_KEYS[name]
            if frame.valid_mask.get(validity_key, False) and shape != expected[name]:
                raise ValueError(
                    f"camera shapes changed: expected {expected}, got {actual}"
                )
        if frame.valid_mask.get("coordinator_command", False) and not np.array_equal(
            frame.action, frame.coordinator_command, equal_nan=True
        ):
            raise ValueError("action must equal coordinator_command when valid")
        unknown_source = set(frame.source_timestamps) - set(REQUIRED_TOPICS)
        unknown_arrival = set(frame.arrival_timestamps) - set(REQUIRED_TOPICS)
        unknown_validity = set(frame.valid_mask) - ROLLOUT_VALIDITY_KEYS
        if unknown_source or unknown_arrival or unknown_validity:
            raise ValueError("frame contains unknown timestamp or validity keys")

    def _write_frame(self, frame: FrameSample) -> None:
        assert self._file is not None
        assert self._image_shapes is not None
        index = self._count
        images = {
            "cam_high": frame.camera_high_rgb,
            "cam_left_wrist": frame.camera_left_rgb,
            "cam_right_wrist": frame.camera_right_rgb,
        }
        normalized_images = {
            name: (
                value
                if frame.valid_mask.get(IMAGE_VALIDITY_KEYS[name], False)
                else np.zeros(self._image_shapes[name], dtype=np.uint8)
            )
            for name, value in images.items()
        }
        values = {
            "observations/images/cam_high": normalized_images["cam_high"],
            "observations/images/cam_left_wrist": normalized_images[
                "cam_left_wrist"
            ],
            "observations/images/cam_right_wrist": normalized_images[
                "cam_right_wrist"
            ],
            "observations/qpos": frame.qpos,
            "observations/qvel": frame.qvel,
            "observations/effort": frame.effort,
            "action": frame.action,
            "base_action": frame.base_action,
        }
        for path, value in values.items():
            self._file[path][index] = value
        for name in ROLLOUT_VECTOR_FIELDS:
            self._file[f"rollout/{name}"][index] = getattr(frame, name)
        for name in ROLLOUT_SCALAR_DTYPES:
            value = getattr(frame, name)
            if name.startswith("control_source_"):
                value = int(value)
            self._file[f"rollout/{name}"][index] = value
        self._file["rollout/handover_mode"][index] = frame.handover_mode
        self._file["rollout/handover_fault"][index] = frame.handover_fault
        for key in REQUIRED_TOPICS:
            self._file[f"rollout/topic_timestamp/{key}"][index] = (
                frame.source_timestamps.get(key, np.nan)
            )
            self._file[f"rollout/arrival_timestamp/{key}"][index] = (
                frame.arrival_timestamps.get(key, np.nan)
            )
        for key in ROLLOUT_VALIDITY_KEYS:
            self._file[f"rollout/valid_mask/{key}"][index] = frame.valid_mask.get(
                key, False
            )

    def _resize_temporal_datasets(self) -> None:
        assert self._file is not None

        def resize(_name: str, value: h5py.Dataset | h5py.Group) -> None:
            if isinstance(value, h5py.Dataset):
                value.resize((self._count, *value.shape[1:]))

        self._file.visititems(resize)

    def _flush_and_fsync(self) -> None:
        if self._file is None:
            return
        self._file.flush()
        handle = self._file.id.get_vfd_handle()
        if isinstance(handle, tuple):
            handle = handle[0]
        if isinstance(handle, int):
            os.fsync(handle)

    def _close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _seal_failed(self, reason: str) -> None:
        self._failed = True
        self._failure_reason = reason
        if self._file is None and self.incomplete_path.exists():
            self._file = h5py.File(self.incomplete_path, "r+", libver="latest")
        if self._file is not None:
            try:
                self._file.attrs["completion_state"] = "error"
                self._file.attrs["failure_reason"] = reason
                self._file.attrs["end_timestamp"] = float(self._wall_clock())
                self._flush_and_fsync()
            finally:
                self._close()

    def _fsync_parent_directory(self) -> None:
        directory_fd = os.open(self.final_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _commit_publication(self) -> None:
        """Use rename as the sole visibility linearization point.

        The parent directory is fsynced before this call. A crash after rename may
        recover either name because no post-rename fsync is attempted, but any
        visible final path references the fully flushed and closed HDF5 inode.
        """
        with self._publication_lock:
            self._publication_status = "commit_in_progress"
            if self._cancel_requested.is_set():
                self._publication_status = "revoked"
                self._raise_if_cancelled()
            try:
                os.replace(self.incomplete_path, self.final_path)
            except Exception:
                self._publication_status = "failed"
                raise
            self._finalized = True
            self._publication_status = "committed"

    def finalize(
        self,
        summary: Mapping[str, object] | None = None,
        *,
        completion_state: str = "complete",
        end_timestamp: float | None = None,
    ) -> Path:
        summary = dict(summary or {})
        unsupported = set(summary) - SUMMARY_ATTRIBUTE_ALLOWLIST
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"unsupported summary attribute(s): {names}")
        termination_reason = summary.get("termination_reason")
        if "termination_reason" in summary and (
            not isinstance(termination_reason, str) or not termination_reason
        ):
            raise TypeError(
                "termination_reason summary attribute must be a non-empty str"
            )
        if completion_state not in {"complete", "aborted"}:
            raise ValueError("completion_state must be complete or aborted")
        if end_timestamp is None:
            end_timestamp = float(self._wall_clock())
        if not np.isfinite(end_timestamp):
            raise ValueError("end_timestamp must be finite")
        if self._finalized:
            return self.final_path
        if self._failed:
            raise RuntimeError(f"writer failed: {self._failure_reason}")
        if self._aborted:
            raise RuntimeError("aborted writer cannot be finalized")
        self._publication_status = "finalizing"
        try:
            assert self._file is not None
            self._raise_if_cancelled()
            if self._count == 0:
                raise ValueError("cannot finalize an episode with zero frames")
            self._resize_temporal_datasets()
            self._file.attrs["completion_state"] = completion_state
            self._file.attrs["end_timestamp"] = float(end_timestamp)
            for key, value in summary.items():
                if value is not None and isinstance(value, (str, bool, int, float)):
                    self._file.attrs[key] = value
            self._flush_and_fsync()
            self._close()
            self._fsync_parent_directory()
            self._commit_publication()
            return self.final_path
        except Exception as error:
            if self._publication_status in {"open", "finalizing"}:
                self._publication_status = (
                    "revoked" if self._cancel_requested.is_set() else "failed"
                )
            self._seal_failed(str(error))
            raise

    def abort(self, reason: str) -> Path:
        """Close safely without publishing; the incomplete file is forensic evidence."""
        if self._finalized:
            raise RuntimeError("finalized writer cannot be aborted")
        if self._aborted:
            return self.incomplete_path
        if self._failed:
            self._publication_status = "revoked"
            return self.incomplete_path
        self._aborted = True
        self._publication_status = "revoked"
        if self._file is not None:
            try:
                self._file.attrs["completion_state"] = "error"
                self._file.attrs["failure_reason"] = str(reason)
                self._file.attrs["end_timestamp"] = float(self._wall_clock())
                self._flush_and_fsync()
            finally:
                self._close()
        return self.incomplete_path

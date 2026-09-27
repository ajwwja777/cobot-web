"""Strict schema detection and validation for immutable source HDF5 files."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

import h5py
import numpy as np

from capture_core.hdf5_writer import (
    COLLECTOR_VERSION,
    IMAGE_DATASET_NAMES,
    PROJECT_ID,
    ROLLOUT_SCALAR_DTYPES,
    ROLLOUT_VALIDITY_KEYS,
    ROLLOUT_VECTOR_FIELDS,
)
from capture_core.schema import ControlSource
from capture_core.topics import REQUIRED_TOPICS

from .legacy_hdf5 import LegacyHdf5Source
from .rollout_v1_hdf5 import RolloutV1Hdf5Source
from .source_types import EpisodeMetadata, EpisodeSource

SourceKind = Literal["legacy", "rollout_v1"]


class SourceValidationError(ValueError):
    """The input is not an unambiguous supported episode schema."""


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def detect_source(path: Path | str) -> SourceKind:
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file() or source_path.is_symlink():
        raise SourceValidationError("source must be a regular HDF5 file")
    try:
        with h5py.File(source_path, "r") as handle:
            has_rollout = "rollout" in handle
            version = handle.attrs.get("rollout_schema_version")
            if has_rollout and version == 1:
                return "rollout_v1"
            if has_rollout or version is not None:
                raise SourceValidationError("ambiguous or unsupported rollout schema")
            if (
                _text(handle.attrs.get("collector", "")) == "cobot-station"
                and _text(handle.attrs.get("collector_mode", "")) == "ros_readonly"
            ):
                return "legacy"
    except OSError as error:
        raise SourceValidationError("source is not a readable HDF5 file") from error
    raise SourceValidationError("ambiguous HDF5 source schema")


def _dataset(
    handle: h5py.File, path: str, shape: tuple[int, ...], dtype: np.dtype[object]
) -> None:
    try:
        value = handle[path]
    except KeyError as error:
        raise SourceValidationError(f"missing dataset: {path}") from error
    if isinstance(value, h5py.Dataset) and value.shape and value.shape[0] != shape[0]:
        raise SourceValidationError(f"temporal length mismatch: {path}")
    if (
        not isinstance(value, h5py.Dataset)
        or value.shape != shape
        or value.dtype != dtype
    ):
        raise SourceValidationError(f"invalid dataset {path}: expected {shape} {dtype}")


def _validate_standard(handle: h5py.File) -> tuple[int, float, float]:
    try:
        action = handle["action"]
        fps = float(handle.attrs["fps"])
        dt = float(handle.attrs["DT"])
    except (KeyError, TypeError, ValueError) as error:
        raise SourceValidationError(
            "missing or invalid standard metadata/action"
        ) from error
    if action.ndim != 2 or action.shape[1:] != (14,) or action.dtype != np.float32:
        raise SourceValidationError(
            "invalid action shape or dtype; expected [T,14] float32"
        )
    frames = int(action.shape[0])
    if frames <= 0 or not math.isfinite(fps) or fps <= 0:
        raise SourceValidationError("invalid frame count or fps")
    if not math.isfinite(dt) or dt <= 0 or not math.isclose(dt, 1 / fps, rel_tol=1e-6):
        raise SourceValidationError("DT and fps are inconsistent")
    for path, tail, dtype in (
        ("observations/qpos", (14,), np.dtype(np.float32)),
        ("observations/qvel", (14,), np.dtype(np.float32)),
        ("observations/effort", (14,), np.dtype(np.float32)),
        ("base_action", (2,), np.dtype(np.float32)),
    ):
        _dataset(handle, path, (frames, *tail), dtype)
    for camera in IMAGE_DATASET_NAMES:
        try:
            image = handle[f"observations/images/{camera}"]
        except KeyError as error:
            raise SourceValidationError(f"missing camera: {camera}") from error
        if (
            image.dtype != np.uint8
            or image.ndim != 4
            or image.shape[0] != frames
            or image.shape[-1] != 3
            or min(image.shape[1:3]) <= 0
        ):
            raise SourceValidationError(f"invalid camera dataset: {camera}")
    # The exact length check above prevents silently truncating any standard stream.
    return frames, fps, dt


def _validate_legacy(path: Path) -> LegacyHdf5Source:
    with h5py.File(path, "r") as handle:
        frames, fps, dt = _validate_standard(handle)
    return LegacyHdf5Source(
        path,
        EpisodeMetadata(path, "legacy_cobot_hdf5", frames, fps, dt),
    )


def _require_text_vector(handle: h5py.File, name: str, frames: int) -> None:
    try:
        value = handle[f"rollout/{name}"]
    except KeyError as error:
        raise SourceValidationError(f"missing rollout text: {name}") from error
    info = h5py.check_string_dtype(value.dtype)
    if value.shape != (frames,) or info is None or info.encoding != "utf-8":
        raise SourceValidationError(f"invalid rollout text: {name}")


def _validate_rollout(path: Path) -> RolloutV1Hdf5Source:
    with h5py.File(path, "r") as handle:
        frames, fps, dt = _validate_standard(handle)
        attrs = handle.attrs
        if _text(attrs.get("project_id", "")) != PROJECT_ID:
            raise SourceValidationError("invalid rollout project_id")
        if _text(attrs.get("collector_version", "")) != COLLECTOR_VERSION:
            raise SourceValidationError("invalid rollout collector_version")
        if _text(attrs.get("completion_state", "")) != "complete":
            raise SourceValidationError("rollout source must be complete")
        required_attrs = (
            "task_id",
            "model_id",
            "checkpoint_id",
            "dataset_round",
            "episode_index",
            "episode_uuid",
            "start_timestamp",
            "end_timestamp",
            "termination_reason",
        )
        if any(name not in attrs for name in required_attrs):
            raise SourceValidationError(
                "missing rollout identity or finalization attribute"
            )
        for name in ROLLOUT_VECTOR_FIELDS:
            _dataset(
                handle,
                f"rollout/{name}",
                (frames, 14),
                np.dtype(np.float32),
            )
        for name, dtype in ROLLOUT_SCALAR_DTYPES.items():
            _dataset(handle, f"rollout/{name}", (frames,), dtype)
        _require_text_vector(handle, "handover_mode", frames)
        _require_text_vector(handle, "handover_fault", frames)
        for key in REQUIRED_TOPICS:
            _dataset(
                handle,
                f"rollout/topic_timestamp/{key}",
                (frames,),
                np.dtype(np.float64),
            )
            _dataset(
                handle,
                f"rollout/arrival_timestamp/{key}",
                (frames,),
                np.dtype(np.float64),
            )
        for key in ROLLOUT_VALIDITY_KEYS:
            _dataset(
                handle,
                f"rollout/valid_mask/{key}",
                (frames,),
                np.dtype(np.bool_),
            )
        timestamps = np.asarray(handle["rollout/sample_timestamp"][:])
        if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) < 0):
            raise SourceValidationError(
                "sample timestamps must be finite and monotonic"
            )
        indices = np.asarray(handle["rollout/frame_index"][:])
        if not np.array_equal(indices, np.arange(frames, dtype=np.uint64)):
            raise SourceValidationError("frame indices must be contiguous")
        for side in ("left", "right"):
            source = np.asarray(handle[f"rollout/control_source_{side}"][:])
            if not np.isin(source, [int(value) for value in ControlSource]).all():
                raise SourceValidationError("invalid control source")
            intervention = np.asarray(handle[f"rollout/is_intervention_{side}"][:])
            if not np.array_equal(intervention, source == int(ControlSource.HUMAN)):
                raise SourceValidationError("intervention flag does not match source")
        metadata = EpisodeMetadata(
            path=path,
            source_schema="task5_rollout_hdf5_v1",
            frame_count=frames,
            fps=fps,
            dt=dt,
            task_id=_text(attrs["task_id"]),
            model_id=_text(attrs["model_id"]),
            checkpoint_id=_text(attrs["checkpoint_id"]),
            dataset_round=_text(attrs["dataset_round"]),
            episode_index=int(attrs["episode_index"]),
            episode_uuid=_text(attrs["episode_uuid"]),
            termination_reason=_text(attrs["termination_reason"]),
        )
    return RolloutV1Hdf5Source(path, metadata)


def open_source(path: Path | str) -> EpisodeSource:
    source_path = Path(path).expanduser().resolve()
    try:
        kind = detect_source(source_path)
        return (
            _validate_legacy(source_path)
            if kind == "legacy"
            else _validate_rollout(source_path)
        )
    except SourceValidationError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise SourceValidationError(str(error)) from error

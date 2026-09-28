"""Recorder preflight and runtime safety gates."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .ros_cache import CacheSnapshot
from .asset_storage import require_storage, migrated_path

PREFLIGHT_STREAMS = (
    "camera_high",
    "camera_left",
    "camera_right",
    "front_left",
    "front_right",
    "handover_mode",
)


class PreflightError(RuntimeError):
    """A named, operator-actionable reason recording could not start."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        message = code if detail is None else f"{code}: {detail}"
        super().__init__(message)


def _verify_payload(snapshot: CacheSnapshot, key: str) -> bool:
    value = snapshot.get(key)
    if key.startswith("camera_"):
        return (
            isinstance(value, np.ndarray)
            and value.ndim == 3
            and value.shape[-1] == 3
            and value.dtype == np.uint8
        )
    if key.startswith("front_"):
        position = (
            value.get("position")
            if isinstance(value, dict)
            else getattr(value, "position", value)
        )
        try:
            return np.asarray(position).reshape(-1).size == 7
        except (TypeError, ValueError):
            return False
    if key == "handover_mode":
        data = (
            value.get("data")
            if isinstance(value, dict)
            else getattr(value, "data", value)
        )
        return isinstance(data, str) and bool(data)
    return True


def _prepare_writable_directory(data_root: Path | str) -> Path:
    path = Path(migrated_path(data_root)).expanduser()
    try:
        require_storage(path, write=True)
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise NotADirectoryError(path)
        with tempfile.NamedTemporaryFile(
            prefix=".task5-write-probe-", dir=path
        ) as probe:
            probe.write(b"task5")
            probe.flush()
            os.fsync(probe.fileno())
    except OSError as error:
        raise PreflightError("not_writable", str(path) + ": " + str(error)) from error
    return path.resolve()


def validate_preflight(
    data_root: Path | str,
    snapshot: CacheSnapshot,
    *,
    min_free_disk_bytes: int = 20 * 1024**3,
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
) -> Path:
    """Validate only streams required before acquisition may safely begin."""
    if min_free_disk_bytes <= 0:
        raise ValueError("min_free_disk_bytes must be positive")
    path = _prepare_writable_directory(data_root)
    try:
        free = int(disk_usage(path).free)
    except (OSError, TypeError, ValueError, AttributeError) as error:
        raise PreflightError("disk_check_failed", str(error)) from error
    if free < min_free_disk_bytes:
        raise PreflightError(
            "disk_space_low", f"free={free}, required={min_free_disk_bytes}"
        )
    invalid = []
    for key in PREFLIGHT_STREAMS:
        available = (
            snapshot.has_value(key)
            if key == "handover_mode"
            else snapshot.is_fresh(key)
        )
        if not available or not _verify_payload(snapshot, key):
            invalid.append(key)
    if invalid:
        raise PreflightError("streams_not_ready", ",".join(invalid))
    return path


def is_disk_below_threshold(
    path: Path,
    threshold_bytes: int,
    *,
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
) -> bool:
    """Return whether acquisition must stop; disk inspection failures are fatal."""
    if threshold_bytes <= 0:
        raise ValueError("threshold_bytes must be positive")
    usage = disk_usage(path)
    return int(usage.free) <= threshold_bytes

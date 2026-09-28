"""Safe preparation and index allocation for one rollout episode series."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from .deletion import DeletionError, read_deletion_records
from .asset_storage import require_storage, migrated_path
from .schema import IDENTIFIER_RE

_EPISODE_FILE_RE = re.compile(
    r"^episode_(?P<index>[0-9]{6})\.hdf5(?:\.incomplete)?$"
)
_PRESERVED_EPISODE_RE = re.compile(r"^episode_(?P<index>[0-9]{6})\.(?:rlt|labels|failed)\.json$")
_MAX_EPISODE_INDEX = 999999


class StoragePathError(ValueError):
    """The requested storage location cannot be used safely."""


@dataclass(frozen=True)
class SeriesIdentity:
    """Path-safe identity for a task/model/dataset-round series."""

    task_id: str
    model_id: str
    dataset_round: str
    storage_layout: str = "legacy"

    def __post_init__(self) -> None:
        if self.storage_layout not in {"legacy", "flat"}:
            raise ValueError("invalid storage_layout")
        for name in ("task_id", "model_id", "dataset_round"):
            value = getattr(self, name)
            if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be a path-safe identifier")


@dataclass(frozen=True)
class PreparedSeries:
    """Resolved series directory and the next collision-free numeric index."""

    data_root: Path
    episode_directory: Path
    next_episode_index: int
    existing_indices: Tuple[int, ...]
    deleted_indices: Tuple[int, ...]


def _absolute_expanded_path(value: Path | str) -> Path:
    try:
        path = Path(migrated_path(value)).expanduser()
    except (TypeError, ValueError, RuntimeError) as error:
        raise StoragePathError("data_root is invalid") from error
    if not path.is_absolute():
        raise StoragePathError("data_root must be absolute or start with ~")
    return path


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            if current.is_symlink():
                raise StoragePathError(
                    f"data_root contains a symlink component: {current}"
                )
        except OSError as error:
            raise StoragePathError(
                f"could not inspect data_root component: {current}"
            ) from error


def prepare_series(
    data_root: Path | str,
    series: SeriesIdentity,
    *,
    create: bool = True,
    reuse_deleted_indices: bool = True,
) -> PreparedSeries:
    """Validate/create one series directory and allocate its next index."""
    if not isinstance(series, SeriesIdentity):
        raise TypeError("series must be SeriesIdentity")
    root = _absolute_expanded_path(data_root)
    try:
        require_storage(root, write=create)
    except OSError as error:
        raise StoragePathError(str(error)) from error
    parent = root if series.storage_layout == "flat" else root / series.task_id / series.model_id / series.dataset_round
    _reject_symlink_components(parent)
    try:
        if create:
            parent.mkdir(parents=True, exist_ok=True)
        if not root.is_dir() or not parent.is_dir():
            raise StoragePathError("data_root and episode path must be directories")
        resolved_root = root.resolve(strict=True)
        resolved_parent = parent.resolve(strict=True)
        resolved_parent.relative_to(resolved_root)
    except StoragePathError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise StoragePathError("could not prepare storage directory") from error

    indices = []
    preserved_indices = []
    try:
        for candidate in resolved_parent.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                continue
            match = _EPISODE_FILE_RE.fullmatch(candidate.name)
            if match is not None:
                indices.append(int(match.group("index")))
            preserved = _PRESERVED_EPISODE_RE.fullmatch(candidate.name)
            if preserved is not None:
                preserved_indices.append(int(preserved.group("index")))
    except OSError as error:
        raise StoragePathError("could not scan episode directory") from error
    existing = tuple(sorted(set(indices)))
    try:
        deleted = tuple(
            record.episode_index for record in read_deletion_records(resolved_parent)
        )
    except DeletionError as error:
        raise StoragePathError("could not read deletion ledger") from error
    allocated = tuple(set(existing + tuple(preserved_indices)))
    if not reuse_deleted_indices:allocated = tuple(set(allocated + deleted))
    if allocated and max(allocated) >= _MAX_EPISODE_INDEX:
        raise StoragePathError("episode index space is exhausted")
    next_index = 1 if not allocated else max(allocated) + 1
    return PreparedSeries(
        data_root=resolved_root,
        episode_directory=resolved_parent,
        next_episode_index=next_index,
        existing_indices=existing,
        deleted_indices=deleted,
    )

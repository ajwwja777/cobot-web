"""Permanent episode deletion with an append-only identity audit ledger."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Callable, Iterable, Tuple
from uuid import UUID

import h5py

_EPISODE_RE = re.compile(r"^episode_(?P<index>[0-9]{6})\.hdf5$")
_LEDGER_FIELDS = frozenset({"episode_uuid", "episode_index", "deleted_at"})
_LOCKS_GUARD = Lock()
_LOCKS: dict[Path, RLock] = {}


class DeletionError(RuntimeError):
    """A permanent-delete request could not be completed safely."""


@dataclass(frozen=True)
class DeletionRecord:
    episode_uuid: str
    episode_index: int
    deleted_at: str


def _root_lock(series: Path) -> RLock:
    resolved = series.resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, RLock())


def _parse_utc(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("deleted_at must be text")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("deleted_at must be UTC")
    return value


def _parse_record(value: object) -> DeletionRecord:
    if not isinstance(value, dict) or set(value) != _LEDGER_FIELDS:
        raise ValueError("invalid deletion record fields")
    episode_uuid = str(UUID(str(value["episode_uuid"])))
    if episode_uuid != value["episode_uuid"]:
        raise ValueError("episode_uuid must be canonical")
    index = value["episode_index"]
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("episode_index must be non-negative integer")
    return DeletionRecord(episode_uuid, index, _parse_utc(value["deleted_at"]))


def _task5_directory(series: Path, *, create: bool = False) -> Path:
    task5 = series / ".task5"
    if task5.is_symlink():
        raise DeletionError("deletion ledger directory is a symlink")
    if create:
        try:
            task5.mkdir(parents=False, exist_ok=True)
        except OSError as error:
            raise DeletionError("could not create deletion ledger directory") from error
    if task5.exists() and not task5.is_dir():
        raise DeletionError("deletion ledger path is not a directory")
    return task5


def read_deletion_records(series_directory: Path | str) -> Tuple[DeletionRecord, ...]:
    """Strictly read deleted UUID/index identities for audit."""
    series = Path(series_directory).expanduser()
    if series.is_symlink():
        raise DeletionError("series directory is a symlink")
    task5 = _task5_directory(series)
    ledger = task5 / "deletions.jsonl"
    if not ledger.exists():
        return ()
    if ledger.is_symlink() or not ledger.is_file():
        raise DeletionError("deletion ledger is unsafe")
    try:
        payload = ledger.read_text(encoding="utf-8")
        if payload and not payload.endswith("\n"):
            raise ValueError("truncated deletion ledger")
        records = [
            _parse_record(json.loads(line))
            for line in payload.splitlines()
            if line.strip()
        ]
        by_uuid: dict[str, int] = {}
        for record in records:
            if (
                record.episode_uuid in by_uuid
                and by_uuid[record.episode_uuid] != record.episode_index
            ):
                raise ValueError("deletion ledger identity conflict")
            by_uuid[record.episode_uuid] = record.episode_index
        unique = {
            (record.episode_uuid, record.episode_index): record for record in records
        }
        return tuple(sorted(unique.values(), key=lambda item: item.episode_index))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise DeletionError(f"invalid deletion ledger: {error}") from error


def _append_record(series: Path, record: DeletionRecord) -> None:
    task5 = _task5_directory(series, create=True)
    ledger = task5 / "deletions.jsonl"
    if ledger.is_symlink():
        raise DeletionError("deletion ledger is a symlink")
    current = read_deletion_records(series)
    if any(item.episode_uuid == record.episode_uuid for item in current):
        raise DeletionError("deletion ledger identity conflict")
    line = json.dumps(
        {
            "episode_uuid": record.episode_uuid,
            "episode_index": record.episode_index,
            "deleted_at": record.deleted_at,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise DeletionError("could not append deletion ledger") from error


def _validated_episode(path: Path, expected_uuid: str) -> Tuple[Path, int]:
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise DeletionError("episode path is unsafe")
    match = _EPISODE_RE.fullmatch(path.name)
    if match is None:
        raise DeletionError("episode filename is invalid")
    index = int(match.group("index"))
    try:
        with h5py.File(path, "r") as episode:
            actual_uuid = str(episode.attrs["episode_uuid"])
            actual_index = int(episode.attrs["episode_index"])
            completion = str(episode.attrs["completion_state"])
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise DeletionError("episode identity could not be validated") from error
    if (
        actual_uuid != expected_uuid
        or actual_index != index
        or completion != "complete"
    ):
        raise DeletionError("episode identity does not match deletion request")
    return path.parent, index


def _validated_empty_incomplete_episode(
    path: Path, expected_episode: Path, expected_uuid: str
) -> Tuple[Path, int]:
    """Validate the recorder artifact produced when an empty episode is stopped."""
    match = _EPISODE_RE.fullmatch(expected_episode.name)
    if match is None:
        raise DeletionError("episode filename is invalid")
    index = int(match.group("index"))
    if (
        path != Path(str(expected_episode) + ".incomplete")
        or path.is_symlink()
        or not path.is_file()
        or path.resolve() != path
    ):
        raise DeletionError("incomplete episode path is unsafe")
    try:
        with h5py.File(path, "r") as episode:
            actual_uuid = str(episode.attrs["episode_uuid"])
            actual_index = int(episode.attrs["episode_index"])
            completion = str(episode.attrs["completion_state"])
            failure_reason = str(episode.attrs["failure_reason"])
            is_empty = len(episode) == 0
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise DeletionError("incomplete episode identity could not be validated") from error
    if (
        actual_uuid != expected_uuid
        or actual_index != index
        or completion != "error"
        or failure_reason != "empty_episode"
        or not is_empty
    ):
        raise DeletionError("incomplete episode does not match empty deletion request")
    return expected_episode.parent, index


def permanently_delete_episode(
    episode_path: Path | str,
    sidecar_path: Path | str,
    preview_directory: Path | str,
    *,
    expected_uuid: str,
    incomplete_episode_path: Path | str | None = None,
    extra_artifacts: Iterable[Path | str] = (),
    record_deletion: bool = True,
    replace: Callable[[Path, Path], None] = lambda source, target: source.replace(
        target
    ),
) -> DeletionRecord:
    """Remove validated artifacts; operator discard opts out of the ledger."""
    try:
        canonical_uuid = str(UUID(expected_uuid))
    except (AttributeError, TypeError, ValueError) as error:
        raise DeletionError("expected_uuid is invalid") from error
    expected_episode = Path(episode_path).expanduser()
    episode = expected_episode
    sidecar = Path(sidecar_path).expanduser()
    preview = Path(preview_directory).expanduser()
    if expected_episode.exists():
        series, index = _validated_episode(expected_episode, canonical_uuid)
    elif incomplete_episode_path is not None:
        episode = Path(incomplete_episode_path).expanduser()
        series, index = _validated_empty_incomplete_episode(
            episode, expected_episode, canonical_uuid
        )
    else:
        series, index = _validated_episode(expected_episode, canonical_uuid)
    expected_sidecar = expected_episode.with_suffix(".labels.json")
    expected_preview = series / ".previews" / canonical_uuid
    if sidecar != expected_sidecar or preview != expected_preview:
        raise DeletionError("artifact paths do not match episode identity")
    if sidecar.is_symlink() or (sidecar.exists() and not sidecar.is_file()):
        raise DeletionError("label artifact is unsafe")
    if preview.is_symlink() or (preview.exists() and not preview.is_dir()):
        raise DeletionError("preview artifact is unsafe")
    extras = []
    for value in extra_artifacts:
        extra = Path(value).expanduser()
        if (
            extra.name != canonical_uuid
            or extra.is_symlink()
            or not extra.exists()
            or extra.resolve() != extra
        ):
            raise DeletionError("extra artifact is unsafe")
        extras.append(extra)

    with _root_lock(series):
        task5 = _task5_directory(series, create=True)
        deleting = task5 / "deleting"
        if deleting.is_symlink():
            raise DeletionError("deletion staging directory is a symlink")
        deleting.mkdir(exist_ok=True)
        staging = deleting / canonical_uuid
        if staging.exists() or staging.is_symlink():
            raise DeletionError("deletion staging conflict")
        staging.mkdir()
        artifacts = [
            (episode, staging / episode.name),
            (sidecar, staging / sidecar.name),
            (preview, staging / "preview"),
        ]
        artifacts.extend(
            (source, staging / f"extra-{index:04d}")
            for index, source in enumerate(extras, start=1)
        )
        moved: list[tuple[Path, Path]] = []
        try:
            for source, target in artifacts:
                if not source.exists():
                    continue
                replace(source, target)
                moved.append((target, source))
        except OSError as error:
            for target, source in reversed(moved):
                try:
                    target.replace(source)
                except OSError:
                    pass
            shutil.rmtree(staging, ignore_errors=True)
            raise DeletionError("could not stage episode deletion") from error

        record = DeletionRecord(
            canonical_uuid,
            index,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        try:
            if record_deletion:
                _append_record(series, record)
        except DeletionError:
            for target, source in reversed(moved):
                try:
                    target.replace(source)
                except OSError:
                    pass
            shutil.rmtree(staging, ignore_errors=True)
            raise
        # Do not report success while a discarded episode remains on disk.
        try:
            shutil.rmtree(staging)
        except OSError as error:
            raise DeletionError("could not remove staged episode artifacts") from error
        return record

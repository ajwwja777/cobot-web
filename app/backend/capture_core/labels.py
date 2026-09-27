"""Atomic JSON labels for finalized, immutable rollout episodes."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from uuid import UUID

import h5py
import numpy as np

from .control_source import parse_handover_mode
from .hdf5_writer import (
    COLLECTOR_VERSION,
    IMAGE_DATASET_NAMES,
    PROJECT_ID,
    ROLLOUT_SCALAR_DTYPES,
    ROLLOUT_SCHEMA_VERSION,
    ROLLOUT_VALIDITY_KEYS,
    ROLLOUT_VECTOR_FIELDS,
    STANDARD_VECTOR_DATASETS,
)
from .schema import IDENTIFIER_RE, ControlSource, EpisodeLabels
from .topics import REQUIRED_TOPICS

LABEL_SCHEMA_VERSION = 1
_EPISODE_NAME = re.compile(r"^episode_(\d{6})\.hdf5$")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "label_schema_version",
        "episode_uuid",
        "episode_outcome",
        "episode_quality",
        "last_completed_stage",
        "failure_stage",
        "failure_type",
        "termination_reason",
        "keep_for_training",
        "operator_note",
        "interventions",
        "operator_nodes",
        "label_updated_at",
    }
)
_UPDATE_FIELDS = _TOP_LEVEL_FIELDS - {"label_updated_at"}
_EDITABLE_LABEL_FIELDS = frozenset(
    {
        "episode_outcome",
        "episode_quality",
        "last_completed_stage",
        "failure_stage",
        "failure_type",
        "termination_reason",
        "keep_for_training",
        "operator_note",
    }
)
_INTERVENTION_IDENTITY_FIELDS = frozenset({"side", "intervention_id"})
_INTERVENTION_READ_ONLY_FIELDS = frozenset(
    {
        "start_frame",
        "end_frame",
        "start_timestamp",
        "end_timestamp",
        "handover_mode",
    }
)
_INTERVENTION_EDITABLE_FIELDS = frozenset({"reason", "outcome", "quality", "note"})
_INTERVENTION_REASONS = frozenset(
    {"preventive", "corrective", "recovery", "safety", "efficiency", "unknown"}
)
_INTERVENTION_OUTCOMES = frozenset(
    {"recovered", "not_recovered", "aborted", "uncertain"}
)
_INTERVENTION_QUALITIES = frozenset({"good", "bad", "uncertain"})
_ROOT_LOCKS_GUARD = Lock()
_ROOT_LOCKS: dict[Path, RLock] = {}


class LabelStoreError(RuntimeError):
    """Base class for errors safe to translate at the HTTP boundary."""


class LabelNotFoundError(LabelStoreError):
    """The requested UUID does not identify a validated complete episode."""


class LabelConflictError(LabelStoreError):
    """The update conflicts with immutable episode identity."""


class LabelValidationError(LabelStoreError, ValueError):
    """A label update does not satisfy the public sidecar contract."""


@dataclass(frozen=True)
class _EpisodeRecord:
    path: Path
    episode_uuid: UUID
    task_id: str
    model_id: str
    checkpoint_id: str
    dataset_round: str
    recorded_dataset_round: str
    episode_index: int
    frame_count: int
    interventions: tuple[dict[str, object], ...]
    intervention_phases: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class EpisodeArtifacts:
    """Validated internal paths for a finalized episode and its derived files."""

    episode_uuid: UUID
    episode_index: int
    episode_path: Path
    sidecar_path: Path
    preview_directory: Path
    frame_count: int
    size_bytes: int


def labels_are_complete(payload: Mapping[str, object]) -> bool:
    """Return whether the minimum operator decisions are final."""
    outcome = payload.get("episode_outcome")
    quality = payload.get("episode_quality")
    keep = payload.get("keep_for_training")
    if outcome == "unknown" and keep == "false" and payload.get("termination_reason") == "operator_save":
        return True  # Deliberately saved for review, never a training label.
    if outcome not in {"success", "failure", "aborted"}:
        return False
    return quality in {"good", "bad"} and keep in {"true", "false"}


def _text_attribute(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise ValueError("attribute is not text")


def _timestamp(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _strict_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError("attribute is not an integer")
    return int(value)


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int, np.number)):
        raise TypeError("attribute is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("attribute is not finite")
    return result


def _utc_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("timestamp is not text")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp must be UTC ISO-8601")
    return value


class LabelStore:
    """Discover complete episodes and update their JSON sidecars atomically.

    Every HDF5 handle in this class is opened with mode ``r``.  Mutable state is
    confined to the adjacent ``.labels.json`` file.
    """

    def __init__(self, data_root: Path | str) -> None:
        root = Path(data_root).expanduser()
        self._root = root.resolve()
        with _ROOT_LOCKS_GUARD:
            self._lock = _ROOT_LOCKS.setdefault(self._root, RLock())

    @property
    def data_root(self) -> Path:
        """Return the immutable root used to discover episode files."""
        return self._root

    def _is_safe_regular_file(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self._root)
        except ValueError:
            return False
        current = self._root
        try:
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    return False
            return path.is_file() and path.resolve() == path
        except OSError:
            return False

    def _read_record(self, path: Path) -> _EpisodeRecord | None:
        match = _EPISODE_NAME.fullmatch(path.name)
        if match is None or not self._is_safe_regular_file(path):
            return None
        try:
            relative = path.relative_to(self._root)
            if len(relative.parts) not in {1, 4}:
                return None
            if len(relative.parts) == 4:
                path_task, path_model, path_round, _filename = relative.parts
            with h5py.File(path, "r") as episode:
                attrs = episode.attrs
                frame_count = self._validate_complete_episode(episode)
                task_id = _text_attribute(attrs["task_id"])
                model_id = _text_attribute(attrs["model_id"])
                checkpoint_id = _text_attribute(attrs["checkpoint_id"])
                recorded_dataset_round = _text_attribute(attrs["dataset_round"])
                if len(relative.parts) == 1:
                    if _text_attribute(attrs.get("storage_layout", "legacy")) != "flat":
                        return None
                    path_task, path_model, path_round = task_id, model_id, recorded_dataset_round
                identifiers = (
                    task_id,
                    model_id,
                    checkpoint_id,
                    recorded_dataset_round,
                    path_task,
                    path_model,
                    path_round,
                )
                if not all(IDENTIFIER_RE.fullmatch(value) for value in identifiers):
                    return None
                episode_index = _strict_integer(attrs["episode_index"])
                if episode_index < 0 or episode_index != int(match.group(1)):
                    return None
                if (task_id, model_id) != (path_task, path_model):
                    return None
                episode_uuid = UUID(_text_attribute(attrs["episode_uuid"]))
                (
                    interventions,
                    intervention_phases,
                    intervention_frame_count,
                ) = self._derive_interventions(episode)
                if intervention_frame_count != frame_count:
                    return None
        except (OSError, KeyError, TypeError, ValueError, OverflowError):
            return None
        return _EpisodeRecord(
            path=path,
            episode_uuid=episode_uuid,
            task_id=task_id,
            model_id=model_id,
            checkpoint_id=checkpoint_id,
            dataset_round=path_round,
            recorded_dataset_round=recorded_dataset_round,
            episode_index=episode_index,
            frame_count=frame_count,
            interventions=interventions,
            intervention_phases=intervention_phases,
        )

    @staticmethod
    def _validate_complete_episode(episode: h5py.File) -> int:
        """Validate the complete immutable writer contract without loading images."""
        attrs = episode.attrs
        if _text_attribute(attrs["project_id"]) != PROJECT_ID:
            raise ValueError("invalid project_id")
        if _text_attribute(attrs["collector_version"]) != COLLECTOR_VERSION:
            raise ValueError("invalid collector_version")
        if _strict_integer(attrs["rollout_schema_version"]) != ROLLOUT_SCHEMA_VERSION:
            raise ValueError("invalid rollout schema")
        if _text_attribute(attrs["completion_state"]) != "complete":
            raise ValueError("episode is not complete")
        fps = _finite_float(attrs["fps"])
        dt = _finite_float(attrs["DT"])
        if fps <= 0 or dt <= 0 or not math.isclose(dt, 1.0 / fps, rel_tol=1e-6):
            raise ValueError("invalid recording rate")
        start = _finite_float(attrs["start_timestamp"])
        end = _finite_float(attrs["end_timestamp"])
        if end < start:
            raise ValueError("invalid episode timestamps")
        if not _text_attribute(attrs["termination_reason"]):
            raise ValueError("missing termination reason")

        action = episode["action"]
        if action.dtype != np.dtype(np.float32) or action.ndim != 2:
            raise ValueError("invalid action dataset")
        frame_count = int(action.shape[0])
        if frame_count <= 0 or action.shape != (frame_count, 14):
            raise ValueError("invalid action shape")

        def require(path: str, shape: tuple[int, ...], dtype: np.dtype[object]) -> None:
            dataset = episode[path]
            if dataset.shape != shape or dataset.dtype != dtype:
                raise ValueError(f"invalid dataset: {path}")

        for name in IMAGE_DATASET_NAMES:
            dataset = episode[f"observations/images/{name}"]
            if (
                dataset.dtype != np.dtype(np.uint8)
                or dataset.ndim != 4
                or dataset.shape[0] != frame_count
                or dataset.shape[-1] != 3
                or any(size <= 0 for size in dataset.shape[1:3])
            ):
                raise ValueError(f"invalid image dataset: {name}")
        for path in STANDARD_VECTOR_DATASETS:
            require(path, (frame_count, 14), np.dtype(np.float32))
        require("base_action", (frame_count, 2), np.dtype(np.float32))
        for name in ROLLOUT_VECTOR_FIELDS:
            require(f"rollout/{name}", (frame_count, 14), np.dtype(np.float32))
        for name, dtype in ROLLOUT_SCALAR_DTYPES.items():
            require(f"rollout/{name}", (frame_count,), dtype)
        for name in ("handover_mode", "handover_fault"):
            dataset = episode[f"rollout/{name}"]
            string_info = h5py.check_string_dtype(dataset.dtype)
            if (
                dataset.shape != (frame_count,)
                or string_info is None
                or string_info.encoding != "utf-8"
            ):
                raise ValueError(f"invalid text dataset: {name}")
        for key in REQUIRED_TOPICS:
            require(
                f"rollout/topic_timestamp/{key}",
                (frame_count,),
                np.dtype(np.float64),
            )
            require(
                f"rollout/arrival_timestamp/{key}",
                (frame_count,),
                np.dtype(np.float64),
            )
        for key in ROLLOUT_VALIDITY_KEYS:
            require(
                f"rollout/valid_mask/{key}",
                (frame_count,),
                np.dtype(np.bool_),
            )

        coordinator_valid = np.asarray(
            episode["rollout/valid_mask/coordinator_command"][...], dtype=np.bool_
        )
        action_valid = np.asarray(
            episode["rollout/valid_mask/action"][...], dtype=np.bool_
        )
        if not np.array_equal(coordinator_valid, action_valid):
            raise ValueError("action validity does not match coordinator validity")
        if coordinator_valid.any():
            coordinator = np.asarray(
                episode["rollout/coordinator_command"][coordinator_valid]
            )
            recorded_action = np.asarray(action[coordinator_valid])
            if not np.array_equal(recorded_action, coordinator, equal_nan=True):
                raise ValueError("action does not match coordinator command")
        return frame_count

    @staticmethod
    def _derive_interventions(
        episode: h5py.File,
    ) -> tuple[
        tuple[dict[str, object], ...],
        tuple[dict[str, object], ...],
        int,
    ]:
        required = [
            "rollout/control_source_left",
            "rollout/control_source_right",
            "rollout/intervention_id_left",
            "rollout/intervention_id_right",
            "rollout/is_intervention_left",
            "rollout/is_intervention_right",
            "rollout/frame_index",
            "rollout/sample_timestamp",
            "rollout/handover_mode",
        ]
        datasets = {name: episode[name] for name in required}
        expected_dtypes = {
            "rollout/control_source_left": np.dtype(np.uint8),
            "rollout/control_source_right": np.dtype(np.uint8),
            "rollout/intervention_id_left": np.dtype(np.uint64),
            "rollout/intervention_id_right": np.dtype(np.uint64),
            "rollout/is_intervention_left": np.dtype(np.bool_),
            "rollout/is_intervention_right": np.dtype(np.bool_),
            "rollout/frame_index": np.dtype(np.uint64),
            "rollout/sample_timestamp": np.dtype(np.float64),
        }
        if any(
            datasets[name].dtype != dtype for name, dtype in expected_dtypes.items()
        ):
            raise ValueError("invalid intervention dataset dtype")
        string_info = h5py.check_string_dtype(datasets["rollout/handover_mode"].dtype)
        if string_info is None or string_info.encoding != "utf-8":
            raise ValueError("handover_mode must be UTF-8")
        arrays = {
            name: np.asarray(dataset[...])
            for name, dataset in datasets.items()
            if name != "rollout/handover_mode"
        }
        modes = np.asarray(datasets["rollout/handover_mode"].asstr()[...])
        arrays["rollout/handover_mode"] = modes
        lengths = {value.shape[0] for value in arrays.values() if value.ndim == 1}
        if len(lengths) != 1 or any(value.ndim != 1 for value in arrays.values()):
            raise ValueError("invalid intervention dataset shapes")
        frame_count = lengths.pop()
        frame_index = arrays["rollout/frame_index"]
        timestamps = arrays["rollout/sample_timestamp"]
        if not np.array_equal(frame_index, np.arange(frame_count, dtype=np.uint64)):
            raise ValueError("frame indices must be contiguous")
        if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) < 0):
            raise ValueError("sample timestamps must be finite and monotonic")
        if any(not isinstance(mode, str) or not mode for mode in modes.tolist()):
            raise ValueError("handover modes must be non-empty strings")
        derived: list[dict[str, object]] = []
        for side in ("left", "right"):
            sources = arrays[f"rollout/control_source_{side}"]
            identifiers = arrays[f"rollout/intervention_id_{side}"]
            intervention_flags = arrays[f"rollout/is_intervention_{side}"]
            if not np.isin(sources, [int(item) for item in ControlSource]).all():
                raise ValueError("invalid control source")
            human = sources == int(ControlSource.HUMAN)
            if not np.array_equal(intervention_flags, human):
                raise ValueError("intervention flags do not match control source")
            if np.any(human & (identifiers == 0)):
                raise ValueError("human frames require a positive intervention ID")
            expected_id = 0
            was_human = False
            # Every one-dimensional rollout field has already been checked
            # against the same frame count above; plain zip keeps the runtime
            # compatible with the Cobot's ROS Python 3.8.
            for source, intervention_id in zip(sources, identifiers):
                is_human = int(source) == int(ControlSource.HUMAN)
                if is_human and not was_human:
                    expected_id += 1
                if int(intervention_id) != expected_id:
                    raise ValueError("intervention ID transition is inconsistent")
                was_human = is_human
            start: int | None = None
            active_id = 0
            for position in range(frame_count + 1):
                is_human = (
                    position < frame_count
                    and int(sources[position]) == int(ControlSource.HUMAN)
                    and int(identifiers[position]) > 0
                )
                same_interval = is_human and (
                    start is None or int(identifiers[position]) == active_id
                )
                if start is not None and not same_interval:
                    end = position - 1
                    derived.append(
                        {
                            "side": side,
                            "intervention_id": active_id,
                            "start_frame": int(frame_index[start]),
                            "end_frame": int(frame_index[end]),
                            "start_timestamp": float(timestamps[start]),
                            "end_timestamp": float(timestamps[end]),
                            "handover_mode": str(modes[start]),
                            "reason": "unknown",
                            "outcome": "uncertain",
                            "quality": "uncertain",
                            "note": None,
                        }
                    )
                    start = None
                if is_human and start is None:
                    start = position
                    active_id = int(identifiers[position])
            # The sentinel position closes every active interval in the loop.
        for position, mode in enumerate(modes.tolist()):
            expected = parse_handover_mode(mode)
            if int(arrays["rollout/control_source_left"][position]) != int(
                expected.left
            ) or int(arrays["rollout/control_source_right"][position]) != int(
                expected.right
            ):
                raise ValueError("handover mode does not match control sources")
        derived.sort(key=lambda item: (str(item["side"]), int(item["start_frame"])))
        phases: list[dict[str, object]] = []
        phase_start = 0
        mode_values = modes.tolist()
        for position in range(1, frame_count + 1):
            same_mode = (
                position < frame_count
                and mode_values[position] == mode_values[phase_start]
            )
            if same_mode:
                continue
            mode = str(mode_values[phase_start])
            pair = parse_handover_mode(mode)
            human_sides = []
            if pair.left is ControlSource.HUMAN:
                human_sides.append("left")
            if pair.right is ControlSource.HUMAN:
                human_sides.append("right")
            if human_sides:
                phases.append(
                    {
                        "start_frame": int(frame_index[phase_start]),
                        "end_frame": int(frame_index[position - 1]),
                        "handover_mode": mode,
                        "human_sides": human_sides,
                    }
                )
            phase_start = position
        return tuple(derived), tuple(phases), frame_count

    def _records(self) -> list[_EpisodeRecord]:
        if not self._root.is_dir() or self._root.is_symlink():
            return []
        records: list[_EpisodeRecord] = []
        for path in self._root.rglob("episode_*.hdf5"):
            record = self._read_record(path)
            if record is not None:
                records.append(record)
        records.sort(
            key=lambda item: (
                item.task_id,
                item.model_id,
                item.dataset_round,
                item.episode_index,
            )
        )
        return records

    def _find_record(self, episode_uuid: UUID | str) -> _EpisodeRecord:
        try:
            wanted = (
                episode_uuid if isinstance(episode_uuid, UUID) else UUID(episode_uuid)
            )
        except (TypeError, ValueError, AttributeError) as error:
            raise LabelNotFoundError("episode_not_found") from error
        matches = [item for item in self._records() if item.episode_uuid == wanted]
        if not matches:
            raise LabelNotFoundError("episode_not_found")
        if len(matches) > 1:
            raise LabelConflictError("duplicate_episode_uuid")
        return matches[0]

    @staticmethod
    def _sidecar_path(record: _EpisodeRecord) -> Path:
        return record.path.with_suffix(".labels.json")

    @staticmethod
    def _default_payload(record: _EpisodeRecord) -> dict[str, object]:
        return {
            "label_schema_version": LABEL_SCHEMA_VERSION,
            "episode_uuid": str(record.episode_uuid),
            "episode_outcome": "unknown",
            "episode_quality": "uncertain",
            "last_completed_stage": None,
            "failure_stage": None,
            "failure_type": None,
            "termination_reason": None,
            "keep_for_training": "undecided",
            "operator_note": None,
            "interventions": [dict(item) for item in record.interventions],
            "label_updated_at": _timestamp(record.path),
        }

    def _read_labels(self, record: _EpisodeRecord) -> dict[str, object]:
        defaults = self._default_payload(record)
        sidecar = self._sidecar_path(record)
        if not sidecar.exists():
            return defaults
        if sidecar.is_symlink() or not sidecar.is_file():
            raise LabelValidationError("unsafe label sidecar")
        try:
            stored = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise LabelValidationError("invalid label sidecar") from error
        if not isinstance(stored, dict):
            raise LabelValidationError("invalid label sidecar")
        unknown = set(stored) - _TOP_LEVEL_FIELDS
        if unknown:
            raise LabelValidationError(f"unknown label field: {min(unknown)}")
        if stored.get("episode_uuid") != str(record.episode_uuid):
            raise LabelConflictError("episode_uuid_mismatch")
        merged = dict(defaults)
        merged.update(
            {
                key: value
                for key, value in stored.items()
                if key in _TOP_LEVEL_FIELDS and key != "interventions"
            }
        )
        merged["interventions"] = self._merge_saved_interventions(
            record.interventions, stored.get("interventions", [])
        )
        self._validate_episode_labels(merged)
        return merged

    def _merge_saved_interventions(
        self,
        derived: tuple[dict[str, object], ...],
        saved: object,
    ) -> list[dict[str, object]]:
        if not isinstance(saved, list):
            raise LabelValidationError("interventions must be a list")
        result = [dict(item) for item in derived]
        by_key = {(item["side"], item["intervention_id"]): item for item in result}
        seen: set[tuple[object, object]] = set()
        for update in saved:
            key, values = self._validate_intervention_update(update, by_key)
            if key in seen:
                raise LabelValidationError("duplicate intervention update")
            seen.add(key)
            by_key[key].update(values)
        return result

    @staticmethod
    def _validate_intervention_update(
        update: object,
        derived: Mapping[tuple[object, object], dict[str, object]],
    ) -> tuple[tuple[object, object], dict[str, object]]:
        if not isinstance(update, Mapping):
            raise LabelValidationError("intervention update must be an object")
        unknown = set(update) - (
            _INTERVENTION_IDENTITY_FIELDS
            | _INTERVENTION_READ_ONLY_FIELDS
            | _INTERVENTION_EDITABLE_FIELDS
        )
        if unknown:
            raise LabelValidationError(f"unknown intervention field: {min(unknown)}")
        side = update.get("side")
        intervention_id = update.get("intervention_id")
        if (
            side not in {"left", "right"}
            or isinstance(intervention_id, bool)
            or not isinstance(intervention_id, int)
        ):
            raise LabelValidationError("invalid intervention identity")
        try:
            key = (side, int(intervention_id))
        except (TypeError, ValueError, OverflowError) as error:
            raise LabelValidationError("invalid intervention identity") from error
        if key not in derived or intervention_id != key[1]:
            raise LabelValidationError("unknown intervention identity")
        baseline = derived[key]
        for name in _INTERVENTION_READ_ONLY_FIELDS & set(update):
            if update[name] != baseline[name]:
                raise LabelValidationError(f"{name} is read_only")
        editable = {
            name: update[name] for name in _INTERVENTION_EDITABLE_FIELDS & set(update)
        }
        if "reason" in editable and editable["reason"] not in _INTERVENTION_REASONS:
            raise LabelValidationError("invalid intervention reason")
        if "outcome" in editable and editable["outcome"] not in _INTERVENTION_OUTCOMES:
            raise LabelValidationError("invalid intervention outcome")
        if "quality" in editable and editable["quality"] not in _INTERVENTION_QUALITIES:
            raise LabelValidationError("invalid intervention quality")
        if (
            "note" in editable
            and editable["note"] is not None
            and not isinstance(editable["note"], str)
        ):
            raise LabelValidationError("invalid intervention note")
        return key, editable

    @staticmethod
    def _validate_episode_labels(payload: Mapping[str, object]) -> None:
        try:
            unknown = set(payload) - _TOP_LEVEL_FIELDS
            if unknown:
                raise ValueError(f"unknown label field: {min(unknown)}")
            version = payload.get("label_schema_version")
            if (
                isinstance(version, bool)
                or not isinstance(version, int)
                or version != LABEL_SCHEMA_VERSION
            ):
                raise ValueError("invalid label_schema_version")
            _utc_timestamp(payload.get("label_updated_at"))
            for name in (
                "last_completed_stage",
                "failure_stage",
                "failure_type",
                "operator_note",
            ):
                value = payload.get(name)
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"{name} must be a string or null")
            UUID(str(payload["episode_uuid"]))
            labels = EpisodeLabels(
                episode_uuid=UUID(str(payload["episode_uuid"])),
                episode_outcome=payload.get("episode_outcome", "unknown"),  # type: ignore[arg-type]
                episode_quality=payload.get("episode_quality", "uncertain"),  # type: ignore[arg-type]
                last_completed_stage=payload.get("last_completed_stage"),  # type: ignore[arg-type]
                failure_stage=payload.get("failure_stage"),  # type: ignore[arg-type]
                failure_type=payload.get("failure_type"),  # type: ignore[arg-type]
                termination_reason=payload.get("termination_reason"),  # type: ignore[arg-type]
                keep_for_training=payload.get("keep_for_training", "undecided"),  # type: ignore[arg-type]
                operator_note=payload.get("operator_note"),  # type: ignore[arg-type]
                interventions=tuple(payload.get("interventions", ())),  # type: ignore[arg-type]
            )
            del labels
        except (KeyError, TypeError, ValueError) as error:
            raise LabelValidationError(str(error)) from error

    def list_episodes(self) -> list[dict[str, object]]:
        """Return metadata for validated, finalized ``completion_state=complete`` files."""
        with self._lock:
            result = []
            for record in self._records():
                sidecar = self._sidecar_path(record)
                labels = self._read_labels(record)
                result.append(
                    {
                        "episode_uuid": str(record.episode_uuid),
                        "task_id": record.task_id,
                        "model_id": record.model_id,
                        "checkpoint_id": record.checkpoint_id,
                        "dataset_round": record.dataset_round,
                        "recorded_dataset_round": record.recorded_dataset_round,
                        "round_name_mismatch": (
                            record.dataset_round != record.recorded_dataset_round
                        ),
                        "episode_index": record.episode_index,
                        "completion_state": "complete",
                        "frame_count": record.frame_count,
                        "size_bytes": record.path.stat().st_size,
                        "has_labels": sidecar.is_file() and not sidecar.is_symlink(),
                        "labels_complete": labels_are_complete(labels),
                        "episode_outcome": labels.get("episode_outcome", "unknown"),
                        "has_hil": bool(labels.get("interventions")),
                    }
                )
            return result

    def inspect_series(
        self, task_id: str, model_id: str, dataset_round: str
    ) -> dict[str, object]:
        """Return the latest finalized episode and its forward label gate."""
        for name, value in (
            ("task_id", task_id),
            ("model_id", model_id),
            ("dataset_round", dataset_round),
        ):
            if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
                raise LabelValidationError(f"invalid {name}")
        with self._lock:
            matching = [
                record
                for record in self._records()
                if (
                    record.task_id,
                    record.model_id,
                    record.dataset_round,
                )
                == (task_id, model_id, dataset_round)
            ]
            if not matching:
                return {
                    "episode_count": 0,
                    "latest_episode_uuid": None,
                    "latest_episode_index": None,
                    "latest_labels_complete": True,
                    "label_blocked": False,
                }
            latest = max(matching, key=lambda record: record.episode_index)
            complete = labels_are_complete(self._read_labels(latest))
            return {
                "episode_count": len(matching),
                "latest_episode_uuid": str(latest.episode_uuid),
                "latest_episode_index": latest.episode_index,
                "latest_labels_complete": complete,
                "label_blocked": not complete,
            }

    def get_intervention_phases(
        self, episode_uuid: UUID | str
    ) -> list[dict[str, object]]:
        """Return read-only global handover phases for operator display."""
        with self._lock:
            record = self._find_record(episode_uuid)
            return [dict(item) for item in record.intervention_phases]

    def get_labels(self, episode_uuid: UUID | str) -> dict[str, object]:
        """Return derived intervals merged with any server-authored augmentation."""
        with self._lock:
            return self._read_labels(self._find_record(episode_uuid))

    def get_episode_artifacts(self, episode_uuid: UUID | str) -> EpisodeArtifacts:
        """Return validated paths for internal replay/delete services."""
        with self._lock:
            record = self._find_record(episode_uuid)
            return EpisodeArtifacts(
                episode_uuid=record.episode_uuid,
                episode_index=record.episode_index,
                episode_path=record.path,
                sidecar_path=self._sidecar_path(record),
                preview_directory=(
                    record.path.parent / ".previews" / str(record.episode_uuid)
                ),
                frame_count=record.frame_count,
                size_bytes=record.path.stat().st_size,
            )

    def set_outcome(
        self, episode_uuid: UUID | str, outcome: str
    ) -> dict[str, object]:
        """Apply the minimal operator decision and compatibility annotations."""
        if outcome not in {"success", "failure"}:
            raise LabelValidationError("outcome must be success or failure")
        with self._lock:
            record = self._find_record(episode_uuid)
            current = self._read_labels(record)
            approved = outcome == "success"
            interventions = [
                {
                    "side": item["side"],
                    "intervention_id": item["intervention_id"],
                    "quality": "good" if approved else "uncertain",
                    "outcome": "recovered" if approved else "uncertain",
                }
                for item in current["interventions"]  # type: ignore[union-attr]
            ]
            return self.update_labels(
                record.episode_uuid,
                {
                    "episode_uuid": str(record.episode_uuid),
                    "episode_outcome": outcome,
                    "episode_quality": "good" if approved else "bad",
                    "keep_for_training": "true" if approved else "false",
                    "interventions": interventions,
                },
            )

    def update_labels(
        self, episode_uuid: UUID | str, update: Mapping[str, object]
    ) -> dict[str, object]:
        """Merge a partial update and atomically publish its JSON sidecar."""
        if not isinstance(update, Mapping):
            raise LabelValidationError("label update must be an object")
        unknown = set(update) - _UPDATE_FIELDS
        if unknown:
            raise LabelValidationError(f"unknown label field: {min(unknown)}")
        with self._lock:
            record = self._find_record(episode_uuid)
            body_uuid = update.get("episode_uuid")
            if body_uuid is None or str(body_uuid) != str(record.episode_uuid):
                raise LabelConflictError("episode_uuid_mismatch")
            if (
                isinstance(
                    update.get("label_schema_version", LABEL_SCHEMA_VERSION), bool
                )
                or not isinstance(
                    update.get("label_schema_version", LABEL_SCHEMA_VERSION), int
                )
                or update.get("label_schema_version", LABEL_SCHEMA_VERSION)
                != LABEL_SCHEMA_VERSION
            ):
                raise LabelValidationError("invalid label_schema_version")
            current = self._read_labels(record)
            merged = dict(current)
            if "operator_nodes" in update:
                nodes = update["operator_nodes"]
                if not isinstance(nodes, list) or len(nodes) > 4096:
                    raise LabelValidationError("invalid operator_nodes")
                for node in nodes:
                    if (not isinstance(node, dict) or set(node) != {"frame_index", "node_kind"}
                            or type(node["frame_index"]) is not int
                            or not 0 <= node["frame_index"] < record.frame_count
                            or node["node_kind"] not in {"pause", "resume", "marker"}):
                        raise LabelValidationError("invalid operator node")
                merged["operator_nodes"] = sorted(nodes, key=lambda node: node["frame_index"])
            for name in _EDITABLE_LABEL_FIELDS & set(update):
                merged[name] = update[name]
            if "interventions" in update:
                current_interventions = tuple(
                    dict(item)
                    for item in current["interventions"]  # type: ignore[union-attr]
                )
                merged["interventions"] = self._merge_saved_interventions(
                    current_interventions, update["interventions"]
                )
            merged["label_schema_version"] = LABEL_SCHEMA_VERSION
            merged["episode_uuid"] = str(record.episode_uuid)
            merged["label_updated_at"] = (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            )
            self._validate_episode_labels(merged)
            self._atomic_write(self._sidecar_path(record), merged)
            return merged

    @staticmethod
    def _write_temp(path: Path, data: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_parent_directory(parent: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _atomic_write(self, sidecar: Path, payload: Mapping[str, object]) -> None:
        if sidecar.is_symlink():
            raise LabelValidationError("unsafe label sidecar")
        temp = Path(f"{sidecar}.tmp")
        if temp.is_symlink():
            raise LabelValidationError("unsafe label temp file")
        old_data = sidecar.read_bytes() if sidecar.exists() else None
        data = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        published = False
        try:
            self._write_temp(temp, data)
            os.replace(temp, sidecar)
            published = True
            self._fsync_parent_directory(sidecar.parent)
        except Exception:
            try:
                if temp.exists() and not temp.is_symlink():
                    temp.unlink()
            except OSError:
                pass
            if published:
                try:
                    if old_data is None:
                        sidecar.unlink(missing_ok=True)
                    else:
                        self._write_temp(temp, old_data)
                        os.replace(temp, sidecar)
                    self._fsync_parent_directory(sidecar.parent)
                except OSError:
                    pass
            raise

    # Explicit aliases keep call sites readable without exposing file paths.
    load = get_labels
    save = update_labels

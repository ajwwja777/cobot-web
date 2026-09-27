"""Immutable, validated records shared by Task5 v1 components."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from types import MappingProxyType
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
VECTOR_DIMENSION = 14


class ControlSource(IntEnum):
    """Stable integer labels persisted in rollout HDF5 files."""

    UNKNOWN = 0
    POLICY = 1
    HUMAN = 2
    HOLD = 3
    SAFETY = 4


def _validate_identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must match {IDENTIFIER_RE.pattern}")
    return value


def _readonly_array(
    value: NDArray[np.generic],
    *,
    name: str,
    shape: tuple[int, ...],
    dtype: np.dtype[np.generic],
) -> NDArray[np.generic]:
    array = np.asarray(value)
    if array.shape != shape:
        raise ValueError(f"{name} must have shape {shape}, got {array.shape}")
    if array.dtype != dtype:
        raise ValueError(f"{name} must have dtype {dtype}, got {array.dtype}")
    array = np.array(array, copy=True)
    array.setflags(write=False)
    return array


def _freeze_intervention_value(value: object) -> object:
    """Return an immutable, JSON-compatible intervention value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("intervention mapping keys must be strings")
        return MappingProxyType(
            {key: _freeze_intervention_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_intervention_value(item) for item in value)
    raise ValueError("intervention values must be JSON-compatible")


@dataclass(frozen=True)
class EpisodeIdentity:
    task_id: str
    model_id: str
    checkpoint_id: str
    dataset_round: str
    episode_index: int = 0
    episode_uuid: UUID = field(default_factory=uuid4)
    storage_layout: str = "legacy"

    def __post_init__(self) -> None:
        for name in ("task_id", "model_id", "checkpoint_id", "dataset_round"):
            _validate_identifier(name, getattr(self, name))
        if self.storage_layout not in {"legacy", "flat"}:
            raise ValueError("invalid storage_layout")
        if self.episode_index < 0:
            raise ValueError("episode_index must be non-negative")
        if not isinstance(self.episode_uuid, UUID):
            raise ValueError(  # noqa: TRY004 - preserve public validation contract
                "episode_uuid must be a UUID"
            )


@dataclass(frozen=True)
class FrameSample:
    """One sampler tick, with immutable array snapshots and stream metadata."""

    identity: EpisodeIdentity
    frame_index: int
    sample_timestamp: float
    camera_high_rgb: NDArray[np.uint8]
    camera_left_rgb: NDArray[np.uint8]
    camera_right_rgb: NDArray[np.uint8]
    qpos: NDArray[np.floating]
    qvel: NDArray[np.floating]
    effort: NDArray[np.floating]
    action: NDArray[np.floating]
    policy_command_submitted: NDArray[np.floating]
    coordinator_command: NDArray[np.floating]
    front_observation: NDArray[np.floating]
    rear_observation: NDArray[np.floating]
    teach_active_left: bool
    teach_active_right: bool
    handover_mode: str
    handover_fault: str
    control_source_left: ControlSource
    control_source_right: ControlSource
    is_intervention_left: bool
    is_intervention_right: bool
    intervention_id_left: int
    intervention_id_right: int
    source_timestamps: Mapping[str, float]
    arrival_timestamps: Mapping[str, float]
    valid_mask: Mapping[str, bool]
    base_action: NDArray[np.floating] = field(
        default_factory=lambda: np.zeros(2, dtype=np.float32)
    )

    def __post_init__(self) -> None:
        if self.frame_index < 0:
            raise ValueError("frame_index must be non-negative")
        if not self.handover_mode:
            raise ValueError("handover_mode must not be empty")
        if not isinstance(self.handover_fault, str):
            raise TypeError("handover_fault must be a str")
        if self.intervention_id_left < 0 or self.intervention_id_right < 0:
            raise ValueError("intervention IDs must be non-negative")
        for name in ("camera_high_rgb", "camera_left_rgb", "camera_right_rgb"):
            image = np.asarray(getattr(self, name))
            if image.ndim != 3 or image.shape[-1] != 3:
                raise ValueError(f"{name} must be an HxWx3 RGB array")
            object.__setattr__(
                self,
                name,
                _readonly_array(
                    image, name=name, shape=image.shape, dtype=np.dtype(np.uint8)
                ),
            )
        for name in (
            "qpos",
            "qvel",
            "effort",
            "action",
            "policy_command_submitted",
            "coordinator_command",
            "front_observation",
            "rear_observation",
        ):
            object.__setattr__(
                self,
                name,
                _readonly_array(
                    getattr(self, name),
                    name=name,
                    shape=(VECTOR_DIMENSION,),
                    dtype=np.dtype(np.float32),
                ),
            )
        object.__setattr__(
            self,
            "base_action",
            _readonly_array(
                self.base_action,
                name="base_action",
                shape=(2,),
                dtype=np.dtype(np.float32),
            ),
        )
        object.__setattr__(
            self, "control_source_left", ControlSource(self.control_source_left)
        )
        object.__setattr__(
            self, "control_source_right", ControlSource(self.control_source_right)
        )
        object.__setattr__(
            self, "source_timestamps", MappingProxyType(dict(self.source_timestamps))
        )
        object.__setattr__(
            self, "arrival_timestamps", MappingProxyType(dict(self.arrival_timestamps))
        )
        if not all(isinstance(value, bool) for value in self.valid_mask.values()):
            raise ValueError("valid_mask values must be bool")
        object.__setattr__(self, "valid_mask", MappingProxyType(dict(self.valid_mask)))


@dataclass(frozen=True)
class EpisodeLabels:
    """Editable semantic sidecar fields; rollout HDF5 remains immutable."""

    episode_uuid: UUID
    episode_outcome: str = "unknown"
    episode_quality: str = "uncertain"
    last_completed_stage: str | None = None
    failure_stage: str | None = None
    failure_type: str | None = None
    termination_reason: str | None = None
    keep_for_training: str = "undecided"
    operator_note: str | None = None
    interventions: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.episode_uuid, UUID):
            raise ValueError(  # noqa: TRY004 - preserve public validation contract
                "episode_uuid must be a UUID"
            )
        if self.episode_outcome not in {"success", "failure", "aborted", "unknown"}:
            raise ValueError("invalid episode_outcome")
        if self.episode_quality not in {"good", "bad", "uncertain"}:
            raise ValueError("invalid episode_quality")
        if self.termination_reason not in {
            None,
            "success",
            "failure",
            "timeout",
            "safety_stop",
            "operator_abort",
            "operator_save",
        }:
            raise ValueError("invalid termination_reason")
        if self.keep_for_training not in {"true", "false", "undecided"}:
            raise ValueError("invalid keep_for_training")
        if not all(isinstance(item, Mapping) for item in self.interventions):
            raise ValueError("interventions must contain mappings")
        object.__setattr__(
            self,
            "interventions",
            tuple(_freeze_intervention_value(item) for item in self.interventions),
        )

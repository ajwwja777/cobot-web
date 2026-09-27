"""Pure synchronized sampler that turns a cache snapshot into one rollout frame."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from .control_source import InterventionTracker, parse_handover_mode
from .ros_cache import CacheSnapshot
from .schema import EpisodeIdentity, FrameSample
from .topics import MODE_KEYS, REQUIRED_TOPICS

_SIDE_SIZE = 7
_VECTOR_SIZE = 14
_NAN_VECTOR = np.full(_VECTOR_SIZE, np.nan, dtype=np.float32)


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _array(value: object | None) -> np.ndarray | None:
    if value is None:
        return None
    try:
        result = np.asarray(value, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    return result if result.size == _SIDE_SIZE else None


def _joint_component(value: object | None, name: str) -> np.ndarray | None:
    if value is None:
        return None
    candidate = _field(value, name)
    if candidate is None and name == "position":
        candidate = value
    return _array(candidate)


def _pair(
    snapshot: CacheSnapshot,
    left_key: str,
    right_key: str,
    component: str,
    *,
    zero_if_missing: bool,
) -> tuple[np.ndarray, bool]:
    left = _joint_component(
        snapshot.get(left_key) if snapshot.is_fresh(left_key) else None, component
    )
    right = _joint_component(
        snapshot.get(right_key) if snapshot.is_fresh(right_key) else None, component
    )
    valid = left is not None and right is not None
    if zero_if_missing:
        left = np.zeros(_SIDE_SIZE, dtype=np.float32) if left is None else left
        right = np.zeros(_SIDE_SIZE, dtype=np.float32) if right is None else right
    else:
        left = np.full(_SIDE_SIZE, np.nan, dtype=np.float32) if left is None else left
        right = (
            np.full(_SIDE_SIZE, np.nan, dtype=np.float32) if right is None else right
        )
    return np.concatenate((left, right)).astype(np.float32, copy=False), valid


def _bool(value: object | None) -> bool:
    data = _field(value, "data")
    return bool(value if data is None else data)


def _text(value: object | None) -> str:
    data = _field(value, "data")
    return (
        data
        if isinstance(data, str)
        else value
        if isinstance(value, str)
        else "unknown"
    )


def _strict_text(value: object | None) -> str | None:
    data = _field(value, "data")
    if isinstance(data, str):
        return data
    return value if isinstance(value, str) else None


def _camera_rgb(snapshot: CacheSnapshot, key: str) -> tuple[np.ndarray, bool]:
    value = snapshot.get(key) if snapshot.is_fresh(key) else None
    if not isinstance(value, np.ndarray) or value.ndim != 3 or value.shape[-1] != 3:
        return np.zeros((1, 1, 3), dtype=np.uint8), False
    if value.dtype != np.uint8:
        return np.zeros((1, 1, 3), dtype=np.uint8), False
    return np.array(value[..., ::-1], copy=True), True


class FrameSampler:
    """Build immutable frames using coordinator mode as the only control authority."""

    def __init__(self, tracker: InterventionTracker | None = None) -> None:
        self._tracker = tracker or InterventionTracker()
        self._last_sample_timestamp: float | None = None

    def reset(self) -> None:
        """Reset episode-local timestamps and intervention identifiers."""
        self._tracker.reset()
        self._last_sample_timestamp = None

    def sample(
        self, snapshot: CacheSnapshot, identity: EpisodeIdentity, frame_index: int
    ) -> FrameSample:
        """Sample one monotonic cache snapshot without performing any ROS writes."""
        if (
            self._last_sample_timestamp is not None
            and snapshot.now < self._last_sample_timestamp
        ):
            raise ValueError("sample timestamps must be monotonic")
        self._last_sample_timestamp = snapshot.now

        camera_high_rgb, high_ok = _camera_rgb(snapshot, "camera_high")
        camera_left_rgb, left_camera_ok = _camera_rgb(snapshot, "camera_left")
        camera_right_rgb, right_camera_ok = _camera_rgb(snapshot, "camera_right")
        qpos, qpos_ok = _pair(
            snapshot, "front_left", "front_right", "position", zero_if_missing=False
        )
        qvel, qvel_ok = _pair(
            snapshot, "front_left", "front_right", "velocity", zero_if_missing=True
        )
        effort, effort_ok = _pair(
            snapshot, "front_left", "front_right", "effort", zero_if_missing=True
        )
        front_observation = qpos.copy()
        rear_observation, rear_ok = _pair(
            snapshot, "rear_left", "rear_right", "position", zero_if_missing=False
        )
        policy_command, policy_ok = _pair(
            snapshot, "policy_left", "policy_right", "position", zero_if_missing=False
        )
        coordinator_command, coordinator_ok = _pair(
            snapshot,
            "coordinator_left",
            "coordinator_right",
            "position",
            zero_if_missing=False,
        )

        mode_ok = snapshot.has_value("handover_mode")
        handover_mode = _text(snapshot.get("handover_mode")) if mode_ok else "unknown"
        fault_value = (
            _strict_text(snapshot.get("handover_fault"))
            if snapshot.has_value("handover_fault")
            else None
        )
        fault_ok = fault_value is not None
        handover_fault = fault_value if fault_value is not None else ""
        sources = parse_handover_mode(handover_mode, is_fresh=mode_ok)
        intervention = self._tracker.update(snapshot.now, sources)
        teach_left_ok = snapshot.has_value("teach_left")
        teach_right_ok = snapshot.has_value("teach_right")
        teach_active_left = (
            _bool(snapshot.get("teach_left")) if teach_left_ok else False
        )
        teach_active_right = (
            _bool(snapshot.get("teach_right")) if teach_right_ok else False
        )

        source_timestamps = {
            key: snapshot.source_timestamp(key) for key in REQUIRED_TOPICS
        }
        arrival_timestamps = {
            key: snapshot.arrival_timestamp(key) for key in REQUIRED_TOPICS
        }
        valid_mask = {key: snapshot.is_fresh(key) for key in REQUIRED_TOPICS}
        valid_mask.update(
            {key: snapshot.has_value(key) for key in MODE_KEYS}
        )
        valid_mask.update(
            {
                "qpos": qpos_ok,
                "qvel": qvel_ok,
                "effort": effort_ok,
                "front_observation": qpos_ok,
                "rear_observation": rear_ok,
                "policy_command_submitted": policy_ok,
                "coordinator_command": coordinator_ok,
                "action": coordinator_ok,
                "teach_active_left": teach_left_ok,
                "teach_active_right": teach_right_ok,
                "handover_mode": mode_ok,
                "handover_fault": fault_ok,
            }
        )
        valid_mask["camera_high"] = high_ok
        valid_mask["camera_left"] = left_camera_ok
        valid_mask["camera_right"] = right_camera_ok
        return FrameSample(
            identity=identity,
            frame_index=frame_index,
            sample_timestamp=snapshot.now,
            camera_high_rgb=camera_high_rgb,
            camera_left_rgb=camera_left_rgb,
            camera_right_rgb=camera_right_rgb,
            qpos=qpos,
            qvel=qvel,
            effort=effort,
            action=coordinator_command.copy() if coordinator_ok else _NAN_VECTOR.copy(),
            policy_command_submitted=policy_command,
            coordinator_command=coordinator_command,
            front_observation=front_observation,
            rear_observation=rear_observation,
            teach_active_left=teach_active_left,
            teach_active_right=teach_active_right,
            handover_mode=handover_mode,
            handover_fault=handover_fault,
            control_source_left=intervention.control_source_left,
            control_source_right=intervention.control_source_right,
            is_intervention_left=intervention.is_intervention_left,
            is_intervention_right=intervention.is_intervention_right,
            intervention_id_left=intervention.intervention_id_left,
            intervention_id_right=intervention.intervention_id_right,
            source_timestamps=source_timestamps,
            arrival_timestamps=arrival_timestamps,
            valid_mask=valid_mask,
        )

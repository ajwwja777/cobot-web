"""Derive factual per-arm expert supervision without semantic labels."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from capture_core.schema import ControlSource

ACTION_DIM = 14
ARM_DIM = 7


def frame_expert_mask(
    left_source: ControlSource | int,
    right_source: ControlSource | int,
    left_valid: bool,
    right_valid: bool,
) -> NDArray[np.float32]:
    """Map observed control authority to a binary physical-action mask."""
    left = ControlSource(left_source)
    right = ControlSource(right_source)
    mask = np.zeros(ACTION_DIM, dtype=np.float32)
    if left is ControlSource.HUMAN and bool(left_valid):
        mask[:ARM_DIM] = 1.0
    if right is ControlSource.HUMAN and bool(right_valid):
        mask[ARM_DIM:] = 1.0
    return mask


def _validated_masks(masks: ArrayLike) -> NDArray[np.float32]:
    value = np.asarray(masks, dtype=np.float32)
    if value.ndim != 2 or value.shape[1] != ACTION_DIM:
        raise ValueError("expert masks must have shape [H,14]")
    if not np.isin(value, [0.0, 1.0]).all():
        raise ValueError("expert masks must contain only 0.0 or 1.0")
    return value


def _validity(values: ArrayLike, length: int, name: str) -> NDArray[np.bool_]:
    result = np.asarray(values, dtype=np.bool_)
    if result.shape != (length,):
        raise ValueError(f"{name} must have shape [H]")
    return result


def chunk_anchor(
    masks: ArrayLike,
    action_valid: ArrayLike,
    state_valid: ArrayLike,
) -> bool:
    """Return whether an in-episode chunk contains only valid expert elements."""
    value = _validated_masks(masks)
    action = _validity(action_valid, len(value), "action_valid")
    state = _validity(state_valid, len(value), "state_valid")
    selected_rows = np.any(value == 1.0, axis=1)
    if not selected_rows.any():
        return False
    return bool(np.all(action[selected_rows] & state[selected_rows]))


def episode_chunk_anchors(
    masks: ArrayLike,
    *,
    action_valid: ArrayLike,
    state_valid: ArrayLike,
    horizon: int,
) -> NDArray[np.bool_]:
    """Compute anchors using only each episode's remaining, clipped horizon."""
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise ValueError("horizon must be a positive integer")
    value = _validated_masks(masks)
    action = _validity(action_valid, len(value), "action_valid")
    state = _validity(state_valid, len(value), "state_valid")
    result = np.zeros(len(value), dtype=np.bool_)
    for start in range(len(value)):
        end = min(start + horizon, len(value))
        result[start] = chunk_anchor(
            value[start:end], action[start:end], state[start:end]
        )
    return result

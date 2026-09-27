from __future__ import annotations

import itertools

import numpy as np
import pytest

from dataset_tools.expert_mask import (
    chunk_anchor,
    episode_chunk_anchors,
    frame_expert_mask,
)
from capture_core.schema import ControlSource


@pytest.mark.parametrize(
    ("left", "right", "expected_left", "expected_right"),
    [
        (ControlSource.HUMAN, ControlSource.HOLD, 1.0, 0.0),
        (ControlSource.HOLD, ControlSource.HUMAN, 0.0, 1.0),
        (ControlSource.HUMAN, ControlSource.HUMAN, 1.0, 1.0),
        (ControlSource.POLICY, ControlSource.HOLD, 0.0, 0.0),
        (ControlSource.UNKNOWN, ControlSource.UNKNOWN, 0.0, 0.0),
    ],
)
def test_frame_mask_maps_human_authority_to_exact_arm_dimensions(
    left: ControlSource,
    right: ControlSource,
    expected_left: float,
    expected_right: float,
):
    mask = frame_expert_mask(left, right, left_valid=True, right_valid=True)

    assert mask.dtype == np.float32
    assert mask.shape == (14,)
    np.testing.assert_array_equal(mask[:7], np.full(7, expected_left))
    np.testing.assert_array_equal(mask[7:], np.full(7, expected_right))
    assert set(mask.tolist()) <= {0.0, 1.0}


@pytest.mark.parametrize(
    "left_valid,right_valid", itertools.product([False, True], repeat=2)
)
def test_invalid_human_side_is_excluded_independently(
    left_valid: bool, right_valid: bool
):
    mask = frame_expert_mask(
        ControlSource.HUMAN,
        ControlSource.HUMAN,
        left_valid=left_valid,
        right_valid=right_valid,
    )

    assert mask[:7].sum() == (7 if left_valid else 0)
    assert mask[7:].sum() == (7 if right_valid else 0)


def test_chunk_anchor_requires_expert_elements_and_valid_action_state():
    masks = np.zeros((4, 14), dtype=np.float32)
    masks[2, :7] = 1

    assert chunk_anchor(masks, np.ones(4, bool), np.ones(4, bool)) is True
    assert (
        chunk_anchor(np.zeros_like(masks), np.ones(4, bool), np.ones(4, bool)) is False
    )
    action_valid = np.ones(4, bool)
    action_valid[2] = False
    assert chunk_anchor(masks, action_valid, np.ones(4, bool)) is False
    state_valid = np.ones(4, bool)
    state_valid[2] = False
    assert chunk_anchor(masks, np.ones(4, bool), state_valid) is False


def test_episode_chunk_anchors_never_pad_or_cross_episode_end():
    masks = np.zeros((5, 14), dtype=np.float32)
    masks[4, 7:] = 1

    anchors = episode_chunk_anchors(
        masks,
        action_valid=np.ones(5, bool),
        state_valid=np.ones(5, bool),
        horizon=3,
    )

    # Frames 2, 3, 4 see the expert row within their clipped in-episode horizon.
    np.testing.assert_array_equal(anchors, [False, False, True, True, True])


@pytest.mark.parametrize("bad", [np.ones((2, 13)), np.ones((2, 14)) * 0.5])
def test_chunk_functions_reject_wrong_width_or_non_binary_masks(bad: np.ndarray):
    with pytest.raises(ValueError):
        episode_chunk_anchors(
            bad,
            action_valid=np.ones(2, bool),
            state_valid=np.ones(2, bool),
            horizon=2,
        )

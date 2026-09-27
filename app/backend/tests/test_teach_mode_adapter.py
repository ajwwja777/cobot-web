from __future__ import annotations

import pytest

from segmented_capture.teach_mode import (
    TeachEdge,
    TeachModeAdapter,
    TeachModeError,
    mode_to_mask,
)
from segmented_capture.state import TeachMask


def test_mode_diff_preserves_two_directional_edges() -> None:
    adapter = TeachModeAdapter("manual:left+right")

    assert adapter.observe("manual:left") == (TeachEdge("right", entered=False),)
    assert adapter.observe("policy") == (TeachEdge("left", entered=False),)
    assert adapter.observe("policy") == ()


def test_enter_edges_are_emitted_in_stable_side_order() -> None:
    adapter = TeachModeAdapter("policy")

    assert adapter.observe("manual:left+right") == (
        TeachEdge("left", entered=True),
        TeachEdge("right", entered=True),
    )


@pytest.mark.parametrize(
    ("mode", "mask"),
    [
        ("policy", TeachMask(False, False)),
        ("manual:left", TeachMask(True, False)),
        ("manual:right", TeachMask(False, True)),
        ("manual:left+right", TeachMask(True, True)),
    ],
)
def test_mode_parser_accepts_only_the_task2_contract(mode, mask) -> None:
    assert mode_to_mask(mode) == mask


@pytest.mark.parametrize("mode", ["fault", "unknown", "", None])
def test_fault_or_unknown_mode_fails_closed(mode) -> None:
    with pytest.raises(TeachModeError):
        mode_to_mask(mode)

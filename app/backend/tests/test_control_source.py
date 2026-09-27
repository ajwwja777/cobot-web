import pytest

from capture_core.control_source import InterventionTracker, parse_handover_mode
from capture_core.schema import ControlSource


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("policy", (ControlSource.POLICY, ControlSource.POLICY)),
        ("manual:left", (ControlSource.HUMAN, ControlSource.HOLD)),
        ("manual:right", (ControlSource.HOLD, ControlSource.HUMAN)),
        ("manual:left+right", (ControlSource.HUMAN, ControlSource.HUMAN)),
        ("fault", (ControlSource.SAFETY, ControlSource.SAFETY)),
        ("garbage", (ControlSource.UNKNOWN, ControlSource.UNKNOWN)),
    ],
)
def test_mode_mapping(mode, expected):
    """Catches a mode parser that misclassifies the coordinator's authority."""
    pair = parse_handover_mode(mode)

    assert (pair.left, pair.right) == expected


def test_stale_mode_is_unknown_even_when_its_last_value_was_manual():
    """Catches reusing a latched manual mode after it exceeds its freshness limit."""
    pair = parse_handover_mode("manual:left", is_fresh=False)

    assert (pair.left, pair.right) == (ControlSource.UNKNOWN, ControlSource.UNKNOWN)


def test_intervention_id_increments_only_when_left_enters_human_control():
    """Catches IDs incrementing for exits, holds, or non-human mode changes."""
    tracker = InterventionTracker()

    first = tracker.update(1.0, parse_handover_mode("policy"))
    entering = tracker.update(2.0, parse_handover_mode("manual:left"))
    held = tracker.update(3.0, parse_handover_mode("manual:left"))
    exiting = tracker.update(4.0, parse_handover_mode("policy"))
    safety = tracker.update(5.0, parse_handover_mode("fault"))
    reentering = tracker.update(6.0, parse_handover_mode("manual:left"))

    assert first.intervention_id_left == 0
    assert entering.intervention_id_left == 1
    assert held.intervention_id_left == 1
    assert exiting.intervention_id_left == 1
    assert safety.intervention_id_left == 1
    assert reentering.intervention_id_left == 2
    assert entering.is_intervention_left is True
    assert held.is_intervention_left is True
    assert exiting.is_intervention_left is False


def test_left_and_right_interventions_have_independent_ids_and_activity():
    """Catches a shared intervention counter or activity flag across both arms."""
    tracker = InterventionTracker()

    left = tracker.update(1.0, parse_handover_mode("manual:left"))
    right = tracker.update(2.0, parse_handover_mode("manual:right"))
    both = tracker.update(3.0, parse_handover_mode("manual:left+right"))

    assert (left.intervention_id_left, left.intervention_id_right) == (1, 0)
    assert (left.is_intervention_left, left.is_intervention_right) == (True, False)
    assert (right.intervention_id_left, right.intervention_id_right) == (1, 1)
    assert (right.is_intervention_left, right.is_intervention_right) == (False, True)
    assert (both.intervention_id_left, both.intervention_id_right) == (2, 1)
    assert (both.is_intervention_left, both.is_intervention_right) == (True, True)

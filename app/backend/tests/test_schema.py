import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from capture_core.schema import ControlSource, EpisodeIdentity, EpisodeLabels, FrameSample


def test_control_source_codes_are_stable():
    assert int(ControlSource.UNKNOWN) == 0
    assert int(ControlSource.POLICY) == 1
    assert int(ControlSource.HUMAN) == 2
    assert int(ControlSource.HOLD) == 3
    assert int(ControlSource.SAFETY) == 4


def test_episode_identity_rejects_path_components():
    with pytest.raises(ValueError):
        EpisodeIdentity(
            task_id="../bad",
            model_id="pi05",
            checkpoint_id="step_2000",
            dataset_round="round_001",
        )


def _frame_sample(**overrides):
    identity = EpisodeIdentity(
        task_id="in_the_pot",
        model_id="pi05",
        checkpoint_id="step_2000",
        dataset_round="round_001",
    )
    values = {
        "identity": identity,
        "frame_index": 0,
        "sample_timestamp": 1.0,
        "camera_high_rgb": np.zeros((2, 2, 3), dtype=np.uint8),
        "camera_left_rgb": np.zeros((2, 2, 3), dtype=np.uint8),
        "camera_right_rgb": np.zeros((2, 2, 3), dtype=np.uint8),
        "qpos": np.zeros(14, dtype=np.float32),
        "qvel": np.zeros(14, dtype=np.float32),
        "effort": np.zeros(14, dtype=np.float32),
        "action": np.zeros(14, dtype=np.float32),
        "policy_command_submitted": np.zeros(14, dtype=np.float32),
        "coordinator_command": np.zeros(14, dtype=np.float32),
        "front_observation": np.zeros(14, dtype=np.float32),
        "rear_observation": np.zeros(14, dtype=np.float32),
        "teach_active_left": False,
        "teach_active_right": False,
        "handover_mode": "policy",
        "handover_fault": "",
        "control_source_left": ControlSource.POLICY,
        "control_source_right": ControlSource.POLICY,
        "is_intervention_left": False,
        "is_intervention_right": False,
        "intervention_id_left": 0,
        "intervention_id_right": 0,
        "source_timestamps": {"camera_high": 1.0},
        "arrival_timestamps": {"camera_high": 1.1},
        "valid_mask": {"camera_high": True},
    }
    values.update(overrides)
    return FrameSample(**values)


def test_frame_sample_metadata_is_immutable():
    source_timestamps = {"camera_high": 1.0}
    arrival_timestamps = {"camera_high": 1.1}
    valid_mask = {"camera_high": True}
    sample = _frame_sample(
        source_timestamps=source_timestamps,
        arrival_timestamps=arrival_timestamps,
        valid_mask=valid_mask,
    )
    interventions = ({"metadata": {"operator": "alice"}},)
    labels = EpisodeLabels(
        episode_uuid=sample.identity.episode_uuid,
        interventions=interventions,
    )
    source_timestamps["camera_high"] = 2.0
    arrival_timestamps["camera_high"] = 2.1
    valid_mask["camera_high"] = False
    interventions[0]["metadata"]["operator"] = "bob"

    with pytest.raises(TypeError):
        sample.source_timestamps["camera_high"] = 2.0
    with pytest.raises(TypeError):
        sample.arrival_timestamps["camera_high"] = 2.1
    with pytest.raises(TypeError):
        sample.valid_mask["camera_high"] = False
    with pytest.raises(TypeError):
        labels.interventions[0]["metadata"]["operator"] = "bob"
    assert sample.source_timestamps["camera_high"] == 1.0
    assert sample.arrival_timestamps["camera_high"] == 1.1
    assert sample.valid_mask["camera_high"] is True
    assert labels.interventions[0]["metadata"]["operator"] == "alice"


def test_failure_episode_labels_allow_optional_diagnostic_details():
    labels = EpisodeLabels(
        episode_uuid=uuid4(),
        episode_outcome="failure",
        episode_quality="bad",
        keep_for_training="false",
    )

    assert labels.failure_stage is None
    assert labels.failure_type is None


def test_frame_sample_rejects_non_rgb_uint8_and_non_boolean_validity():
    with pytest.raises(ValueError):
        _frame_sample(camera_high_rgb=np.zeros((2, 2, 3), dtype=np.float32))
    with pytest.raises(ValueError):
        _frame_sample(valid_mask={"camera_high": "false"})
    with pytest.raises(ValueError):
        _frame_sample(qpos=np.zeros(14, dtype=np.float64))
    with pytest.raises(ValueError):
        _frame_sample(base_action=np.zeros(2, dtype=np.float64))


def test_frame_sample_requires_an_immutable_string_fault_payload():
    """Catches dropping or retaining a mutable ROS message as fault telemetry."""
    sample = _frame_sample(handover_fault="left servo timeout")

    assert sample.handover_fault == "left servo timeout"
    with pytest.raises(TypeError, match="handover_fault"):
        _frame_sample(handover_fault={"data": "left servo timeout"})


def test_rollout_schema_declares_fault_payload_dataset():
    """Catches the machine-readable schema omitting persisted fault diagnostics."""
    schema_path = (
        Path(__file__).resolve().parents[2] / "shared/schemas/rollout_v1.schema.json"
    )
    schema = json.loads(schema_path.read_text())

    assert "rollout/handover_fault" in schema["properties"]["rollout_fields"]["const"]

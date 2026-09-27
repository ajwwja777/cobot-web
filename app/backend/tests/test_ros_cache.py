"""Behavioral tests for the read-only latest-message cache."""

from __future__ import annotations

from pathlib import Path
from threading import Barrier, Thread

import numpy as np
import pytest

from capture_core.ros_cache import LatestMessageCache
from capture_core.safety import find_ros_write_violations
from capture_core.topics import REQUIRED_TOPICS


def test_required_topics_match_the_task2_contract():
    """Catches a recorder subscribing to a renamed or unsafe Task2 stream."""
    assert REQUIRED_TOPICS == {
        "camera_high": "/camera_f/color/image_raw",
        "camera_left": "/camera_l/color/image_raw",
        "camera_right": "/camera_r/color/image_raw",
        "front_left": "/puppet/joint_left",
        "front_right": "/puppet/joint_right",
        "rear_left": "/task2/teach/rear_left/joint_states",
        "rear_right": "/task2/teach/rear_right/joint_states",
        "policy_left": "/task2/policy/joint_left",
        "policy_right": "/task2/policy/joint_right",
        "coordinator_left": "/master/joint_left",
        "coordinator_right": "/master/joint_right",
        "teach_left": "/task2/teach/rear_left/teach_active",
        "teach_right": "/task2/teach/rear_right/teach_active",
        "handover_mode": "/task2/teach_handover/mode",
        "handover_fault": "/task2/teach_handover/fault",
    }


def test_task5_runtime_never_contains_ros_write_primitives():
    """Catches an accidental ROS output path anywhere in the Task5 package."""
    package = Path(__import__("capture_core").__path__[0])

    assert find_ros_write_violations(package) == []


@pytest.mark.parametrize(
    "unsafe_source",
    [
        "import rospy\nrospy.Publisher('/master/joint_left', object)\n",
        "import rospy\nrospy.ServiceProxy('/robot/stop', object)\n",
        "import rospy\npublisher_factory = rospy.Publisher\npublisher_factory('/master/joint_left', object)\n",
        "import rospy\nros = rospy\nros.ServiceProxy('/robot/stop', object)\n",
        "from rospy import Publisher as factory\npublisher_factory = factory\npublisher_factory('/master/joint_left', object)\n",
        "import rospy as ros\nfirst = ros.Publisher\nsecond = first\nsecond('/master/joint_left', object)\n",
        "import rospy\npublisher = rospy.Publisher('/master/joint_left', object)\npublisher.publish(command)\n",
        "import rospy\npublisher = rospy.Publisher('/master/joint_left', object)\ngetattr(publisher, 'publish')(command)\n",
        "import rospy\ngetattr(rospy, 'Publisher')('/master/joint_left', object)\n",
        "import rospy\ngetattr(rospy, output_name)('/master/joint_left', object)\n",
    ],
)
def test_ros_write_ban_recursively_detects_nested_and_dynamic_violations(
    tmp_path, unsafe_source
):
    """Catches a child package evading the output ban through syntax indirection."""
    nested_file = tmp_path / "nested" / "unsafe.py"
    nested_file.parent.mkdir()
    nested_file.write_text(unsafe_source, encoding="utf-8")

    violations = find_ros_write_violations(tmp_path)

    assert violations
    assert "nested/unsafe.py" in violations[0]


@pytest.mark.parametrize(
    "bare_reference_source",
    [
        "import rospy\n\ndef factory():\n    return rospy.Publisher\n",
        "import rospy\nservices = [rospy.ServiceProxy]\n",
        "import rospy\nfactory = rospy.Publisher if enabled else fallback\n",
        "from rospy import Publisher as publisher_factory\npublisher_factory\n",
        "import rospy\ngetattr(rospy, 'Publisher')\n",
    ],
)
def test_ros_write_ban_rejects_bare_resolved_factory_references(
    tmp_path, bare_reference_source
):
    """Catches ROS output factories escaping through values that are never called."""
    source_file = tmp_path / "bare_reference.py"
    source_file.write_text(bare_reference_source, encoding="utf-8")

    violations = find_ros_write_violations(tmp_path)

    assert len(violations) == 1
    assert "bare_reference.py" in violations[0]


@pytest.mark.parametrize(
    "safe_source",
    [
        "class Report:\n    def publish(self):\n        pass\n\nReport().publish()\n",
        "report = object()\ngetattr(report, 'publish')()\n",
        "controller.publish_command(command)\n",
    ],
)
def test_ros_write_ban_does_not_flag_non_ros_publish_apis(tmp_path, safe_source):
    """Catches a string-based ban rejecting unrelated application APIs."""
    source_file = tmp_path / "safe.py"
    source_file.write_text(safe_source, encoding="utf-8")

    assert find_ros_write_violations(tmp_path) == []


def test_put_copies_values_and_snapshot_cannot_alias_cache_data():
    """Catches callbacks or callers mutating a retained latest ROS message."""
    cache = LatestMessageCache()
    image = np.array([[[1, 2, 3]]], dtype=np.uint8)
    cache.put("camera_high", image, source_stamp=2.0, arrival_stamp=2.1)
    image[0, 0, 0] = 99

    first = cache.snapshot(now=2.15)
    first_image = first.get("camera_high")
    first_image[0, 0, 1] = 88
    second = cache.snapshot(now=2.16)

    assert second.get("camera_high").tolist() == [[[1, 2, 3]]]
    assert second.source_timestamp("camera_high") == 2.0
    assert second.arrival_timestamp("camera_high") == 2.1


@pytest.mark.parametrize(
    ("key", "age", "expected"),
    [
        ("camera_high", 0.20, True),
        ("camera_high", 0.200001, False),
        ("front_left", 0.10, True),
        ("front_left", 0.100001, False),
        ("rear_left", 0.10, True),
        ("rear_left", 0.100001, False),
        ("policy_left", 0.25, True),
        ("policy_left", 0.250001, False),
        ("coordinator_left", 0.25, True),
        ("coordinator_left", 0.250001, False),
        ("handover_mode", 1.0, True),
        ("handover_mode", 1.000001, False),
    ],
)
def test_freshness_is_measured_against_arrival_time(key, age, expected):
    """Catches using a ROS source clock or the wrong stream expiry window."""
    cache = LatestMessageCache()
    cache.put(key, object(), source_stamp=0.0, arrival_stamp=10.0)

    snapshot = cache.snapshot(now=10.0 + age)

    assert snapshot.is_fresh(key) is expected


def test_missing_stream_is_not_fresh_and_has_no_value():
    """Catches a missing topic being treated as a fresh default message."""
    snapshot = LatestMessageCache().snapshot(now=5.0)

    assert snapshot.get("policy_left") is None
    assert snapshot.is_fresh("policy_left") is False
    assert np.isnan(snapshot.source_timestamp("policy_left"))
    assert np.isnan(snapshot.arrival_timestamp("policy_left"))


def test_future_arrival_from_a_concurrent_callback_is_not_fresh():
    """Catches a callback timestamp that is later than the sampler clock as fresh."""
    cache = LatestMessageCache()
    start = Barrier(2)

    def callback() -> None:
        start.wait()
        cache.put("camera_high", object(), source_stamp=10.001, arrival_stamp=10.001)

    worker = Thread(target=callback)
    worker.start()
    start.wait()
    worker.join()

    assert cache.snapshot(now=10.0).is_fresh("camera_high") is False

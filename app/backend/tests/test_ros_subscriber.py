"""Behavioral tests for the read-only production ROS subscriber bridge."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

from capture_core.ros_cache import LatestMessageCache
from capture_core.ros_subscriber import RosBindings, RosSubscriberBridge
from capture_core.topics import REQUIRED_TOPICS


class Image:
    pass


class JointState:
    pass


class String:
    pass


class Bool:
    pass


class FakeSubscription:
    def __init__(self, topic, message_type, callback, queue_size):
        self.topic = topic
        self.message_type = message_type
        self.callback = callback
        self.queue_size = queue_size
        self.unregistered = False

    def unregister(self):
        self.unregistered = True


class FakeRospy:
    def __init__(self, *, initialized: bool):
        self.core = SimpleNamespace(is_initialized=lambda: initialized)
        self.init_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.subscriptions: list[FakeSubscription] = []

    def init_node(self, *args, **kwargs):
        self.init_calls.append((args, kwargs))

    def Subscriber(self, topic, message_type, callback, queue_size=1):
        subscription = FakeSubscription(topic, message_type, callback, queue_size)
        self.subscriptions.append(subscription)
        return subscription


class FakeCvBridge:
    def imgmsg_to_cv2(self, message, desired_encoding):
        assert desired_encoding == "bgr8"
        return np.array(message.image, copy=True)


@dataclass
class Stamp:
    value: float

    def to_sec(self) -> float:
        return self.value


def _bindings(rospy: FakeRospy) -> RosBindings:
    return RosBindings(
        rospy=rospy,
        cv_bridge_factory=FakeCvBridge,
        image_type=Image,
        joint_state_type=JointState,
        string_type=String,
        bool_type=Bool,
    )


def test_bridge_subscribes_to_all_read_only_topics_and_copies_callback_payloads():
    """Catches missing topics, double RGB conversion, aliases, or wrong timestamps."""
    cache = LatestMessageCache()
    rospy = FakeRospy(initialized=True)
    arrivals = iter(float(value) for value in range(10, 30))
    bridge = RosSubscriberBridge(
        cache,
        bindings_loader=lambda: _bindings(rospy),
        monotonic=lambda: next(arrivals),
        master_probe=lambda: True,
        mode_publisher_probe=lambda _uri, _node: True,
        registration_probe=lambda _uri, _node, _topics: True,
    )

    bridge.start()

    assert rospy.init_calls == []
    assert {item.topic for item in rospy.subscriptions} == set(REQUIRED_TOPICS.values())
    assert len(rospy.subscriptions) == 15
    assert all(item.queue_size == 1 for item in rospy.subscriptions)
    callbacks = {item.topic: item.callback for item in rospy.subscriptions}
    bgr = np.array([[[1, 2, 3]]], dtype=np.uint8)
    callbacks[REQUIRED_TOPICS["camera_high"]](
        SimpleNamespace(image=bgr, header=SimpleNamespace(stamp=Stamp(2.5)))
    )
    joint_message = SimpleNamespace(
        position=list(range(7)),
        velocity=list(range(10, 17)),
        effort=list(range(20, 27)),
        header=SimpleNamespace(stamp=Stamp(3.5)),
    )
    callbacks[REQUIRED_TOPICS["front_left"]](joint_message)
    callbacks[REQUIRED_TOPICS["handover_mode"]](SimpleNamespace(data="policy"))
    callbacks[REQUIRED_TOPICS["teach_left"]](SimpleNamespace(data=True))
    bgr[0, 0, 0] = 99
    joint_message.position[0] = 99

    snapshot = cache.snapshot(now=13.0)
    assert snapshot.get("camera_high").tolist() == [[[1, 2, 3]]]
    assert snapshot.source_timestamp("camera_high") == 2.5
    assert snapshot.arrival_timestamp("camera_high") == 10.0
    assert snapshot.get("front_left")["position"].tolist() == list(range(7))
    assert snapshot.source_timestamp("front_left") == 3.5
    assert snapshot.get("handover_mode") == "policy"
    assert snapshot.source_timestamp("handover_mode") == 12.0
    assert snapshot.get("teach_left") is True
    assert bridge.status() == {"state": "ready", "error_code": None}

    bridge.shutdown()
    assert all(item.unregistered for item in rospy.subscriptions)
    assert bridge.status() == {"state": "stopped", "error_code": None}


def test_bridge_initializes_ros_once_when_process_has_no_node():
    """Catches duplicate ROS node initialization or signal-handler takeover."""
    rospy = FakeRospy(initialized=False)
    bridge = RosSubscriberBridge(
        LatestMessageCache(),
        bindings_loader=lambda: _bindings(rospy),
        master_probe=lambda: True,
        mode_publisher_probe=lambda _uri, _node: True,
    )

    bridge.start()

    assert rospy.init_calls == [
        (("capture_core_recorder",), {"anonymous": False, "disable_signals": True})
    ]


def test_bridge_start_failure_is_reported_without_partial_subscriptions():
    """Catches ROS import/start failures crashing the API process or leaking handles."""

    def fail_bindings():
        raise ModuleNotFoundError("rospy unavailable at /private/ros")

    bridge = RosSubscriberBridge(LatestMessageCache(), bindings_loader=fail_bindings)

    bridge.start()

    assert bridge.status() == {"state": "not_ready", "error_code": "ros_unavailable"}
    bridge.shutdown()


def test_bridge_fails_fast_before_init_when_ros_master_is_unreachable():
    """Catches rospy.init_node blocking API startup without a running roscore."""
    rospy = FakeRospy(initialized=False)
    bridge = RosSubscriberBridge(
        LatestMessageCache(),
        bindings_loader=lambda: _bindings(rospy),
        master_probe=lambda: False,
    )

    bridge.start()

    assert rospy.init_calls == []
    assert rospy.subscriptions == []
    assert bridge.status() == {
        "state": "not_ready",
        "error_code": "ros_master_unavailable",
    }


def test_bridge_detects_master_uri_change_after_start():
    cache = LatestMessageCache()
    rospy = FakeRospy(initialized=True)
    uri = ["http://master-a:11311"]
    bridge = RosSubscriberBridge(
        cache,
        bindings_loader=lambda: _bindings(rospy),
        master_probe=lambda: True,
        mode_publisher_probe=lambda _uri, _node: True,
        master_uri_getter=lambda: uri[0],
        registration_probe=lambda _uri, _node, _topics: True,
    )
    bridge.start()
    assert bridge.status()["state"] == "ready"
    uri[0] = "http://master-b:11311"
    assert bridge.status()["error_code"] == "ros_master_changed"


def test_bridge_detects_missing_current_master_registration():
    cache = LatestMessageCache()
    rospy = FakeRospy(initialized=True)
    registered = [True]
    bridge = RosSubscriberBridge(
        cache,
        bindings_loader=lambda: _bindings(rospy),
        master_probe=lambda: True,
        mode_publisher_probe=lambda _uri, _node: True,
        master_uri_getter=lambda: "http://master:11311",
        registration_probe=lambda _uri, node, topics: registered[0]
        and node == "/capture_core_recorder"
        and len(topics) == len(REQUIRED_TOPICS),
    )
    bridge.start()
    assert bridge.status()["state"] == "ready"
    registered[0] = False
    assert bridge.status()["error_code"] == "ros_node_unregistered"


def test_bridge_rechecks_master_reachability_in_status():
    cache = LatestMessageCache()
    rospy = FakeRospy(initialized=True)
    reachable = [True]
    bridge = RosSubscriberBridge(
        cache,
        bindings_loader=lambda: _bindings(rospy),
        master_probe=lambda: reachable[0],
        mode_publisher_probe=lambda _uri, _node: True,
        master_uri_getter=lambda: "http://master:11311",
        registration_probe=lambda _uri, _node, _topics: True,
    )
    bridge.start()
    reachable[0] = False
    assert bridge.status()["error_code"] == "ros_master_unavailable"

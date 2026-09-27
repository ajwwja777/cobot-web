from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


NODE_PATH = (Path(__file__).resolve().parents[3] / "integrations/legacy_control"
             / "task2_handover/task2_handover_node.py")
SCRIPT_DIR = NODE_PATH.parent
ARM_TOPICS = {
    "/master/joint_left",
    "/master/joint_right",
    "/task2/rear_left/joint_cmd",
    "/task2/rear_right/joint_cmd",
}
NAMES = ["joint{}".format(index) for index in range(7)]


class Stamp:
    def __init__(self, value=0.0):
        self.value = float(value)

    def to_sec(self):
        return self.value


class JointState:
    def __init__(self):
        self.header = SimpleNamespace(stamp=Stamp())
        self.name = []
        self.position = []
        self.velocity = []
        self.effort = []


class String:
    def __init__(self, data=""):
        self.data = data


class Bool:
    def __init__(self, data=False):
        self.data = data


class SetBool:
    pass


class SetBoolRequest:
    def __init__(self, data=False):
        self.data = data


class SetBoolResponse:
    def __init__(self, success=False, message=""):
        self.success = success
        self.message = message


class Trigger:
    pass


class TriggerResponse:
    def __init__(self, success=False, message=""):
        self.success = success
        self.message = message


class Clock:
    def __init__(self, wall=10.0, monotonic=50.0):
        self.wall = float(wall)
        self.monotonic = float(monotonic)

    def advance(self, seconds):
        self.wall += float(seconds)
        self.monotonic += float(seconds)


class FakePublisher:
    def __init__(self, topic, events, hooks):
        self.topic = topic
        self.events = events
        self.hooks = hooks
        self.messages = []

    def publish(self, message):
        self.messages.append(message)
        self.events.append(("publish", self.topic))
        hook = self.hooks.get(self.topic)
        if hook is not None:
            hook(message)


class FakeRos:
    def __init__(self, clock, params=None):
        self.clock = clock
        self.params = dict(params or {})
        self.events = []
        self.hooks = {}
        self.publishers = {}
        self.subscribers = {}
        self.services = {}
        self.errors = []
        outer = self

        class TimeApi:
            @staticmethod
            def now():
                return Stamp(outer.clock.wall)

        self.Time = TimeApi

    def get_param(self, name, default=None):
        return self.params.get(name, default)

    def set_param(self, name, value):
        self.params[name] = value

    def Publisher(self, topic, _message_type, queue_size=1, latch=False):
        publisher = FakePublisher(topic, self.events, self.hooks)
        self.publishers[topic] = publisher
        return publisher

    def Subscriber(self, topic, _message_type, callback, queue_size=1):
        self.subscribers[topic] = callback
        return callback

    def Service(self, topic, _service_type, callback):
        self.services[topic] = callback
        return callback

    def loginfo(self, *_args):
        pass

    def logwarn(self, *_args):
        pass

    def logerr(self, message, *args):
        self.errors.append(message % args if args else message)


class ServiceFactory:
    def __init__(self, events):
        self.events = events
        self.behaviors = {}

    def __call__(self, name, _service_type):
        # rospy service proxies for argument-less types such as Trigger are
        # called with no arguments, so the fake must accept that too.
        def invoke(request=None):
            value = getattr(request, "data", None)
            self.events.append(("service", name, value))
            behavior = self.behaviors.get(name)
            if isinstance(behavior, Exception):
                raise behavior
            if behavior is None:
                return SimpleNamespace(success=True, message="ok")
            return behavior(request)

        return invoke


def install_import_stubs():
    rospy = ModuleType("rospy")
    sensor_msgs = ModuleType("sensor_msgs")
    sensor_msgs_msg = ModuleType("sensor_msgs.msg")
    sensor_msgs_msg.JointState = JointState
    std_msgs = ModuleType("std_msgs")
    std_msgs_msg = ModuleType("std_msgs.msg")
    std_msgs_msg.String = String
    std_msgs_msg.Bool = Bool
    std_srvs = ModuleType("std_srvs")
    std_srvs_srv = ModuleType("std_srvs.srv")
    std_srvs_srv.SetBool = SetBool
    std_srvs_srv.SetBoolRequest = SetBoolRequest
    std_srvs_srv.SetBoolResponse = SetBoolResponse
    std_srvs_srv.Trigger = Trigger
    std_srvs_srv.TriggerResponse = TriggerResponse
    sys.modules.update(
        {
            "rospy": rospy,
            "sensor_msgs": sensor_msgs,
            "sensor_msgs.msg": sensor_msgs_msg,
            "std_msgs": std_msgs,
            "std_msgs.msg": std_msgs_msg,
            "std_srvs": std_srvs,
            "std_srvs.srv": std_srvs_srv,
        }
    )


def load_node_module():
    if not NODE_PATH.is_file():
        raise AssertionError("missing production node: {}".format(NODE_PATH))
    install_import_stubs()
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        spec = importlib.util.spec_from_file_location("task2_handover_node", str(NODE_PATH))
        module = importlib.util.module_from_spec(spec)
        if spec.loader is None:
            raise AssertionError("unable to load {}".format(NODE_PATH))
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT_DIR))


def joint(stamp, positions=None, names=None):
    message = JointState()
    message.header.stamp = Stamp(stamp)
    message.name = list(NAMES if names is None else names)
    message.position = list([0.0] * 7 if positions is None else positions)
    return message


class HandoverFixture:
    def __init__(self, module, **params):
        defaults = {
            "~command_max_age_sec": 0.25,
            "~pair_max_skew_sec": 0.02,
            "~joint_sync_tolerance_rad": 0.05,
            "~gripper_sync_tolerance_m": 0.015,
            "~feedback_max_age_sec": 0.10,
            "~role_timeout_sec": 0.01,
            "~motion_ready_timeout_sec": 0.01,
            "~master_feedback_timeout_sec": 0.01,
        }
        defaults.update({"~" + key if not key.startswith("~") else key: value for key, value in params.items()})
        self.module = module
        self.clock = Clock()
        self.ros = FakeRos(self.clock, defaults)
        self.services = ServiceFactory(self.ros.events)
        self.node = module.Task2HandoverNode(
            ros_api=self.ros,
            service_factory=self.services,
            wall_clock=lambda: self.clock.wall,
            monotonic_clock=lambda: self.clock.monotonic,
        )
        self.install_successful_service_side_effects()
        self.install_motion_ready_hooks()
        self.ros.events.clear()

    def install_successful_service_side_effects(self):
        def role_behavior(side, request):
            self.clock.advance(0.001)
            callback = getattr(self.node, "rear_{}_role_callback".format(side))
            callback(String("master" if request.data else "slave"))
            if request.data:
                getattr(self.node, "rear_{}_master_callback".format(side))(
                    joint(self.clock.wall)
                )
            return SimpleNamespace(success=True, message="ok")

        self.services.behaviors["/task2/rear_left/set_master"] = (
            lambda request: role_behavior("left", request)
        )
        self.services.behaviors["/task2/rear_right/set_master"] = (
            lambda request: role_behavior("right", request)
        )

    def install_motion_ready_hooks(self):
        received = set()

        def mark(side):
            def hook(_message):
                received.add(side)
                if received == {"left", "right"}:
                    self.node.rear_left_motion_ready_callback(Bool(True))
                    self.node.rear_right_motion_ready_callback(Bool(True))

            return hook

        self.ros.hooks["/task2/rear_left/joint_cmd"] = mark("left")
        self.ros.hooks["/task2/rear_right/joint_cmd"] = mark("right")

    def publish_synchronized_feedback(self, rear_offset=0.0):
        front = joint(self.clock.wall)
        rear = joint(
            self.clock.wall,
            positions=[rear_offset] * 6 + [0.0],
        )
        self.node.front_left_feedback_callback(front)
        self.node.front_right_feedback_callback(front)
        self.node.rear_left_feedback_callback(rear)
        self.node.rear_right_feedback_callback(rear)

    def arm_events(self):
        return [event for event in self.ros.events if len(event) >= 2 and event[1] in ARM_TOPICS]


class HandoverNodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_node_module()

    def test_paused_state_never_routes_policy_messages(self):
        fixture = HandoverFixture(self.module)
        fixture.node.policy_left_callback(joint(fixture.clock.wall))
        fixture.node.policy_right_callback(joint(fixture.clock.wall))
        self.assertEqual(fixture.node.mode.value, "paused")
        self.assertEqual(fixture.arm_events(), [])

    def test_request_policy_holds_rears_then_fresh_pair_opens_all_four_outputs(self):
        fixture = HandoverFixture(self.module)
        fixture.publish_synchronized_feedback()
        fixture.ros.events.clear()

        response = fixture.node.handle_request_policy(SimpleNamespace())

        self.assertTrue(response.success)
        self.assertEqual(fixture.node.mode.value, "resuming")
        events = fixture.ros.events
        pause_index = events.index(("service", "/task2/policy/set_paused", True))
        left_slave_index = events.index(("service", "/task2/rear_left/set_master", False))
        right_slave_index = events.index(("service", "/task2/rear_right/set_master", False))
        left_hold_index = events.index(("publish", "/task2/rear_left/joint_cmd"))
        right_hold_index = events.index(("publish", "/task2/rear_right/joint_cmd"))
        resume_index = events.index(("service", "/task2/policy/set_paused", False))
        self.assertLess(pause_index, left_slave_index)
        self.assertLess(left_slave_index, right_slave_index)
        self.assertLess(right_slave_index, left_hold_index)
        self.assertLess(left_hold_index, right_hold_index)
        self.assertLess(right_hold_index, resume_index)

        fixture.ros.events.clear()
        fixture.clock.advance(0.01)
        fixture.node.policy_left_callback(joint(fixture.clock.wall))
        self.assertEqual(fixture.arm_events(), [])
        fixture.node.policy_right_callback(joint(fixture.clock.wall))
        self.assertEqual(
            fixture.ros.events[-4:],
            [
                ("publish", "/master/joint_left"),
                ("publish", "/master/joint_right"),
                ("publish", "/task2/rear_left/joint_cmd"),
                ("publish", "/task2/rear_right/joint_cmd"),
            ],
        )
        self.assertEqual(fixture.node.mode.value, "policy")

    def test_pre_resume_or_invalid_policy_pair_faults_without_arm_output(self):
        for bad_message in (
            lambda fixture: joint(fixture.node.resume_cutoff_wall - 0.001),
            lambda fixture: joint(fixture.clock.wall, names=NAMES[::-1]),
            lambda fixture: joint(fixture.clock.wall, positions=[float("nan")] + [0.0] * 6),
            lambda fixture: joint(fixture.clock.wall - 1.0),
        ):
            fixture = HandoverFixture(self.module)
            fixture.publish_synchronized_feedback()
            self.assertTrue(fixture.node.handle_request_policy(SimpleNamespace()).success)
            fixture.ros.events.clear()
            fixture.node.policy_left_callback(bad_message(fixture))
            self.assertEqual(fixture.node.mode.value, "fault")
            self.assertEqual(fixture.arm_events(), [])

    def test_policy_request_faults_on_missing_or_mismatched_feedback(self):
        missing = HandoverFixture(self.module)
        response = missing.node.handle_request_policy(SimpleNamespace())
        self.assertFalse(response.success)
        self.assertEqual(missing.node.mode.value, "fault")
        self.assertEqual(missing.arm_events(), [])

        mismatch = HandoverFixture(self.module)
        mismatch.publish_synchronized_feedback(rear_offset=0.051)
        response = mismatch.node.handle_request_policy(SimpleNamespace())
        self.assertFalse(response.success)
        self.assertEqual(mismatch.node.mode.value, "fault")
        self.assertEqual(mismatch.arm_events(), [])

    def test_policy_pause_or_role_failure_faults_before_any_arm_output(self):
        for service_name in (
            "/task2/policy/set_paused",
            "/task2/rear_left/set_master",
            "/task2/rear_right/set_master",
        ):
            fixture = HandoverFixture(self.module)
            fixture.publish_synchronized_feedback()
            fixture.services.behaviors[service_name] = RuntimeError("service down")
            response = fixture.node.handle_request_policy(SimpleNamespace())
            self.assertFalse(response.success)
            self.assertEqual(fixture.node.mode.value, "fault")
            self.assertEqual(fixture.arm_events(), [])

    def test_motion_ready_timeout_faults_after_rear_hold_and_before_resume(self):
        fixture = HandoverFixture(self.module, motion_ready_timeout_sec=0.002)
        fixture.publish_synchronized_feedback()
        fixture.ros.hooks.clear()
        # Make the injected monotonic deadline advance on every predicate read.
        current = [fixture.clock.monotonic]

        def ticking_monotonic():
            current[0] += 0.001
            return current[0]

        fixture.node.monotonic_clock = ticking_monotonic
        response = fixture.node.handle_request_policy(SimpleNamespace())
        self.assertFalse(response.success)
        self.assertEqual(fixture.node.mode.value, "fault")
        self.assertNotIn(
            ("service", "/task2/policy/set_paused", False), fixture.ros.events
        )
        self.assertEqual(
            fixture.arm_events(),
            [
                ("publish", "/task2/rear_left/joint_cmd"),
                ("publish", "/task2/rear_right/joint_cmd"),
            ],
        )

    def test_manual_request_pauses_then_switches_roles_and_routes_only_front_pair(self):
        fixture = HandoverFixture(self.module)
        fixture.publish_synchronized_feedback()
        fixture.ros.events.clear()

        response = fixture.node.handle_request_manual(SimpleNamespace())

        self.assertTrue(response.success)
        self.assertEqual(fixture.node.mode.value, "manual")
        self.assertTrue(fixture.node.policy_pause_confirmed)
        mode_index = fixture.ros.events.index(("publish", "/task2/handover/mode"))
        pause_index = fixture.ros.events.index(("service", "/task2/policy/set_paused", True))
        left_index = fixture.ros.events.index(("service", "/task2/rear_left/set_master", True))
        right_index = fixture.ros.events.index(("service", "/task2/rear_right/set_master", True))
        self.assertLess(mode_index, pause_index)
        self.assertLess(pause_index, left_index)
        self.assertLess(left_index, right_index)

        fixture.ros.events.clear()
        fixture.clock.advance(0.01)
        fixture.node.rear_left_master_callback(joint(fixture.clock.wall))
        self.assertEqual(fixture.arm_events(), [])
        fixture.node.rear_right_master_callback(joint(fixture.clock.wall))
        self.assertEqual(
            fixture.arm_events(),
            [
                ("publish", "/master/joint_left"),
                ("publish", "/master/joint_right"),
            ],
        )

    def test_unconfirmed_manual_pause_is_allowed_but_policy_retry_precedes_slave(self):
        fixture = HandoverFixture(self.module)
        fixture.publish_synchronized_feedback()
        fixture.services.behaviors["/task2/policy/set_paused"] = RuntimeError("timeout")

        response = fixture.node.handle_request_manual(SimpleNamespace())

        self.assertTrue(response.success)
        self.assertEqual(fixture.node.mode.value, "manual")
        self.assertFalse(fixture.node.policy_pause_confirmed)

        fixture.ros.events.clear()
        response = fixture.node.handle_request_policy(SimpleNamespace())
        self.assertFalse(response.success)
        self.assertEqual(fixture.node.mode.value, "fault")
        self.assertEqual(
            fixture.ros.events[1],
            ("service", "/task2/policy/set_paused", True),
        )
        self.assertFalse(
            any(
                event[0] == "service" and "rear_" in event[1]
                for event in fixture.ros.events
            )
        )

    def test_reset_fault_only_returns_to_paused_without_external_calls(self):
        fixture = HandoverFixture(self.module)
        fixture.node._latch_fault("test fault")
        fixture.ros.events.clear()
        response = fixture.node.handle_reset_fault(SimpleNamespace())
        self.assertTrue(response.success)
        self.assertEqual(fixture.node.mode.value, "paused")
        self.assertFalse(any(event[0] == "service" for event in fixture.ros.events))
        self.assertEqual(fixture.arm_events(), [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Fail-closed ROS1 coordinator for Task2 policy/manual handover."""

import math
import threading
import time
from typing import Any, Callable, Dict, Optional

import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, SetBoolRequest, Trigger, TriggerResponse

from task2_handover_core import (
    CommandPair,
    HandoverState,
    Mode,
    PairBuffer,
    PairingError,
    TransitionError,
    ValidatedJoint,
    ValidationError,
    arms_are_synchronized,
    validate_joint,
)


POLICY_LEFT_TOPIC = "/task2/policy/joint_left"
POLICY_RIGHT_TOPIC = "/task2/policy/joint_right"
FRONT_LEFT_COMMAND_TOPIC = "/master/joint_left"
FRONT_RIGHT_COMMAND_TOPIC = "/master/joint_right"
REAR_LEFT_COMMAND_TOPIC = "/task2/rear_left/joint_cmd"
REAR_RIGHT_COMMAND_TOPIC = "/task2/rear_right/joint_cmd"


class HandoverError(RuntimeError):
    """One coordinated handover precondition or external operation failed."""


class Task2HandoverNode:
    """Own the only software command path to both front and rear arm pairs."""

    def __init__(
        self,
        ros_api: Any = rospy,
        service_factory: Optional[Callable] = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ros = ros_api
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock
        self.command_max_age_sec = self._positive_param(
            "~command_max_age_sec", 0.25
        )
        self.pair_max_skew_sec = self._positive_param(
            "~pair_max_skew_sec", 0.02
        )
        self.joint_sync_tolerance_rad = self._positive_param(
            "~joint_sync_tolerance_rad", 0.05
        )
        self.gripper_sync_tolerance_m = self._positive_param(
            "~gripper_sync_tolerance_m", 0.015
        )
        self.feedback_max_age_sec = self._positive_param(
            "~feedback_max_age_sec", 0.10
        )
        self.role_timeout_sec = self._positive_param("~role_timeout_sec", 1.0)
        self.motion_ready_timeout_sec = self._positive_param(
            "~motion_ready_timeout_sec", 5.0
        )
        self.master_feedback_timeout_sec = self._positive_param(
            "~master_feedback_timeout_sec", 1.0
        )

        # One lock is the ownership boundary for state, buffers and routing.
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self.state = HandoverState()
        self.policy_pair = PairBuffer(
            self.pair_max_skew_sec, self.command_max_age_sec
        )
        self.manual_pair = PairBuffer(
            self.pair_max_skew_sec, self.command_max_age_sec
        )
        self._feedback: Dict[str, ValidatedJoint] = {}
        self._rear_roles: Dict[str, Optional[str]] = {"left": None, "right": None}
        self._rear_role_arrival: Dict[str, Optional[float]] = {
            "left": None,
            "right": None,
        }
        self._rear_motion_ready = {"left": False, "right": False}
        self._rear_master: Dict[str, ValidatedJoint] = {}
        self.resume_cutoff_wall = float("inf")

        # Publishers are deliberately constructed before every subscriber and
        # service, so no callback can race a partially initialized output path.
        self.front_left_pub = self.ros.Publisher(
            FRONT_LEFT_COMMAND_TOPIC, JointState, queue_size=1
        )
        self.front_right_pub = self.ros.Publisher(
            FRONT_RIGHT_COMMAND_TOPIC, JointState, queue_size=1
        )
        self.rear_left_pub = self.ros.Publisher(
            REAR_LEFT_COMMAND_TOPIC, JointState, queue_size=1
        )
        self.rear_right_pub = self.ros.Publisher(
            REAR_RIGHT_COMMAND_TOPIC, JointState, queue_size=1
        )
        self.mode_pub = self.ros.Publisher(
            "/task2/handover/mode", String, queue_size=1, latch=True
        )
        self.fault_pub = self.ros.Publisher(
            "/task2/handover/fault", String, queue_size=1, latch=True
        )

        proxy_factory = service_factory or self.ros.ServiceProxy
        self.rear_left_role_service = proxy_factory(
            "/task2/rear_left/set_master", SetBool
        )
        self.rear_right_role_service = proxy_factory(
            "/task2/rear_right/set_master", SetBool
        )
        self.policy_pause_service = proxy_factory(
            "/task2/policy/set_paused", SetBool
        )

        self._subscribers = [
            self.ros.Subscriber(
                POLICY_LEFT_TOPIC,
                JointState,
                self.policy_left_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                POLICY_RIGHT_TOPIC,
                JointState,
                self.policy_right_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/task2/rear_left/master_joint",
                JointState,
                self.rear_left_master_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/task2/rear_right/master_joint",
                JointState,
                self.rear_right_master_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/puppet/joint_left",
                JointState,
                self.front_left_feedback_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/puppet/joint_right",
                JointState,
                self.front_right_feedback_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/task2/rear_left/joint_states",
                JointState,
                self.rear_left_feedback_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/task2/rear_right/joint_states",
                JointState,
                self.rear_right_feedback_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/task2/rear_left/role",
                String,
                self.rear_left_role_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/task2/rear_right/role",
                String,
                self.rear_right_role_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/task2/rear_left/motion_ready",
                Bool,
                self.rear_left_motion_ready_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                "/task2/rear_right/motion_ready",
                Bool,
                self.rear_right_motion_ready_callback,
                queue_size=1,
            ),
        ]
        self._services = [
            self.ros.Service(
                "/task2/handover/request_manual",
                Trigger,
                self.handle_request_manual,
            ),
            self.ros.Service(
                "/task2/handover/request_policy",
                Trigger,
                self.handle_request_policy,
            ),
            self.ros.Service(
                "/task2/handover/reset_fault", Trigger, self.handle_reset_fault
            ),
        ]
        self._publish_mode()
        self.fault_pub.publish(String(data=""))

    @property
    def mode(self) -> Mode:
        return self.state.mode

    @property
    def policy_pause_confirmed(self) -> bool:
        return self.state.policy_pause_confirmed

    def _positive_param(self, name: str, default: float) -> float:
        value = float(self.ros.get_param(name, default))
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("{} must be finite and greater than zero".format(name))
        return value

    def _publish_mode(self) -> None:
        self.mode_pub.publish(String(data=self.mode.value))

    def _close_gate(self) -> None:
        self.policy_pair.clear()
        self.manual_pair.clear()

    def _latch_fault(self, reason: str) -> None:
        with self._condition:
            self._close_gate()
            self.state.fault(reason)
            self._publish_mode()
            self.fault_pub.publish(String(data=str(reason)))
            self._condition.notify_all()
        self.ros.logerr("Task2 handover fault: %s", reason)

    def _service_bool(self, proxy: Callable, value: bool, label: str) -> None:
        try:
            response = proxy(SetBoolRequest(data=bool(value)))
        except Exception as exc:
            raise HandoverError("{} service failed: {}".format(label, exc)) from exc
        if not bool(getattr(response, "success", False)):
            raise HandoverError(
                "{} service rejected request: {}".format(
                    label, getattr(response, "message", "no message")
                )
            )

    def _wait_until(self, predicate: Callable[[], bool], timeout_sec: float) -> bool:
        deadline = self.monotonic_clock() + timeout_sec
        with self._condition:
            while not predicate():
                remaining = deadline - self.monotonic_clock()
                if remaining <= 0.0:
                    return False
                self._condition.wait(timeout=min(0.01, remaining))
            return True

    def _validated_message(self, message: JointState) -> ValidatedJoint:
        arrival = float(self.monotonic_clock())
        stamp = getattr(getattr(message, "header", None), "stamp", None)
        if stamp is None or not hasattr(stamp, "to_sec"):
            raise ValidationError("JointState header stamp is missing")
        return validate_joint(
            names=message.name,
            positions=message.position,
            stamp_sec=float(stamp.to_sec()),
            arrival_monotonic=arrival,
            now_wall=float(self.wall_clock()),
            now_monotonic=float(self.monotonic_clock()),
            max_age_sec=self.command_max_age_sec,
        )

    def _message_from_validated(
        self, joint: ValidatedJoint, stamp_sec: Optional[float] = None
    ) -> JointState:
        message = JointState()
        if stamp_sec is None:
            stamp_sec = joint.stamp_sec
        # rospy.Time.from_sec is unavailable in small offline fakes.  Assigning
        # now is correct for holds; routed commands retain their source stamp.
        if abs(float(stamp_sec) - float(self.wall_clock())) <= 1e-9:
            message.header.stamp = self.ros.Time.now()
        else:
            try:
                message.header.stamp = self.ros.Time.from_sec(float(stamp_sec))
            except AttributeError:
                message.header.stamp = type(self.ros.Time.now())(float(stamp_sec))
        message.name = ["joint{}".format(index) for index in range(7)]
        message.position = list(joint.positions)
        message.velocity = [0.0] * 7
        message.effort = [0.0] * 7
        return message

    def _feedback_callback(self, key: str, message: JointState) -> None:
        try:
            validated = self._validated_message(message)
        except Exception as exc:
            self._latch_fault("invalid {} feedback: {}".format(key, exc))
            return
        with self._condition:
            self._feedback[key] = validated
            self._condition.notify_all()

    def front_left_feedback_callback(self, message: JointState) -> None:
        self._feedback_callback("front_left", message)

    def front_right_feedback_callback(self, message: JointState) -> None:
        self._feedback_callback("front_right", message)

    def rear_left_feedback_callback(self, message: JointState) -> None:
        self._feedback_callback("rear_left", message)

    def rear_right_feedback_callback(self, message: JointState) -> None:
        self._feedback_callback("rear_right", message)

    def _role_callback(self, side: str, message: String) -> None:
        role = str(message.data).strip().lower()
        if role not in ("master", "slave", "fault"):
            self._latch_fault("invalid rear {} role: {}".format(side, role))
            return
        if role == "fault":
            self._latch_fault("rear {} driver reported fault".format(side))
            return
        with self._condition:
            self._rear_roles[side] = role
            self._rear_role_arrival[side] = float(self.monotonic_clock())
            self._condition.notify_all()

    def rear_left_role_callback(self, message: String) -> None:
        self._role_callback("left", message)

    def rear_right_role_callback(self, message: String) -> None:
        self._role_callback("right", message)

    def _motion_ready_callback(self, side: str, message: Bool) -> None:
        with self._condition:
            self._rear_motion_ready[side] = bool(message.data)
            self._condition.notify_all()

    def rear_left_motion_ready_callback(self, message: Bool) -> None:
        self._motion_ready_callback("left", message)

    def rear_right_motion_ready_callback(self, message: Bool) -> None:
        self._motion_ready_callback("right", message)

    def _master_callback(self, side: str, message: JointState) -> None:
        with self._lock:
            if self.mode not in (Mode.TO_MANUAL, Mode.MANUAL):
                return
            try:
                validated = self._validated_message(message)
                self._rear_master[side] = validated
                self._condition.notify_all()
                if self.mode is not Mode.MANUAL:
                    return
                pair = self.manual_pair.offer(
                    side, validated, now_monotonic=float(self.monotonic_clock())
                )
                if pair is not None:
                    self._publish_front_pair(pair)
            except Exception as exc:
                self._latch_fault("invalid manual {} command: {}".format(side, exc))

    def rear_left_master_callback(self, message: JointState) -> None:
        self._master_callback("left", message)

    def rear_right_master_callback(self, message: JointState) -> None:
        self._master_callback("right", message)

    def _policy_callback(self, side: str, message: JointState) -> None:
        with self._lock:
            if self.mode not in (Mode.RESUMING, Mode.POLICY):
                return
            try:
                validated = self._validated_message(message)
                if (
                    self.mode is Mode.RESUMING
                    and validated.stamp_sec < self.resume_cutoff_wall - 1e-9
                ):
                    raise ValidationError("policy command predates fresh resume")
                pair = self.policy_pair.offer(
                    side, validated, now_monotonic=float(self.monotonic_clock())
                )
                if pair is None:
                    return
                if self.mode is Mode.RESUMING:
                    self.state.complete_policy()
                    self._publish_mode()
                self._publish_four_arm_pair(pair)
            except Exception as exc:
                self._latch_fault("invalid policy {} command: {}".format(side, exc))

    def policy_left_callback(self, message: JointState) -> None:
        self._policy_callback("left", message)

    def policy_right_callback(self, message: JointState) -> None:
        self._policy_callback("right", message)

    def _publish_front_pair(self, pair: CommandPair) -> None:
        self.front_left_pub.publish(self._message_from_validated(pair.left))
        self.front_right_pub.publish(self._message_from_validated(pair.right))

    def _publish_four_arm_pair(self, pair: CommandPair) -> None:
        left = self._message_from_validated(pair.left)
        right = self._message_from_validated(pair.right)
        self.front_left_pub.publish(left)
        self.front_right_pub.publish(right)
        self.rear_left_pub.publish(left)
        self.rear_right_pub.publish(right)

    def _require_synchronized(self) -> None:
        with self._lock:
            required = ("front_left", "front_right", "rear_left", "rear_right")
            if any(key not in self._feedback for key in required):
                raise HandoverError("four-arm feedback is incomplete")
            if not arms_are_synchronized(
                self._feedback["front_left"],
                self._feedback["front_right"],
                self._feedback["rear_left"],
                self._feedback["rear_right"],
                now_monotonic=float(self.monotonic_clock()),
                joint_tolerance_rad=self.joint_sync_tolerance_rad,
                gripper_tolerance_m=self.gripper_sync_tolerance_m,
                max_feedback_age_sec=self.feedback_max_age_sec,
            ):
                raise HandoverError("front/rear arms are not synchronized or fresh")

    def _prepare_role_wait(self) -> float:
        with self._condition:
            cutoff = float(self.monotonic_clock())
            self._rear_roles = {"left": None, "right": None}
            self._rear_role_arrival = {"left": None, "right": None}
            return cutoff

    def _roles_confirmed(self, role: str, cutoff: float) -> bool:
        return all(
            self._rear_roles[side] == role
            and self._rear_role_arrival[side] is not None
            and self._rear_role_arrival[side] >= cutoff
            for side in ("left", "right")
        )

    def _request_rear_roles(self, master: bool) -> float:
        cutoff = self._prepare_role_wait()
        self._service_bool(
            self.rear_left_role_service, master, "rear-left role"
        )
        self._service_bool(
            self.rear_right_role_service, master, "rear-right role"
        )
        expected = "master" if master else "slave"
        if not self._wait_until(
            lambda: self._roles_confirmed(expected, cutoff), self.role_timeout_sec
        ):
            raise HandoverError("rear {} role feedback timed out".format(expected))
        return cutoff

    def _publish_rear_hold(self) -> None:
        with self._lock:
            left = self._feedback["front_left"]
            right = self._feedback["front_right"]
            stamp = float(self.wall_clock())
            self.rear_left_pub.publish(self._message_from_validated(left, stamp))
            self.rear_right_pub.publish(self._message_from_validated(right, stamp))

    def handle_request_manual(self, _request: Any) -> TriggerResponse:
        with self._lock:
            try:
                self.state.begin_manual()
                self._close_gate()
                self._rear_master.clear()
                self._publish_mode()
            except Exception as exc:
                self._latch_fault("manual request rejected: {}".format(exc))
                return TriggerResponse(success=False, message=str(exc))

        pause_confirmed = True
        try:
            self._service_bool(self.policy_pause_service, True, "policy pause")
        except HandoverError as exc:
            pause_confirmed = False
            self.ros.logwarn("Task2 continuing manual rescue without pause ack: %s", exc)

        try:
            self._require_synchronized()
            role_cutoff = self._request_rear_roles(master=True)
            if not self._wait_until(
                lambda: all(
                    side in self._rear_master
                    and self._rear_master[side].arrival_monotonic >= role_cutoff
                    for side in ("left", "right")
                ),
                self.master_feedback_timeout_sec,
            ):
                raise HandoverError("fresh rear master feedback timed out")
            self._require_synchronized()
            with self._lock:
                self.state.complete_manual(pause_confirmed)
                self._publish_mode()
            return TriggerResponse(success=True, message="manual control active")
        except Exception as exc:
            self._latch_fault("manual handover failed: {}".format(exc))
            return TriggerResponse(success=False, message=str(exc))

    def handle_request_policy(self, _request: Any) -> TriggerResponse:
        with self._lock:
            try:
                self.state.begin_policy()
                self._close_gate()
                self._publish_mode()
                self._rear_motion_ready = {"left": False, "right": False}
            except Exception as exc:
                self._latch_fault("policy request rejected: {}".format(exc))
                return TriggerResponse(success=False, message=str(exc))

        try:
            # Always reconfirm pause.  This makes S safe even if M's pause
            # acknowledgement was missing or the model process restarted.
            self._service_bool(self.policy_pause_service, True, "policy pause")
            self._require_synchronized()
            self._request_rear_roles(master=False)
            self._require_synchronized()
            self._publish_rear_hold()
            if not self._wait_until(
                lambda: self._rear_motion_ready["left"]
                and self._rear_motion_ready["right"],
                self.motion_ready_timeout_sec,
            ):
                raise HandoverError("rear motion-ready acknowledgement timed out")
            self._service_bool(self.policy_pause_service, False, "policy fresh resume")
            with self._lock:
                self.resume_cutoff_wall = float(self.wall_clock())
                self.state.begin_resuming()
                self.policy_pair.clear()
                self._publish_mode()
            return TriggerResponse(success=True, message="waiting for fresh policy pair")
        except Exception as exc:
            self._latch_fault("policy handover failed: {}".format(exc))
            return TriggerResponse(success=False, message=str(exc))

    def handle_reset_fault(self, _request: Any) -> TriggerResponse:
        with self._lock:
            try:
                self.state.reset_fault()
                self._close_gate()
                self.resume_cutoff_wall = float("inf")
                self.fault_pub.publish(String(data=""))
                self._publish_mode()
                return TriggerResponse(success=True, message="software fault reset; paused")
            except TransitionError as exc:
                return TriggerResponse(success=False, message=str(exc))


def main() -> None:
    rospy.init_node("task2_handover_node", anonymous=False)
    Task2HandoverNode()
    rospy.spin()


if __name__ == "__main__":
    main()

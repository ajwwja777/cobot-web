#!/usr/bin/env python3
"""Fail-closed ROS1 coordinator for Task2 handover via the physical teach button.

This node is the only software publisher of commands to the four controlled
arms.  In POLICY it copies every policy pair to both the front and the rear
arms so the operator's grip position always matches the front arms.  In MANUAL
it stops policy routing entirely and forwards the rear arms' measured joints,
produced by the operator dragging them in physical teach mode, to the front
arms.

Role switching does not appear anywhere: on firmware S-V1.7-3 a rear arm that
is sent MasterSlaveConfig(0xFA) stops all CAN output and only becomes a real
teaching-input arm after a power cycle, which a live handover cannot do.
"""

import math
import threading
import time
from typing import Any, Callable, Dict, Optional

import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, SetBoolRequest, Trigger, TriggerResponse

from task2_teach_handover_core import (
    CommandPair,
    PairBuffer,
    TeachHandoverState,
    TeachMode,
    TeachSideTracker,
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
REAR_LEFT_COMMAND_TOPIC = "/task2/teach/rear_left/joint_cmd"
REAR_RIGHT_COMMAND_TOPIC = "/task2/teach/rear_right/joint_cmd"
REAR_FEEDBACK_TOPICS = {
    "left": "/task2/teach/rear_left/joint_states",
    "right": "/task2/teach/rear_right/joint_states",
}
REAR_TEACH_TOPICS = {
    "left": "/task2/teach/rear_left/teach_active",
    "right": "/task2/teach/rear_right/teach_active",
}
REAR_READY_TOPICS = {
    "left": "/task2/teach/rear_left/motion_ready",
    "right": "/task2/teach/rear_right/motion_ready",
}
REAR_FAULT_TOPICS = {
    "left": "/task2/teach/rear_left/fault",
    "right": "/task2/teach/rear_right/fault",
}


class TeachHandoverError(RuntimeError):
    """A coordinated handover precondition or external operation failed."""


class Task2TeachHandoverNode:
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
        self.command_max_age_sec = self._positive_param("~command_max_age_sec", 0.25)
        self.pair_max_skew_sec = self._positive_param("~pair_max_skew_sec", 0.02)
        self.joint_sync_tolerance_rad = self._positive_param(
            "~joint_sync_tolerance_rad", 0.05
        )
        self.gripper_sync_tolerance_m = self._positive_param(
            "~gripper_sync_tolerance_m", 0.015
        )
        self.feedback_max_age_sec = self._positive_param("~feedback_max_age_sec", 0.10)
        # Looser bound used on the resume path only.  Keep it at or below the
        # driver's max_hold_delta_rad so a hold this coordinator accepts is
        # never one the rear driver will refuse.
        self.resync_tolerance_rad = self._positive_param("~resync_tolerance_rad", 0.10)
        # Whether POLICY mirrors every policy pair to the rear arms as well.
        #
        # It used to, so the operator's grip always matched the front arms.
        # That requirement died with absolute mapping: the clutch captures the
        # front/rear offset at the moment teaching engages, so the rear arms are
        # free to sit wherever they are.  Mirroring costs a CAN-control entry on
        # every handback, and on this firmware every entry drops the arm for
        # ~100 ms.  Off by default; the rear arms hold in standby instead.
        #
        # The cost is real: the rear arms no longer track, so across many
        # takeovers they drift away from the front arms and the grip gets
        # awkward.  Re-aligning is a deliberate operator action, not something
        # that happens silently on every S.
        self.rear_follows_policy = bool(
            self.ros.get_param("~rear_follows_policy", False)
        )
        # Arming out of PAUSED is a different situation: at power-on the rear
        # arms sit wherever they were left, with no reason to match the front
        # arms.  The hold IS the correction there, so it gets a wider bound,
        # a supervised low-speed move rather than a refusal.  Keep it at or
        # below the rear driver's max_hold_delta_rad.
        self.initial_sync_tolerance_rad = self._positive_param(
            "~initial_sync_tolerance_rad", 0.60
        )
        # With clutch mapping the front and rear arms are offset by whatever
        # the rear arms sagged at engagement, so "front equals rear" is no
        # longer the invariant after MANUAL.  What must hold is that the
        # OFFSET stayed put: that is exactly "the front arms tracked 1:1".
        self.tracking_tolerance_rad = self._positive_param(
            "~tracking_tolerance_rad", 0.10
        )
        # M waits for the operator to press the arm buttons, so it is long.
        # S never waits for the operator - it only allows time for one fresh
        # report from each rear driver - so it is short.
        self.teach_wait_timeout_sec = self._positive_param(
            "~teach_wait_timeout_sec", 60.0
        )
        self.teach_confirm_timeout_sec = self._positive_param(
            "~teach_confirm_timeout_sec", 2.0
        )
        self.motion_ready_timeout_sec = self._positive_param(
            "~motion_ready_timeout_sec", 10.0
        )
        # motion_ready means the rear arm accepted the hold, not that it has
        # reached it.  A cold arm-up can be a 0.24 rad move at 20% speed, so
        # the convergence check has to wait for the motion instead of judging
        # it microseconds after the target was queued.
        self.hold_settle_timeout_sec = self._positive_param(
            "~hold_settle_timeout_sec", 15.0
        )
        # MANUAL forwards two free-running 200 Hz feedback streams, so it is
        # rate limited rather than driven by message arrival.
        self.manual_forward_period_sec = 1.0 / self._positive_param(
            "~manual_forward_rate", 200.0
        )

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self.state = TeachHandoverState()
        self.policy_pair = PairBuffer(self.pair_max_skew_sec, self.command_max_age_sec)
        self.manual_pair = PairBuffer(self.pair_max_skew_sec, self.command_max_age_sec)
        self._feedback: Dict[str, ValidatedJoint] = {}
        self._teach = TeachSideTracker()
        self._rear_motion_ready = {"left": False, "right": False}
        self.resume_cutoff_wall = float("inf")
        # Clutch reference captured when the operator engages drag teaching.
        # Entering teach mode releases the rear arm's holding torque, so it
        # sags by ~0.2-0.4 rad before the operator can catch it.  Forwarding
        # absolute poses would push that sag straight into the task arms, so
        # MANUAL forwards the DISPLACEMENT since engagement instead.
        self._manual_ref = None
        self._manual_latest = {"left": None, "right": None}
        self._manual_last_publish = 0.0

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
            "/task2/teach_handover/mode", String, queue_size=1, latch=True
        )
        self.prompt_pub = self.ros.Publisher(
            "/task2/teach_handover/prompt", String, queue_size=1, latch=True
        )
        self.fault_pub = self.ros.Publisher(
            "/task2/teach_handover/fault", String, queue_size=1, latch=True
        )

        proxy_factory = service_factory or self.ros.ServiceProxy
        self.policy_pause_service = proxy_factory(
            "/task2/policy/set_paused", SetBool
        )
        # A latched driver fault silently drops every command, including the
        # hold that would re-arm it, so clearing the coordinator alone used to
        # leave the system permanently stuck until the launch was restarted.
        self.rear_reset_services = {
            side: proxy_factory(
                "/task2/teach/rear_{}/reset_fault".format(side), Trigger
            )
            for side in ("left", "right")
        }

        self._subscribers = [
            self.ros.Subscriber(
                POLICY_LEFT_TOPIC, JointState, self.policy_left_callback, queue_size=1
            ),
            self.ros.Subscriber(
                POLICY_RIGHT_TOPIC, JointState, self.policy_right_callback, queue_size=1
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
                REAR_FEEDBACK_TOPICS["left"],
                JointState,
                self.rear_left_feedback_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                REAR_FEEDBACK_TOPICS["right"],
                JointState,
                self.rear_right_feedback_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                REAR_TEACH_TOPICS["left"],
                Bool,
                self.rear_left_teach_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                REAR_TEACH_TOPICS["right"],
                Bool,
                self.rear_right_teach_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                REAR_READY_TOPICS["left"],
                Bool,
                self.rear_left_ready_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                REAR_READY_TOPICS["right"],
                Bool,
                self.rear_right_ready_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                REAR_FAULT_TOPICS["left"],
                String,
                self.rear_left_fault_callback,
                queue_size=1,
            ),
            self.ros.Subscriber(
                REAR_FAULT_TOPICS["right"],
                String,
                self.rear_right_fault_callback,
                queue_size=1,
            ),
        ]
        self._services = [
            self.ros.Service(
                "/task2/teach_handover/request_manual",
                Trigger,
                self.handle_request_manual,
            ),
            self.ros.Service(
                "/task2/teach_handover/request_policy",
                Trigger,
                self.handle_request_policy,
            ),
            self.ros.Service(
                "/task2/teach_handover/reset_fault", Trigger, self.handle_reset_fault
            ),
        ]
        self._publish_mode()
        self.fault_pub.publish(String(data=""))

    # ---------------------------------------------------------------- helpers

    @property
    def mode(self) -> TeachMode:
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
        self.prompt_pub.publish(String(data=self.state.prompt))

    def _close_gate(self) -> None:
        self.policy_pair.clear()
        self.manual_pair.clear()

    def _drop_clutch(self) -> None:
        """Forget the engagement point, ending manual routing for good.

        Deliberately NOT part of _close_gate: S has to keep the front arms
        tracking the rear arms right up to the moment the hold is issued, and
        clearing the reference the instant S is pressed reopens exactly the
        desynchronization window the clutch was introduced to close.
        """
        self._manual_ref = None
        self._manual_latest = {"left": None, "right": None}
        self._manual_latest = {"left": None, "right": None}
        self._manual_last_publish = 0.0

    def _latch_fault(self, reason: str) -> None:
        with self._condition:
            self._close_gate()
            self._drop_clutch()
            self.state.fault(reason)
            self._publish_mode()
            self.fault_pub.publish(String(data=str(reason)))
            self._condition.notify_all()
        self.ros.logerr("Task2 teach handover fault: %s", reason)

    def _service_bool(self, proxy: Callable, value: bool, label: str) -> None:
        try:
            response = proxy(SetBoolRequest(data=bool(value)))
        except Exception as exc:
            raise TeachHandoverError("{} service failed: {}".format(label, exc)) from exc
        if not bool(getattr(response, "success", False)):
            raise TeachHandoverError(
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

    # -------------------------------------------------------------- callbacks

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

    def _rear_feedback_callback(self, side: str, message: JointState) -> None:
        """Rear feedback is both the sync reference and the manual command source."""
        key = "rear_{}".format(side)
        with self._lock:
            try:
                validated = self._validated_message(message)
            except Exception as exc:
                self._latch_fault("invalid {} feedback: {}".format(key, exc))
                return
            self._feedback[key] = validated
            self._condition.notify_all()
            # The teach button is a toggle: one press enters drag teaching,
            # a second press leaves it.  Nobody holds it down, so the two rear
            # arms necessarily leave teaching one at a time.
            #
            # Forwarding deliberately continues after one arm has left teaching
            # and all the way through WAITING_TEACH_EXIT.  Nothing competes for
            # the front arms here - the policy is paused - and a rear arm that
            # has left teaching is enabled and holding, so the front arms
            # simply track it to a standstill.  Gating on teach_active instead
            # would cut tracking the moment the first arm leaves teaching, and
            # whatever the second (still limp) arm does while the operator
            # walks over to press its button would desynchronize the pairs and
            # fault the subsequent hold.
            if self.mode not in (TeachMode.MANUAL, TeachMode.WAITING_TEACH_EXIT):
                return
            if self._manual_ref is None:
                # WAITING_TEACH_EXIT is also reachable straight from PAUSED,
                # where no takeover ever happened and there is nothing to
                # forward.  Only MANUAL genuinely requires a reference.
                if self.mode is TeachMode.MANUAL:
                    self._latch_fault("manual routing without a clutch reference")
                return
            # The two rear arms publish independently at 200 Hz; they never
            # alternate cleanly, so this keeps the latest sample from each
            # side instead of demanding a strict left/right hand-off.  A
            # sample that is stale or badly skewed is skipped, not faulted:
            # jitter on a sensor stream is normal, and skipping simply leaves
            # the front arms where they are.
            self._manual_latest[side] = validated
            left = self._manual_latest["left"]
            right = self._manual_latest["right"]
            if left is None or right is None:
                return
            now = float(self.monotonic_clock())
            if now - self._manual_last_publish < self.manual_forward_period_sec:
                return
            for sample in (left, right):
                age = now - sample.arrival_monotonic
                if not (-1e-9 <= age <= self.feedback_max_age_sec):
                    return
            if abs(left.stamp_sec - right.stamp_sec) > self.pair_max_skew_sec:
                return
            self._manual_last_publish = now
            self._publish_manual_pair(CommandPair(left=left, right=right))

    def rear_left_feedback_callback(self, message: JointState) -> None:
        self._rear_feedback_callback("left", message)

    def rear_right_feedback_callback(self, message: JointState) -> None:
        self._rear_feedback_callback("right", message)

    def _teach_callback(self, side: str, message: Bool) -> None:
        active = bool(message.data)
        with self._condition:
            self._teach.update(side, active, float(self.monotonic_clock()))
            self._condition.notify_all()
            unexpected = active and self.mode in (
                TeachMode.POLICY,
                TeachMode.ARMING_POLICY,
                TeachMode.RESUMING,
            )
        if unexpected:
            self._latch_fault(
                "rear {} entered drag teaching while the policy owned it".format(side)
            )

    def rear_left_teach_callback(self, message: Bool) -> None:
        self._teach_callback("left", message)

    def rear_right_teach_callback(self, message: Bool) -> None:
        self._teach_callback("right", message)

    def _ready_callback(self, side: str, message: Bool) -> None:
        ready = bool(message.data)
        with self._condition:
            was_ready = self._rear_motion_ready[side]
            self._rear_motion_ready[side] = ready
            self._condition.notify_all()
            # Readiness was only ever WAITED on during arming, never watched
            # afterwards.  A rear arm that silently stopped executing left the
            # coordinator routing policy commands to it while the four arms
            # drifted apart, with nothing reporting a problem.
            lost = (
                was_ready
                and not ready
                and self.mode in (
                    TeachMode.ARMING_POLICY,
                    TeachMode.RESUMING,
                    TeachMode.POLICY,
                )
            )
        if lost:
            self._latch_fault(
                "rear {} stopped being motion-ready while the policy owned it"
                .format(side)
            )

    def rear_left_ready_callback(self, message: Bool) -> None:
        self._ready_callback("left", message)

    def rear_right_ready_callback(self, message: Bool) -> None:
        self._ready_callback("right", message)

    def _rear_fault_callback(self, side: str, message: String) -> None:
        reason = str(message.data).strip()
        if reason:
            self._latch_fault("rear {} driver fault: {}".format(side, reason))

    def rear_left_fault_callback(self, message: String) -> None:
        self._rear_fault_callback("left", message)

    def rear_right_fault_callback(self, message: String) -> None:
        self._rear_fault_callback("right", message)

    def _policy_callback(self, side: str, message: JointState) -> None:
        with self._lock:
            if self.mode not in (TeachMode.RESUMING, TeachMode.POLICY):
                return
            try:
                validated = self._validated_message(message)
                if (
                    self.mode is TeachMode.RESUMING
                    and validated.stamp_sec < self.resume_cutoff_wall - 1e-9
                ):
                    raise ValidationError("policy command predates fresh resume")
                pair = self.policy_pair.offer(
                    side, validated, now_monotonic=float(self.monotonic_clock())
                )
                if pair is None:
                    return
                if self.mode is TeachMode.RESUMING:
                    self.state.complete_policy()
                    self._publish_mode()
                self._publish_four_arm_pair(pair)
            except Exception as exc:
                self._latch_fault("invalid policy {} command: {}".format(side, exc))

    def policy_left_callback(self, message: JointState) -> None:
        self._policy_callback("left", message)

    def policy_right_callback(self, message: JointState) -> None:
        self._policy_callback("right", message)

    # ------------------------------------------------------------- publishing

    def _publish_front_pair(self, pair: CommandPair) -> None:
        self.front_left_pub.publish(self._message_from_validated(pair.left))
        self.front_right_pub.publish(self._message_from_validated(pair.right))

    def _capture_manual_reference(self) -> None:
        """Freeze the clutch engagement point for both arm pairs."""
        required = ("front_left", "front_right", "rear_left", "rear_right")
        with self._lock:
            now = float(self.monotonic_clock())
            missing = [key for key in required if key not in self._feedback]
            if missing:
                raise TeachHandoverError(
                    "cannot capture the manual reference: no {}".format(
                        ", ".join(missing)
                    )
                )
            for key in required:
                age = now - self._feedback[key].arrival_monotonic
                if not (-1e-9 <= age <= self.feedback_max_age_sec):
                    raise TeachHandoverError(
                        "cannot capture the manual reference: {} feedback is "
                        "{:.3f}s old".format(key, age)
                    )
            self._manual_ref = {
                key: tuple(self._feedback[key].positions) for key in required
            }

    def _manual_target(self, side: str, rear_positions) -> tuple:
        front_ref = self._manual_ref["front_{}".format(side)]
        rear_ref = self._manual_ref["rear_{}".format(side)]
        return tuple(
            front_ref[index] + (rear_positions[index] - rear_ref[index])
            for index in range(7)
        )

    def _publish_manual_pair(self, pair: CommandPair) -> None:
        """Forward the operator's displacement, never the absolute rear pose."""
        left = self._message_from_validated(pair.left)
        left.position = list(self._manual_target("left", pair.left.positions))
        right = self._message_from_validated(pair.right)
        right.position = list(self._manual_target("right", pair.right.positions))
        self.front_left_pub.publish(left)
        self.front_right_pub.publish(right)

    def _publish_four_arm_pair(self, pair: CommandPair) -> None:
        left = self._message_from_validated(pair.left)
        right = self._message_from_validated(pair.right)
        self.front_left_pub.publish(left)
        self.front_right_pub.publish(right)
        if self.rear_follows_policy:
            self.rear_left_pub.publish(left)
            self.rear_right_pub.publish(right)

    def _publish_rear_hold(self) -> None:
        # Still sent even when the rear arms do not follow policy: this is the
        # message that drives the re-arm handshake, and without it the rear
        # drivers stay NOT_READY and the coordinator refuses to resume.  In
        # standby-hold the driver treats it as "confirm you are holding", not
        # as a pose to drive to.

        """Send the measured front pose to the rear arms only, to re-arm them."""
        with self._lock:
            left = self._feedback["front_left"]
            right = self._feedback["front_right"]
            stamp = float(self.wall_clock())
            self.rear_left_pub.publish(self._message_from_validated(left, stamp))
            self.rear_right_pub.publish(self._message_from_validated(right, stamp))

    def _require_tracked(self) -> None:
        """Verify the clutch offset never drifted, i.e. the front arms tracked.

        Absolute agreement cannot be required after MANUAL: engaging drag
        teaching drops the rear arms by however much gravity takes them (0.84
        rad on one measured cycle), and the clutch deliberately keeps that out
        of the front arms.  The offset staying constant is the real invariant.
        """
        with self._lock:
            if self._manual_ref is None:
                return
            for side in ("left", "right"):
                front_key = "front_{}".format(side)
                rear_key = "rear_{}".format(side)
                if front_key not in self._feedback or rear_key not in self._feedback:
                    raise TeachHandoverError(
                        "cannot verify tracking: no {} feedback".format(side)
                    )
                front = self._feedback[front_key].positions
                rear = self._feedback[rear_key].positions
                front_ref = self._manual_ref[front_key]
                rear_ref = self._manual_ref[rear_key]
                for index in range(6):
                    drift = abs(
                        (front[index] - rear[index])
                        - (front_ref[index] - rear_ref[index])
                    )
                    if drift > self.tracking_tolerance_rad:
                        raise TeachHandoverError(
                            "{} j{} did not track: clutch offset drifted "
                            "{:.4f} rad, limit {:.4f}".format(
                                side, index + 1, drift, self.tracking_tolerance_rad
                            )
                        )

    def _await_synchronized(self, tolerance_rad: float, timeout_sec: float) -> None:
        """Poll until the four arms agree, or report why they never did."""
        deadline = self.monotonic_clock() + timeout_sec
        while True:
            reason = self._sync_failure_reason(tolerance_rad)
            if reason is None:
                return
            if self.monotonic_clock() >= deadline:
                raise TeachHandoverError(
                    "arms did not converge within {:.1f}s: {}".format(
                        timeout_sec, reason
                    )
                )
            time.sleep(0.02)

    def _require_synchronized(self, tolerance_rad: Optional[float] = None) -> None:
        """Check all four arms are fresh and aligned.

        `tolerance_rad` defaults to the strict entry tolerance.  The resume
        path passes the looser resync tolerance: front and rear each carry
        their own gravity steady-state error, so measured-vs-measured can
        exceed the entry tolerance even when nothing is wrong, and the hold
        that follows is itself the correction.
        """
        reason = self._sync_failure_reason(tolerance_rad)
        if reason is not None:
            raise TeachHandoverError(reason)

    def _sync_failure_reason(self, tolerance_rad: Optional[float] = None):
        """Return why the four arms are not usable together, or None if fine."""
        limit = (
            self.joint_sync_tolerance_rad if tolerance_rad is None else tolerance_rad
        )
        with self._lock:
            now = float(self.monotonic_clock())
            required = ("front_left", "front_right", "rear_left", "rear_right")
            missing = [key for key in required if key not in self._feedback]
            if missing:
                return "four-arm feedback is incomplete: no {}".format(
                    ", ".join(missing)
                )

            # Report WHICH condition tripped and by how much.  A safety gate
            # that only says "not synchronized or stale" costs a lot of field
            # time when the joint deltas look fine and the real problem is
            # feedback age, or vice versa.
            ages = {
                key: now - self._feedback[key].arrival_monotonic for key in required
            }
            stale = {
                key: age
                for key, age in ages.items()
                if not (-1e-9 <= age <= self.feedback_max_age_sec)
            }
            if stale:
                return "feedback older than {:.3f}s: {}".format(
                    self.feedback_max_age_sec,
                    ", ".join(
                        "{}={:.3f}s".format(key, age)
                        for key, age in sorted(stale.items())
                    ),
                )

            worst_delta = 0.0
            worst_label = ""
            for side in ("left", "right"):
                front = self._feedback["front_{}".format(side)]
                rear = self._feedback["rear_{}".format(side)]
                for index in range(6):
                    delta = abs(front.positions[index] - rear.positions[index])
                    if delta > worst_delta:
                        worst_delta = delta
                        worst_label = "{} j{}".format(side, index + 1)
                gripper = abs(front.positions[6] - rear.positions[6])
                if gripper > self.gripper_sync_tolerance_m:
                    return "{} gripper differs by {:.4f} m, limit {:.4f}".format(
                        side, gripper, self.gripper_sync_tolerance_m
                    )
            if worst_delta > limit:
                return "front/rear differ by {:.4f} rad at {}, limit {:.4f}".format(
                    worst_delta, worst_label, limit
                )
            self.ros.loginfo(
                "Task2 teach handover sync OK: worst %.4f rad at %s "
                "(limit %.4f), worst feedback age %.3fs",
                worst_delta,
                worst_label or "none",
                limit,
                max(ages.values()),
            )
            return None

    # ---------------------------------------------------------------- services

    def handle_request_manual(self, _request: Any) -> TriggerResponse:
        with self._condition:
            try:
                self.state.begin_waiting_teach_enter()
                self._close_gate()
                self._drop_clutch()
                self._teach.reset_wait()
                cutoff = float(self.monotonic_clock())
                self._publish_mode()
            except Exception as exc:
                self._latch_fault("manual request rejected: {}".format(exc))
                return TriggerResponse(success=False, message=str(exc))

        pause_confirmed = True
        try:
            self._service_bool(self.policy_pause_service, True, "policy pause")
        except TeachHandoverError as exc:
            pause_confirmed = False
            self.ros.logwarn(
                "Task2 continuing manual rescue without pause ack: %s", exc
            )

        try:
            self._require_synchronized()
            self.ros.loginfo(
                "Task2 teach handover: press BOTH rear teach buttons to take over"
            )
            if not self._wait_until(
                lambda: self._teach.both_active(cutoff), self.teach_wait_timeout_sec
            ):
                raise TeachHandoverError(
                    "both rear arms did not enter drag teaching in time"
                )
            # No second synchronization check here.  Engaging drag teaching
            # releases the rear arms, and the sag that follows makes an
            # absolute front/rear match physically unachievable.  The clutch
            # reference captured now is what keeps the transfer jump-free.
            self._capture_manual_reference()
            with self._condition:
                # Drop anything buffered before the operator took the arms.
                self.manual_pair.clear()
                self._manual_latest = {"left": None, "right": None}
                self._manual_last_publish = 0.0
                self.state.complete_manual(pause_confirmed)
                self._publish_mode()
            return TriggerResponse(success=True, message="manual control active")
        except Exception as exc:
            self._latch_fault("manual handover failed: {}".format(exc))
            return TriggerResponse(success=False, message=str(exc))

    def handle_request_policy(self, _request: Any) -> TriggerResponse:
        """S: hand control back to the policy.

        The operator must have ALREADY left drag teaching on both rear arms
        before pressing S.  The order is the opposite of M and both directions
        matter:

          M  keyboard first, then the arm buttons - the policy has to stop
             before the operator may grab the rear arms.
          S  the arm buttons first, then the keyboard - the rear arms have to
             be out of teaching, standing still and back in sync before
             control is handed back.

        So this is a precondition check, not a wait for the operator.  The
        only waiting it does is for one fresh report from each rear driver.
        """
        with self._condition:
            self._teach.reset_wait()
            cutoff = float(self.monotonic_clock())

        if not self._wait_until(
            lambda: self._teach.both_reported(cutoff), self.teach_confirm_timeout_sec
        ):
            reason = "rear drivers did not report a fresh teach state"
            self._latch_fault("policy request rejected: {}".format(reason))
            return TriggerResponse(success=False, message=reason)

        with self._lock:
            still_teaching = self._teach.any_active()
        if still_teaching:
            # Not a fault: the operator simply pressed S too early.  Stay in
            # MANUAL with the front arms still tracking, and let them retry.
            reason = (
                "rear arms are still in drag teaching; press each rear teach "
                "button once to leave teaching, then press S again"
            )
            self.ros.logwarn("Task2 teach handover: %s", reason)
            return TriggerResponse(success=False, message=reason)

        with self._condition:
            try:
                # Remember where this came from: PAUSED means a cold arm-up,
                # MANUAL means the operator just handed control back.
                from_paused = self.state.mode is TeachMode.PAUSED
                self.state.begin_waiting_teach_exit()
                self._close_gate()
                self._rear_motion_ready = {"left": False, "right": False}
                self._publish_mode()
            except Exception as exc:
                self._latch_fault("policy request rejected: {}".format(exc))
                return TriggerResponse(success=False, message=str(exc))
        # Either way the hold is a supervised low-speed correction, so the
        # bound on how far it may travel is the same.  What differs is the
        # extra invariant available on the MANUAL path.
        sync_limit = self.initial_sync_tolerance_rad

        try:
            # Always reconfirm pause: S must be safe even if M's acknowledgement
            # was missing or the policy process restarted in between.
            self._service_bool(self.policy_pause_service, True, "policy pause")
            if not from_paused:
                self._require_tracked()
            self._require_synchronized(sync_limit)
            with self._condition:
                # Manual routing ends here, not when S was pressed.
                self._drop_clutch()
                self.state.begin_arming_policy()
                self._publish_mode()
            self._publish_rear_hold()
            if not self._wait_until(
                lambda: self._rear_motion_ready["left"]
                and self._rear_motion_ready["right"],
                self.motion_ready_timeout_sec,
            ):
                raise TeachHandoverError("rear motion-ready acknowledgement timed out")
            # After the hold the arms must agree tightly, whatever the start -
            # but the move itself takes time, so wait for it.
            self._await_synchronized(
                self.resync_tolerance_rad, self.hold_settle_timeout_sec
            )
            self._service_bool(self.policy_pause_service, False, "policy fresh resume")
            with self._condition:
                self.resume_cutoff_wall = float(self.wall_clock())
                self.state.begin_resuming()
                self.policy_pair.clear()
                self._publish_mode()
            return TriggerResponse(success=True, message="waiting for fresh policy pair")
        except Exception as exc:
            self._latch_fault("policy handover failed: {}".format(exc))
            return TriggerResponse(success=False, message=str(exc))

    def handle_reset_fault(self, _request: Any) -> TriggerResponse:
        with self._condition:
            try:
                self.state.reset_fault()
                self._close_gate()
                self._drop_clutch()
                self.resume_cutoff_wall = float("inf")
                self._rear_motion_ready = {"left": False, "right": False}
                self.fault_pub.publish(String(data=""))
                self._publish_mode()
            except TransitionError as exc:
                return TriggerResponse(success=False, message=str(exc))

        # Clear the rear drivers too, best effort: a driver still latched in
        # fault would drop the very hold that re-arms it.
        cleared = []
        failed = []
        for side, proxy in self.rear_reset_services.items():
            try:
                response = proxy()
                if bool(getattr(response, "success", False)):
                    cleared.append(side)
                else:
                    failed.append("{}: {}".format(side, getattr(response, "message", "")))
            except Exception as exc:  # noqa: BLE001 - report, never mask
                failed.append("{}: {}".format(side, exc))
        if failed:
            return TriggerResponse(
                success=False,
                message="coordinator reset; rear driver reset failed for {}".format(
                    "; ".join(failed)
                ),
            )
        return TriggerResponse(
            success=True,
            message="software fault reset; paused; rear drivers cleared ({})".format(
                ", ".join(sorted(cleared))
            ),
        )


def main() -> None:
    rospy.init_node("task2_teach_handover_node", anonymous=False)
    Task2TeachHandoverNode()
    rospy.spin()


if __name__ == "__main__":
    main()

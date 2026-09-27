#!/usr/bin/env python3
"""Button-driven Task2 handover: the teach button IS the control.

There are no M and S keys here.  Pressing the physical drag-teach button on a
rear arm takes that arm's front arm over; pressing it again hands it back.
Nothing else starts or ends a takeover, so there is no ordering for the
operator to remember and no window in which the two can disagree.

The rear arms are limp whenever they are not being taught.  That is what makes
the button sufficient: the operator carries a limp rear arm to a comfortable
pose, and only the button press decides when that pose becomes the clutch
reference.  A braked arm could not be repositioned at all, and an arm that
forwarded while being repositioned would drag the front arm along for the trip.

Takeover is per side.  Pressing one button takes over one arm; the other front
arm holds still.  Policy is paused globally the moment ANY side engages - the
un-taken arm must not keep executing a plan that was made for a world the
operator is now changing by hand.

Role switching does not appear anywhere: on firmware S-V1.7-3 a rear arm sent
MasterSlaveConfig(0xFA) stops all CAN output and only becomes a real teaching
input arm after a power cycle, which a live handover cannot do.
"""

import threading
import time
from typing import Any, Callable, Dict, Optional

import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, SetBoolRequest, Trigger, TriggerResponse

import task2_homing_core as homing
from task2_handover_core import JOINT_NAMES
from task2_teach_handover_core import (
    ValidatedJoint,
    ValidationError,
    validate_joint,
)


SIDES = ("left", "right")

POLICY_TOPICS = {
    "left": "/task2/policy/joint_left",
    "right": "/task2/policy/joint_right",
}
FRONT_COMMAND_TOPICS = {
    "left": "/master/joint_left",
    "right": "/master/joint_right",
}
FRONT_FEEDBACK_TOPICS = {
    "left": "/puppet/joint_left",
    "right": "/puppet/joint_right",
}
REAR_FEEDBACK_TOPICS = {
    "left": "/task2/teach/rear_left/joint_states",
    "right": "/task2/teach/rear_right/joint_states",
}
REAR_TEACH_TOPICS = {
    "left": "/task2/teach/rear_left/teach_active",
    "right": "/task2/teach/rear_right/teach_active",
}
REAR_FAULT_TOPICS = {
    "left": "/task2/teach/rear_left/fault",
    "right": "/task2/teach/rear_right/fault",
}
REAR_RESET_SERVICES = {
    "left": "/task2/teach/rear_left/reset_fault",
    "right": "/task2/teach/rear_right/reset_fault",
}
PAUSE_SERVICE = "/task2/policy/set_paused"


class ButtonHandoverError(RuntimeError):
    """A precondition or an external service call failed."""


class Task2TeachButtonNode:
    """The only software command path to the two front arms."""

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

        self.feedback_max_age_sec = self._positive_param("~feedback_max_age_sec", 0.10)
        self.command_max_age_sec = self._positive_param("~command_max_age_sec", 0.25)
        self.forward_rate = self._positive_param("~manual_forward_rate", 200.0)
        # How long a side may be engaged without usable rear feedback before the
        # takeover is treated as broken.  Skipping a few frames is normal; going
        # blind for a second while the operator is dragging is not.
        self.feedback_gap_fault_sec = self._positive_param(
            "~feedback_gap_fault_sec", 1.0
        )

        self._lock = threading.RLock()
        self._fault: str = ""
        self._engaged: Dict[str, bool] = {side: False for side in SIDES}
        self._teach_seen: Dict[str, Optional[bool]] = {side: None for side in SIDES}
        self._rear_ref: Dict[str, Optional[Any]] = {side: None for side in SIDES}
        self._front_ref: Dict[str, Optional[Any]] = {side: None for side in SIDES}
        self._hold: Dict[str, Optional[Any]] = {side: None for side in SIDES}
        self._feedback: Dict[str, Optional[Any]] = {}
        self._blind_since: Dict[str, Optional[float]] = {side: None for side in SIDES}
        self._policy_paused = False
        self._pause_service_missing = False
        # When a policy command was last forwarded.  Homing writes to the same
        # front-arm topics, so it has to know whether anything else is.
        self._last_policy_command = None

        self.front_pubs = {
            side: self.ros.Publisher(
                FRONT_COMMAND_TOPICS[side], JointState, queue_size=1
            )
            for side in SIDES
        }
        self.mode_pub = self.ros.Publisher(
            "/task2/teach_handover/mode", String, queue_size=1, latch=True
        )
        self.fault_pub = self.ros.Publisher(
            "/task2/teach_handover/fault", String, queue_size=1, latch=True
        )

        proxy_factory = service_factory or self.ros.ServiceProxy
        self.pause_service = proxy_factory(PAUSE_SERVICE, SetBool)
        self.rear_reset_services = {
            side: proxy_factory(REAR_RESET_SERVICES[side], Trigger) for side in SIDES
        }
        self.reset_service = self.ros.Service(
            "/task2/teach_handover/reset_fault", Trigger, self.handle_reset_fault
        )
        # Front homing lives here because this node is the only publisher to
        # /master/joint_*.  A standalone homing script would have to publish
        # there too, which breaks the single-writer invariant the whole design
        # rests on and races this node's own forwarding loop.
        self.home_service = self.ros.Service(
            "/task2/teach_handover/home_front", Trigger, self.handle_home_front
        )

        self._subscriptions = []
        for side in SIDES:
            self._subscribe(
                POLICY_TOPICS[side], JointState, self._policy_callback(side)
            )
            self._subscribe(
                FRONT_FEEDBACK_TOPICS[side],
                JointState,
                self._feedback_callback("front_" + side),
            )
            self._subscribe(
                REAR_FEEDBACK_TOPICS[side],
                JointState,
                self._feedback_callback("rear_" + side),
            )
            self._subscribe(REAR_TEACH_TOPICS[side], Bool, self._teach_callback(side))
            self._subscribe(
                REAR_FAULT_TOPICS[side], String, self._rear_fault_callback(side)
            )
        self._publish_mode()
        self.fault_pub.publish(String(data=""))

    # ---------------------------------------------------------------- helpers

    def _subscribe(self, topic, message_type, callback):
        self._subscriptions.append(
            self.ros.Subscriber(topic, message_type, callback, queue_size=1)
        )

    def _positive_param(self, name: str, default: float) -> float:
        value = float(self.ros.get_param(name, default))
        if value <= 0.0 or value != value:
            raise ValueError("{} must be finite and greater than zero".format(name))
        return value

    @property
    def fault(self) -> str:
        return self._fault

    @property
    def engaged(self) -> Dict[str, bool]:
        return dict(self._engaged)

    @property
    def any_engaged(self) -> bool:
        return any(self._engaged.values())

    def _mode_text(self) -> str:
        if self._fault:
            return "fault"
        engaged = [side for side in SIDES if self._engaged[side]]
        if not engaged:
            return "policy"
        return "manual:" + "+".join(engaged)

    def _publish_mode(self) -> None:
        self.mode_pub.publish(String(data=self._mode_text()))

    def _latch_fault(self, reason: str) -> None:
        """Stop forwarding anything and say why.

        Faulting also pauses the policy.  A coordinator that has stopped
        publishing is not a safe place for a policy to keep planning against
        arms nobody is driving, and the pause is best-effort precisely because
        the usual reason to be here is that a service already failed.
        """
        if self._fault:
            return
        self._fault = reason
        self._engaged = {side: False for side in SIDES}
        self.ros.logerr("Task2 button handover fault: %s", reason)
        self._set_policy_paused(True)
        self.fault_pub.publish(String(data=reason))
        self._publish_mode()

    def _set_policy_paused(self, paused: bool) -> bool:
        """Ask the policy to pause or resume.  Never fatal, deliberately.

        This call cannot be what decides whether a takeover happens.  Routing
        already makes the policy harmless during one: this node is the only
        publisher to the front arms, and it drops every policy command while
        any side is engaged.  Pausing is a courtesy that stops the policy
        building a chunk for a world the operator is changing - useful, but not
        a safety barrier.

        Refusing to engage when it fails is the genuinely dangerous outcome:
        the operator is holding a rear arm and the front arm does not follow.
        That is how this faulted for real - a teach button pressed in the gap
        between two policy runs, when there was no policy to pause and nothing
        unsafe about it, left the coordinator latched and ignoring the button.
        """
        try:
            response = self.pause_service(SetBoolRequest(data=bool(paused)))
        except Exception as exc:
            # Not an error during data collection: there is no policy to pause,
            # by design.  Logging it at ERROR on every single button press
            # buries the errors that do matter, so say it once and then keep
            # quiet until a policy actually answers again.
            if not self._pause_service_missing:
                self._pause_service_missing = True
                self.ros.logwarn(
                    "%s is unavailable (%s). Continuing - routing already "
                    "blocks the policy, and during data collection there is no "
                    "policy to pause. This will not be logged again until one "
                    "answers.",
                    PAUSE_SERVICE,
                    exc,
                )
            return False
        if not bool(getattr(response, "success", False)):
            self.ros.logerr(
                "%s rejected the request (%s); continuing",
                PAUSE_SERVICE,
                getattr(response, "message", "no message"),
            )
            return False
        if self._pause_service_missing:
            self.ros.loginfo("%s is answering again", PAUSE_SERVICE)
            self._pause_service_missing = False
        self._policy_paused = bool(paused)
        return True

    def _fresh(self, key: str) -> Optional[ValidatedJoint]:
        joint = self._feedback.get(key)
        if joint is None:
            return None
        if self.monotonic_clock() - joint.arrival_monotonic > self.feedback_max_age_sec:
            return None
        return joint

    # -------------------------------------------------------------- callbacks

    def _validated(self, message: JointState, max_age_sec: float) -> ValidatedJoint:
        stamp = message.header.stamp.to_sec()
        return validate_joint(
            names=message.name,
            positions=message.position,
            stamp_sec=float(stamp) if stamp else float(self.wall_clock()),
            arrival_monotonic=float(self.monotonic_clock()),
            now_wall=float(self.wall_clock()),
            now_monotonic=float(self.monotonic_clock()),
            max_age_sec=max_age_sec,
        )

    def _feedback_callback(self, key: str):
        def callback(message: JointState) -> None:
            try:
                joint = self._validated(message, self.feedback_max_age_sec)
            except ValidationError:
                # Feedback is a free-running stream; a bad or late frame is
                # normal and the next one is along in milliseconds.  Faulting
                # here would turn ordinary jitter into a stopped robot.
                return
            with self._lock:
                self._feedback[key] = joint

        return callback

    def _rear_fault_callback(self, side: str):
        def callback(message: String) -> None:
            if not message.data:
                return
            with self._lock:
                self._latch_fault(
                    "rear {} driver faulted: {}".format(side, message.data)
                )

        return callback

    def _teach_callback(self, side: str):
        def callback(message: Bool) -> None:
            active = bool(message.data)
            with self._lock:
                previous = self._teach_seen[side]
                self._teach_seen[side] = active
                if previous is None or previous == active:
                    # First report after startup is a level, not an edge.  An
                    # arm found already in teach mode must not silently engage
                    # a takeover nobody asked for.
                    return
                if self._fault:
                    return
                if active:
                    self._engage(side)
                else:
                    self._disengage(side)

        return callback

    def _policy_callback(self, side: str):
        def callback(message: JointState) -> None:
            with self._lock:
                if self._fault or self.any_engaged:
                    # Dropped, not queued.  A command produced before the
                    # operator intervened describes a world that no longer
                    # exists, and replaying it on resume is exactly the jump
                    # this whole design exists to avoid.
                    return
                try:
                    joint = self._validated(message, self.command_max_age_sec)
                except ValidationError as exc:
                    # A malformed or stale POLICY command is different from
                    # jittery feedback: it is the thing that moves the arms.
                    self._latch_fault("invalid policy command: {}".format(exc))
                    return
                self._last_policy_command = self.monotonic_clock()
                self._publish_front(side, joint.positions)
                self._hold[side] = joint.positions

        return callback

    # ------------------------------------------------------------ engagement

    def _engage(self, side: str) -> None:
        """Capture the clutch reference and take this side over."""
        rear = self._fresh("rear_" + side)
        front = self._fresh("front_" + side)
        if rear is None or front is None:
            self._latch_fault(
                "cannot take over {}: no fresh {} feedback at the moment the "
                "button engaged".format(
                    side, "rear" if rear is None else "front"
                )
            )
            return
        if not self.any_engaged:
            self._set_policy_paused(True)
        # Whatever the other side was last told stays its target while this one
        # is being driven by hand.
        for other in SIDES:
            if other != side and self._hold[other] is None:
                held = self._fresh("front_" + other)
                if held is not None:
                    self._hold[other] = held.positions
        self._rear_ref[side] = rear
        self._front_ref[side] = front
        self._blind_since[side] = None
        self._engaged[side] = True
        self.ros.loginfo("Task2 takeover engaged on %s", side)
        self._publish_mode()

    def _disengage(self, side: str) -> None:
        self._engaged[side] = False
        self._rear_ref[side] = None
        self._front_ref[side] = None
        self._blind_since[side] = None
        self.ros.loginfo("Task2 takeover released on %s", side)
        if not self.any_engaged and not self._fault:
            # If this fails the policy just stays paused, which is the safe
            # direction: nothing moves until it can be reached again.
            self._set_policy_paused(False)
        self._publish_mode()

    # -------------------------------------------------------------- forwarding

    def _clutch_target(self, side: str, rear: ValidatedJoint):
        """front_ref + (rear_now - rear_ref), joint by joint.

        Incremental on purpose.  Engaging drag teaching drops the rear arm by
        however much gravity takes it before the operator has it under control,
        and an absolute mapping would hand that drop straight to the front arm.
        """
        front_ref = self._front_ref[side].positions
        rear_ref = self._rear_ref[side].positions
        return tuple(
            f + (r - r0) for f, r, r0 in zip(front_ref, rear.positions, rear_ref)
        )

    def _publish_front(self, side: str, positions) -> None:
        message = JointState()
        try:
            message.header.stamp = self.ros.Time.from_sec(float(self.wall_clock()))
        except AttributeError:
            message.header.stamp = type(self.ros.Time.now())(float(self.wall_clock()))
        message.name = list(JOINT_NAMES)
        message.position = list(positions)
        self.front_pubs[side].publish(message)

    def _forward_once(self) -> None:
        with self._lock:
            if self._fault:
                return
            for side in SIDES:
                if self._engaged[side]:
                    rear = self._fresh("rear_" + side)
                    if rear is None:
                        # Skipping is correct for a dropped frame and wrong for
                        # a dead link, so it is bounded in time rather than
                        # tolerated forever.
                        started = self._blind_since[side]
                        now = self.monotonic_clock()
                        if started is None:
                            self._blind_since[side] = now
                        elif now - started > self.feedback_gap_fault_sec:
                            self._latch_fault(
                                "rear {} feedback went stale for more than "
                                "{:.1f}s during a takeover".format(
                                    side, self.feedback_gap_fault_sec
                                )
                            )
                        continue
                    self._blind_since[side] = None
                    target = self._clutch_target(side, rear)
                    self._publish_front(side, target)
                    self._hold[side] = target
                elif self.any_engaged and self._hold[side] is not None:
                    # The un-taken arm is actively held rather than left to its
                    # driver's last target, so a stalled policy cannot leave it
                    # drifting while the operator works on the other side.
                    self._publish_front(side, self._hold[side])

    # ---------------------------------------------------------------- services

    def handle_home_front(self, _request: Any) -> TriggerResponse:
        """Ramp both front arms to their configured home pose.

        No mode switching is involved: the front arms live in CAN control all
        the time, so homing is just a different target.  That is why this
        direction costs nothing - nothing is disabled, nothing drops.

        Targets come from ROS parameters because std_srvs carries no pose and a
        custom service would mean rebuilding the piper package.  The CLI writes
        them immediately before calling.
        """
        with self._lock:
            if self._fault:
                return TriggerResponse(
                    success=False, message="coordinator is faulted: " + self._fault
                )
            if self.any_engaged:
                return TriggerResponse(
                    success=False,
                    message="refusing to home while a takeover is engaged",
                )
            last = self._last_policy_command
            if (
                last is not None
                and self.monotonic_clock() - last <= self.command_max_age_sec
            ):
                # Homing and the policy would both be writing to
                # /master/joint_* and the arms would follow whichever message
                # landed last.  Refuse rather than fight: this is the one
                # place the single-writer invariant can be broken from inside
                # the writer itself.
                return TriggerResponse(
                    success=False,
                    message="the policy is actively commanding the front arms; "
                    "pause it (rosservice call /task2/policy/set_paused true) "
                    "before homing",
                )

        targets = {}
        try:
            for side in SIDES:
                param = "/task2/homing/front_{}".format(side)
                raw = self.ros.get_param(param, None)
                if raw is None:
                    return TriggerResponse(
                        success=False, message="{} is not set".format(param)
                    )
                targets[side] = homing.validate_pose(raw, param)
            joint_speed, gripper_speed, rate = homing.speed_settings(
                {"speed": self.ros.get_param("/task2/homing/speed", {})}
            )
            joint_step, gripper_step = homing.step_limits(
                joint_speed, gripper_speed, rate
            )
        except homing.HomingError as exc:
            return TriggerResponse(success=False, message=str(exc))

        plans = {}
        with self._lock:
            for side in SIDES:
                measured = self._fresh("front_" + side)
                if measured is None:
                    return TriggerResponse(
                        success=False,
                        message="no fresh feedback from front {}".format(side),
                    )
                plans[side] = homing.plan_ramp(
                    measured.positions, targets[side], joint_step, gripper_step
                )

        ticks = max(len(plan) for plan in plans.values())
        self.ros.loginfo(
            "Task2 homing the front arms: %d ticks, about %.1fs",
            ticks,
            homing.ramp_duration_sec(ticks, rate),
        )
        period = 1.0 / rate
        for index in range(ticks):
            with self._lock:
                if self._fault:
                    return TriggerResponse(
                        success=False, message="homing aborted: " + self._fault
                    )
                if self.any_engaged:
                    # The operator pressed a teach button mid-move.  Their hands
                    # outrank a homing request.
                    return TriggerResponse(
                        success=False, message="homing aborted: takeover engaged"
                    )
                for side, plan in plans.items():
                    if not plan:
                        continue
                    point = plan[min(index, len(plan) - 1)]
                    self._publish_front(side, point)
                    self._hold[side] = point
            time.sleep(period)

        with self._lock:
            for side in SIDES:
                self._publish_front(side, targets[side])
                self._hold[side] = targets[side]
        return TriggerResponse(success=True, message="front arms homed")

    def handle_reset_fault(self, _request: Any) -> TriggerResponse:
        with self._lock:
            cleared = []
            for side in SIDES:
                try:
                    self.rear_reset_services[side](Trigger._request_class())
                    cleared.append(side)
                except Exception as exc:
                    self.ros.logwarn(
                        "could not reset rear %s driver: %s", side, exc
                    )
            self._fault = ""
            self._engaged = {side: False for side in SIDES}
            self._teach_seen = {side: None for side in SIDES}
            self._hold = {side: None for side in SIDES}
            self._blind_since = {side: None for side in SIDES}
            self.fault_pub.publish(String(data=""))
            self._publish_mode()
            return TriggerResponse(
                success=True,
                message="fault cleared; policy still paused; rear drivers "
                "cleared ({})".format(", ".join(cleared) or "none"),
            )

    def spin(self) -> None:
        rate = self.ros.Rate(self.forward_rate)
        while not self.ros.is_shutdown():
            self._forward_once()
            rate.sleep()


def main() -> None:
    rospy.init_node("task2_teach_button_handover", anonymous=False)
    Task2TeachButtonNode().spin()


if __name__ == "__main__":
    main()

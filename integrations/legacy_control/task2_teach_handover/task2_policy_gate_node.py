#!/usr/bin/env python3
"""Make any existing policy deployment interruptible, without touching it.

The deep integration (inference_pi05_rtc_task2.py) is better and should be
preferred: it lives inside the policy process, so it can drop the action chunk
and re-anchor the step limiter on resume.  But it had to be written by hand for
one runtime, and every future model would need the same surgery.

This node is the shallow alternative that costs nothing per model.  It sits
between an unmodified policy and the takeover coordinator:

    policy  --/task2/policy_raw/joint_*-->  GATE  --/task2/policy/joint_*-->  coordinator

and serves /task2/policy/set_paused on the policy's behalf.  Any deployment
script becomes Task2-capable by pointing its command topics at the gate's
inputs - for the pi0.5 scripts that is two environment variables, no code.

What it cannot do, and this matters:

  * It cannot clear the policy's internal action chunk.  A chunked policy that
    was paused mid-chunk will resume by replaying actions computed for the
    world as it was before the operator moved things.
  * It cannot re-anchor the policy's own step limiter, which ramps from the
    last command the policy issued - i.e. from where the arm used to be.

Both of those show up as the same symptom: a jump on resume.  The gate cannot
prevent the stale plan, but it can stop the jump, by rate-limiting its output
away from the arm's MEASURED position after every resume until the policy's
commands and the arm agree again.  That is what the ramp below does.  It is a
safety limiter, not a fix: the actions are still stale, they just arrive
smoothly.  Use the deep integration for anything that matters.
"""

import threading
from typing import Any, Callable, Dict, Optional

import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool, SetBoolResponse

from task2_handover_core import JOINT_NAMES, ValidationError, validate_joint


SIDES = ("left", "right")

DEFAULT_INPUT_TOPICS = {
    "left": "/task2/policy_raw/joint_left",
    "right": "/task2/policy_raw/joint_right",
}
OUTPUT_TOPICS = {
    "left": "/task2/policy/joint_left",
    "right": "/task2/policy/joint_right",
}
FRONT_FEEDBACK_TOPICS = {
    "left": "/puppet/joint_left",
    "right": "/puppet/joint_right",
}
PAUSE_SERVICE = "/task2/policy/set_paused"
STATE_TOPIC = "/task2/policy_gate/state"


class Task2PolicyGateNode:
    def __init__(
        self,
        ros_api: Any = rospy,
        wall_clock: Callable[[], float] = None,
        monotonic_clock: Callable[[], float] = None,
    ) -> None:
        import time as _time

        self.ros = ros_api
        self.wall_clock = wall_clock or _time.time
        self.monotonic_clock = monotonic_clock or _time.monotonic

        self.command_max_age_sec = self._positive_param("~command_max_age_sec", 0.25)
        self.feedback_max_age_sec = self._positive_param("~feedback_max_age_sec", 0.10)
        # Per-publish joint step used only while ramping back in after a resume.
        # Matches the pi0.5 deployments' own arm_steps_length so the gate never
        # becomes the tighter constraint during normal operation.
        self.ramp_step_rad = self._positive_param("~ramp_step_rad", 0.01)
        self.ramp_gripper_step = self._positive_param("~ramp_gripper_step", 0.2)
        # Once every joint is within this of the policy's command the ramp ends
        # and commands pass through untouched.
        self.ramp_done_tolerance_rad = self._positive_param(
            "~ramp_done_tolerance_rad", 1e-4
        )
        self.start_paused = bool(self.ros.get_param("~start_paused", True))

        self._lock = threading.RLock()
        self._paused = self.start_paused
        self._generation = 0
        self._feedback: Dict[str, Any] = {}
        self._ramping: Dict[str, bool] = {side: False for side in SIDES}
        self._last_output: Dict[str, Optional[tuple]] = {
            side: None for side in SIDES
        }
        self._dropped = 0

        input_topics = {
            side: str(
                self.ros.get_param(
                    "~input_{}_topic".format(side), DEFAULT_INPUT_TOPICS[side]
                )
            )
            for side in SIDES
        }
        self.output_pubs = {
            side: self.ros.Publisher(OUTPUT_TOPICS[side], JointState, queue_size=1)
            for side in SIDES
        }
        self.state_pub = self.ros.Publisher(
            STATE_TOPIC, String, queue_size=1, latch=True
        )
        self.pause_service = self.ros.Service(
            PAUSE_SERVICE, SetBool, self.handle_set_paused
        )
        self._subscriptions = []
        for side in SIDES:
            self._subscriptions.append(
                self.ros.Subscriber(
                    input_topics[side],
                    JointState,
                    self._command_callback(side),
                    queue_size=1,
                )
            )
            self._subscriptions.append(
                self.ros.Subscriber(
                    FRONT_FEEDBACK_TOPICS[side],
                    JointState,
                    self._feedback_callback(side),
                    queue_size=1,
                )
            )
        self.ros.logwarn(
            "Task2 policy gate is forwarding %s -> %s. It cannot clear the "
            "policy's action chunk; it only ramps the jump that a stale chunk "
            "produces on resume.",
            list(input_topics.values()),
            list(OUTPUT_TOPICS.values()),
        )
        self._publish_state()

    # ---------------------------------------------------------------- helpers

    def _positive_param(self, name: str, default: float) -> float:
        value = float(self.ros.get_param(name, default))
        if value <= 0.0 or value != value:
            raise ValueError("{} must be finite and greater than zero".format(name))
        return value

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def ramping(self, side: str) -> bool:
        with self._lock:
            return self._ramping[side]

    def _publish_state(self) -> None:
        self.state_pub.publish(
            String(
                data="paused" if self._paused else "forwarding",
            )
        )

    def _validated(self, message: JointState, max_age_sec: float):
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

    def _fresh_feedback(self, side: str):
        joint = self._feedback.get(side)
        if joint is None:
            return None
        age = self.monotonic_clock() - joint.arrival_monotonic
        if age > self.feedback_max_age_sec:
            return None
        return joint

    # -------------------------------------------------------------- callbacks

    def _feedback_callback(self, side: str):
        def callback(message: JointState) -> None:
            try:
                joint = self._validated(message, self.feedback_max_age_sec)
            except ValidationError:
                return
            with self._lock:
                self._feedback[side] = joint

        return callback

    def handle_set_paused(self, request) -> SetBoolResponse:
        requested = bool(request.data)
        with self._lock:
            was_paused = self._paused
            self._paused = requested
            self._generation += 1
            generation = self._generation
            if was_paused and not requested:
                # Resuming.  Start every side ramping from where the arm
                # actually is; the policy's first command after a takeover
                # describes where it used to be.
                for side in SIDES:
                    measured = self._fresh_feedback(side)
                    self._ramping[side] = measured is not None
                    self._last_output[side] = (
                        tuple(measured.positions) if measured is not None else None
                    )
                    if measured is None:
                        self.ros.logwarn(
                            "Task2 policy gate resuming %s without fresh "
                            "feedback; cannot ramp, forwarding directly",
                            side,
                        )
            self._publish_state()
        return SetBoolResponse(
            success=True,
            message="{} generation={}".format(
                "paused" if requested else "forwarding", generation
            ),
        )

    def _ramped(self, side: str, target: tuple) -> tuple:
        previous = self._last_output[side]
        if previous is None:
            self._ramping[side] = False
            return target
        limits = [self.ramp_step_rad] * (len(target) - 1) + [self.ramp_gripper_step]
        stepped = []
        done = True
        for value, last, limit in zip(target, previous, limits):
            delta = value - last
            if abs(delta) > limit:
                done = False
                stepped.append(last + (limit if delta > 0 else -limit))
            else:
                stepped.append(value)
            if abs(value - stepped[-1]) > self.ramp_done_tolerance_rad:
                done = False
        if done:
            self._ramping[side] = False
        return tuple(stepped)

    def _command_callback(self, side: str):
        def callback(message: JointState) -> None:
            with self._lock:
                if self._paused:
                    self._dropped += 1
                    return
                try:
                    joint = self._validated(message, self.command_max_age_sec)
                except ValidationError:
                    # Stale or malformed: dropping is right.  The gate is not
                    # the component that decides a policy is broken - it has no
                    # way to stop it - and forwarding a stale command is worse
                    # than forwarding nothing.
                    self._dropped += 1
                    return
                positions = tuple(joint.positions)
                if self._ramping[side]:
                    positions = self._ramped(side, positions)
                self._last_output[side] = positions
                self._publish(side, positions)

        return callback

    def _publish(self, side: str, positions) -> None:
        message = JointState()
        try:
            message.header.stamp = self.ros.Time.from_sec(float(self.wall_clock()))
        except AttributeError:
            message.header.stamp = type(self.ros.Time.now())(float(self.wall_clock()))
        message.name = list(JOINT_NAMES)
        message.position = list(positions)
        self.output_pubs[side].publish(message)

    def spin(self) -> None:
        self.ros.spin()


def main() -> None:
    rospy.init_node("task2_policy_gate", anonymous=False)
    Task2PolicyGateNode().spin()


if __name__ == "__main__":
    main()

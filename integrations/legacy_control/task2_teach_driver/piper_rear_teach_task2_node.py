#!/usr/bin/env python3
"""ROS1 driver for one Task2 rear Piper arm that stays a slave for its whole life.

Manual takeover is entered by the operator pressing the arm's physical
drag-teach button, never by software.  This node therefore never calls
MasterSlaveConfig(); it only watches the reported teach state, closes the
command gate while the operator owns the arm, and re-arms position control
afterwards from a hold pose supplied by the coordinator.
"""

import math
import threading
import time
from typing import Any, Optional, Sequence

import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger, TriggerResponse

from piper_msgs.msg import PiperStatusMsg
from piper_sdk import C_PiperInterface

import task2_homing_core as homing
from task2_rear_teach_core import (
    CAN_CONTROL_MODE,
    STANDBY_MODE,
    TEACHING_MODE,
    TEACH_IDLE,
    CommandValidationError,
    GateState,
    MotionGateError,
    RearTeachController,
    TeachStateTracker,
    feedback_to_joint_positions,
    joint_positions_to_piper,
    parse_bool_param,
    validate_can_port,
)


JOINT_NAMES = ["joint{}".format(index) for index in range(7)]


class PiperRearTeachNode:
    """Own one rear CAN interface; expose teach state and a fail-closed gate."""

    def __init__(self, ros_api: Any = rospy, piper_factory: Any = C_PiperInterface) -> None:
        self.ros = ros_api
        self.can_port = validate_can_port(self.ros.get_param("~can_port", ""))
        self.auto_enable = parse_bool_param(
            self.ros.get_param("~auto_enable", False), "auto_enable"
        )
        self.gripper_exist = parse_bool_param(
            self.ros.get_param("~gripper_exist", True), "gripper_exist"
        )
        self.publish_rate = self._positive_param("~publish_rate", 200.0)
        self.command_timeout_sec = self._positive_param("~command_timeout_sec", 0.25)
        self.feedback_timeout_sec = self._positive_param("~feedback_timeout_sec", 0.25)
        self.teach_stability_sec = self._positive_param("~teach_stability_sec", 0.2)
        self.mode_verify_timeout_sec = self._positive_param(
            "~mode_verify_timeout_sec", 2.0
        )
        self.mode_verify_poll_sec = self._positive_param("~mode_verify_poll_sec", 0.02)
        self.enable_verify_timeout_sec = self._positive_param(
            "~enable_verify_timeout_sec", 5.0
        )
        self.enable_verify_poll_sec = self._positive_param(
            "~enable_verify_poll_sec", 0.1
        )
        # Hard backstop on how far one hold may drive the arm.  The
        # coordinator applies the tighter, situation-specific limit; this only
        # has to be wide enough for a supervised cold arm-up and narrow enough
        # that a bad target cannot fling the arm.
        self.max_hold_delta_rad = self._positive_param("~max_hold_delta_rad", 0.60)
        self.state_publish_period_sec = 1.0 / self._positive_param(
            "~state_publish_rate", 10.0
        )
        self.startup_feedback_timeout_sec = self._positive_param(
            "~startup_feedback_timeout_sec", 5.0
        )
        # Piper leaves CAN control mode and disables its motors when commands
        # stop arriving, so an armed arm must be fed continuously even while
        # the policy has nothing new to say.
        # 30 Hz, matching the rate the stock front-arm driver is actually
        # driven at.  Streaming at 100 Hz put 600 command frames/s on top of
        # 1600 feedback frames/s, and the arm answered with
        # JOINT_COMMUNICATION_ERR across all six joints - the signature of the
        # controller losing its internal bus, not of a logic error here.
        self.command_stream_period_sec = 1.0 / self._positive_param(
            "~command_stream_rate", 30.0
        )
        # Enabling the motors provokes a short JOINT_COMMUNICATION_ERR burst
        # that clears itself in ~100 ms.  Arming must absorb it before
        # committing, or motion_ready is granted just in time for the arm to
        # trip back to standby.
        self.enable_settle_window_sec = self._positive_param(
            "~enable_settle_window_sec", 0.3
        )
        self.enable_settle_timeout_sec = self._positive_param(
            "~enable_settle_timeout_sec", 5.0
        )
        # An armed arm may blip out of CAN control mode for ~100 ms.  The
        # command stream re-asserts the mode and heals it, so only a drop that
        # OUTLASTS this grace window means the arm is really gone.
        self.ready_grace_sec = self._positive_param("~ready_grace_sec", 3.0)
        self.reenable_period_sec = self._positive_param("~reenable_period_sec", 0.2)
        # Entering CAN control provokes a disable roughly a second later, every
        # time.  It is cheaper to ride through a known, reproducible transient
        # than to detect it afterwards and heal at 5 Hz while the arm sags.
        self.entry_ride_through_sec = self._positive_param(
            "~entry_ride_through_sec", 3.0
        )
        self.entry_ride_through_period_sec = 1.0 / self._positive_param(
            "~entry_ride_through_rate", 50.0
        )
        # Standby-hold: park the arm in ctrl_mode 0x00 instead of CAN control.
        #
        # In standby the joints are held by their brakes and do not move at all
        # - measured across every dwell in every recorded cycle: zero degrees of
        # travel, err_code 0x0000, motors on.  CAN control releases the brakes
        # and holds with the servos instead, which is fine until the servos stop
        # answering, and on this firmware they always do: 0.5 s after every
        # entry the arm raises JOINT_COMMUNICATION_ERR with err_code 0x003F and
        # goes limp for about 100 ms.  That is the entire drop.
        #
        # Nothing needs the rear arms to be actuators.  They exist to be dragged
        # by the operator, and the front arms follow them through a clutch that
        # captures the offset at engagement - so the rear arms do not have to
        # match the front arms, which was the only reason they were ever driven.
        # Holding them in standby removes the CAN-control entry from the M/S
        # cycle completely, and with it the fault and the drop.
        self.hold_in_standby = bool(self.ros.get_param("~hold_in_standby", True))
        # Idle-disabled: between takeovers the rear arm carries no torque at all.
        #
        # This is deliberate, and it is what makes the takeover ergonomic. The
        # operator has to be able to carry the rear arm to a comfortable pose
        # WITHOUT the front arm copying the trip, and the clutch reference is
        # captured when the teach button engages - so any repositioning has to
        # happen before that, which means the arm has to be limp, not braked.
        # Standby brakes it; only a disabled arm can be moved by hand freely.
        #
        # The consequence is that the arm sags under gravity when it is
        # released.  That is accepted: it is the resting state of an input
        # device, not an arm that dropped out from under a running task.
        self.idle_disabled = bool(self.ros.get_param("~idle_disabled", True))
        self._was_teaching = False
        # Dwell at rest after entering CAN control, BEFORE commanding any
        # motion.  The firmware raises JOINT_COMMUNICATION_ERR exactly 0.5 s
        # after every entry and the arm goes limp for ~100 ms.  Waiting it out
        # while the arm is still lying at its gravity rest position makes that
        # window free; commanding the move first would put the fault in the
        # middle of a lift, which is a real drop from height.
        self.home_settle_sec = self._positive_param("~home_settle_sec", 1.2)
        # True once this arm has been homed and is holding in CAN control.
        # Readiness checking has to know, or it treats the arm's own homed
        # state as "fell out of control" and tries to heal it.
        self._homed = False
        # Dwell in standby before re-entering CAN control.  Measured: with
        # only ~30 ms of dwell the arm answers the mode entry with
        # JOINT_COMMUNICATION_ERR on all six joints half a second later, loses
        # control authority for ~100 ms and free-falls more than 12 deg (it
        # was stopped by the rack, so that is a lower bound).  With 0.5 s of
        # dwell the entry is completely clean: err stays 0x0000.  Standby
        # holds position while the motors are enabled, so this wait costs
        # nothing but wall clock.
        self.post_teach_settle_sec = self._positive_param(
            "~post_teach_settle_sec", 1.0
        )
        self.move_speed_percent = int(self.ros.get_param("~move_speed_percent", 20))
        if not 1 <= self.move_speed_percent <= 100:
            raise ValueError("move_speed_percent must be within 1..100")

        self.piper = piper_factory(can_name=self.can_port)
        self.piper.ConnectPort()
        # Two locks with different jobs.  _hardware_lock serialises WRITES to
        # the arm (JointCtrl / MotionCtrl / EnableArm) and is held for the
        # whole multi-second re-arm sequence.  _state_lock guards this node's
        # own gate and teach-tracker state.  SDK Get* calls read a parsed cache
        # and need neither: letting the publish loop take _hardware_lock made
        # it stall for the entire re-arm, which blacked out joint_states for
        # ~0.77 s and faulted the coordinator's post-hold sync check.
        self._hardware_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self.controller = RearTeachController(
            command_timeout_sec=self.command_timeout_sec,
            max_hold_delta_rad=self.max_hold_delta_rad,
        )
        self.teach_tracker = TeachStateTracker(
            stability_window_sec=self.teach_stability_sec,
            max_sample_age_sec=self.feedback_timeout_sec,
        )
        self._published_teach: Optional[bool] = None
        self._published_ready: Optional[bool] = None
        self._last_state_publish = 0.0
        self._last_target = None
        self._last_target_sent = 0.0
        self._unhealthy_since = None
        self._last_reenable = 0.0

        self.joint_state_pub = self.ros.Publisher(
            "~joint_states", JointState, queue_size=1
        )
        self.arm_status_pub = self.ros.Publisher(
            "~arm_status", PiperStatusMsg, queue_size=1
        )
        self.teach_active_pub = self.ros.Publisher(
            "~teach_active", Bool, queue_size=1, latch=True
        )
        self.motion_ready_pub = self.ros.Publisher(
            "~motion_ready", Bool, queue_size=1, latch=True
        )
        self.fault_pub = self.ros.Publisher(
            "~fault", String, queue_size=1, latch=True
        )
        self.command_sub = self.ros.Subscriber(
            "~joint_cmd", JointState, self.joint_command_callback, queue_size=1
        )
        self.reset_service = self.ros.Service(
            "~reset_fault", Trigger, self.handle_reset_fault
        )
        # Homing lives here, not in a standalone script, because this node owns
        # the CAN handle.  A second process opening the same bus would race the
        # DisableArm this driver issues the instant teaching ends.
        self.home_service = self.ros.Service("~home", Trigger, self.handle_home)

        self.fault_pub.publish(String(data=""))
        self._publish_gate_state(force=True)
        self._verify_startup_state()
        self.ros.loginfo(
            "Task2 rear teach arm ready: can_port=%s auto_enable=%s",
            self.can_port,
            self.auto_enable,
        )

    # ---------------------------------------------------------------- helpers

    def _positive_param(self, name: str, default: float) -> float:
        value = float(self.ros.get_param(name, default))
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("{} must be finite and greater than zero".format(name))
        return value

    def _latch_fault(self, reason: str) -> None:
        self.controller.latch_fault()
        self.fault_pub.publish(String(data=str(reason)))
        self._publish_gate_state(force=True)
        self.ros.logerr("Task2 rear teach %s fault: %s", self.can_port, reason)

    def _feedback_is_fresh(self, wrapper: Any, not_before_wall: Optional[float] = None) -> bool:
        try:
            hz = float(wrapper.Hz)
            stamp = float(wrapper.time_stamp)
        except (AttributeError, TypeError, ValueError):
            return False
        if not math.isfinite(hz) or hz <= 0.0:
            return False
        if not math.isfinite(stamp) or stamp <= 0.0:
            return False
        if not_before_wall is not None and stamp < not_before_wall:
            return False
        age = time.time() - stamp
        return -0.05 <= age <= self.feedback_timeout_sec

    def _fresh_status(self, not_before_wall: Optional[float] = None) -> Any:
        status = self.piper.GetArmStatus()
        if not self._feedback_is_fresh(status, not_before_wall=not_before_wall):
            raise RuntimeError("arm status feedback is missing or stale")
        return status

    def _wait_for_ctrl_mode(
        self,
        expected: int,
        not_before_wall: Optional[float] = None,
        keepalive: bool = False,
        enable_keepalive: bool = False,
    ) -> bool:
        """Wait for a confirmed control mode, optionally feeding the arm.

        `keepalive` matters when waiting for CAN control mode: the arm drops
        back to standby and disables itself if commands stop arriving, and the
        gap between preloading the hold and the streaming loop taking over was
        long enough to lose it whenever the motors had to be enabled first.
        """
        deadline = time.monotonic() + self.mode_verify_timeout_sec
        while True:
            status = self.piper.GetArmStatus()
            if self._feedback_is_fresh(status, not_before_wall=not_before_wall):
                if int(status.arm_status.ctrl_mode) == expected:
                    return True
            if time.monotonic() >= deadline:
                return False
            if keepalive and self._last_target is not None:
                self._send_target(self._last_target)
            if enable_keepalive:
                self._try_enable()
            time.sleep(self.mode_verify_poll_sec)

    def _go_limp(self) -> None:
        """Drop all torque so the operator can carry the arm by hand."""
        with self._hardware_lock:
            self.piper.DisableArm(7)
        # A limp arm is not holding a homed pose, whatever it was doing before.
        self._homed = False

    def _try_enable(self, force: bool = False) -> None:
        """Issue EnableArm, rate limited, swallowing transport errors."""
        now = time.monotonic()
        if not force and now - self._last_reenable < self.reenable_period_sec:
            return
        self._last_reenable = now
        try:
            with self._hardware_lock:
                self.piper.EnableArm(7)
        except Exception as exc:  # noqa: BLE001 - callers report the outcome
            self.ros.logwarn(
                "Task2 rear teach %s enable attempt failed: %s", self.can_port, exc
            )

    def _wait_for_teach_cleared(
        self, not_before_wall: float, enable_keepalive: bool = False
    ) -> bool:
        deadline = time.monotonic() + self.mode_verify_timeout_sec
        while True:
            status = self.piper.GetArmStatus()
            if self._feedback_is_fresh(status, not_before_wall=not_before_wall):
                if int(status.arm_status.teach_status) == TEACH_IDLE:
                    return True
            if time.monotonic() >= deadline:
                return False
            if enable_keepalive:
                self._try_enable()
            time.sleep(self.mode_verify_poll_sec)

    MOTOR_ERROR_FLAGS = (
        "voltage_too_low",
        "motor_overheating",
        "driver_overcurrent",
        "driver_overheating",
        "collision_status",
        "driver_error_status",
        "stall_status",
    )

    def _motor_faults(self):
        """Latched per-motor protection flags, or None if feedback is stale.

        The arm-level status can read 0x00 while a single joint driver sits
        latched in protection: a stalled joint 5 reported overcurrent,
        collision, driver_error and stall while GetArmStatus() still said the
        arm was fine.  Anything that decides whether to re-enable has to look
        here, not only at the arm-level word.
        """
        motors = self.piper.GetArmLowSpdInfoMsgs()
        if not self._feedback_is_fresh(motors):
            return None
        faults = {}
        for index in range(1, 7):
            status = getattr(motors, "motor_{}".format(index)).foc_status
            flags = [
                name
                for name in self.MOTOR_ERROR_FLAGS
                if getattr(status, name, False)
            ]
            if flags:
                faults[index] = flags
        return faults

    def _motor_fault_report(self) -> str:
        faults = self._motor_faults()
        if faults is None:
            return "motor feedback is stale"
        if not faults:
            return "no motor faults"
        return "; ".join(
            "motor {}: {}".format(index, ", ".join(flags))
            for index, flags in sorted(faults.items())
        )

    def _motor_enable_bits(self):
        """Per-motor enable flags, or None when the feedback is not fresh."""
        motors = self.piper.GetArmLowSpdInfoMsgs()
        if not self._feedback_is_fresh(motors):
            return None
        return [
            bool(
                getattr(motors, "motor_{}".format(index)).foc_status.driver_enable_status
            )
            for index in range(1, 7)
        ]

    def _disabled_motor_report(self) -> str:
        """Name the motors that are off, so a fault says which joint failed."""
        bits = self._motor_enable_bits()
        if bits is None:
            return "motor feedback is stale"
        off = [str(index + 1) for index, on in enumerate(bits) if not on]
        pattern = "".join("1" if on else "0" for on in bits)
        if not off:
            return "all motors enabled ({})".format(pattern)
        return "motor(s) {} disabled ({})".format(", ".join(off), pattern)

    def _all_motors_enabled(self) -> bool:
        bits = self._motor_enable_bits()
        return bits is not None and all(bits)

    def _ensure_enabled(self) -> None:
        deadline = time.monotonic() + self.enable_verify_timeout_sec
        while True:
            if self._all_motors_enabled():
                return
            self.piper.EnableArm(7)
            if self._all_motors_enabled():
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "enable not confirmed for {} within {:.2f}s: {}".format(
                        self.can_port,
                        self.enable_verify_timeout_sec,
                        self._disabled_motor_report(),
                    )
                )
            time.sleep(self.enable_verify_poll_sec)

    def _wait_until_healthy(self) -> bool:
        """Wait for the arm to report no fault for a continuous window.

        Measured on hardware: EnableArm is followed a few hundred milliseconds
        later by arm_status=0x05 (JOINT_COMMUNICATION_ERR) with every joint
        flagged, which clears on its own.  Whether arming succeeded came down
        to whether that burst landed before or after CAN control mode was
        confirmed - a race.  Waiting it out removes the race.
        """
        deadline = time.monotonic() + self.enable_settle_timeout_sec
        healthy_since = None
        while True:
            status = self.piper.GetArmStatus()
            if self._feedback_is_fresh(status):
                healthy = (
                    int(status.arm_status.arm_status) == 0x00
                    and int(status.arm_status.err_code) == 0
                )
                now = time.monotonic()
                if healthy:
                    if healthy_since is None:
                        healthy_since = now
                    if now - healthy_since >= self.enable_settle_window_sec:
                        return True
                else:
                    healthy_since = None
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.mode_verify_poll_sec)

    def _measured_positions(self) -> Sequence[float]:
        joint_feedback = self.piper.GetArmJointMsgs()
        if not self._feedback_is_fresh(joint_feedback):
            raise RuntimeError("joint feedback is missing or stale")
        joint = joint_feedback.joint_state
        raw = (
            joint.joint_1,
            joint.joint_2,
            joint.joint_3,
            joint.joint_4,
            joint.joint_5,
            joint.joint_6,
        )
        gripper_angle = 0
        if self.gripper_exist:
            gripper_feedback = self.piper.GetArmGripperMsgs()
            if not self._feedback_is_fresh(gripper_feedback):
                raise RuntimeError("gripper feedback is missing or stale")
            gripper_angle = gripper_feedback.gripper_state.grippers_angle
        return feedback_to_joint_positions(raw, gripper_angle)

    # ------------------------------------------------------------- lifecycle

    def _await_fresh_status(self) -> Optional[Any]:
        """Poll until the SDK has parsed enough CAN frames to report status.

        ConnectPort() returns before a single frame has been received, so Hz
        and time_stamp are still zero for the first moments of a node's life.
        Checking immediately would fault a perfectly healthy arm - which is
        exactly what happened on the first hardware bring-up.
        """
        deadline = time.monotonic() + self.startup_feedback_timeout_sec
        while True:
            status = self.piper.GetArmStatus()
            if self._feedback_is_fresh(status):
                return status
            if time.monotonic() >= deadline:
                return None
            time.sleep(self.mode_verify_poll_sec)

    def _verify_startup_state(self) -> None:
        """Confirm this arm is a working slave, and clear any teach residue.

        Clearing residue only sends the reset and standby frames.  It never
        enables motors and never commands a position, so it cannot move the arm.
        """
        with self._hardware_lock:
            status = self._await_fresh_status()
            if status is None:
                self._latch_fault(
                    "no fresh arm status within {:.1f}s of connecting to {}".format(
                        self.startup_feedback_timeout_sec, self.can_port
                    )
                )
                return
            ctrl_mode = int(status.arm_status.ctrl_mode)
            if ctrl_mode == TEACHING_MODE:
                if TeachStateTracker.raw_is_teaching(
                    ctrl_mode, status.arm_status.teach_status
                ):
                    self.ros.logwarn(
                        "Task2 rear teach %s started while the teach button is "
                        "engaged; leaving the gate closed",
                        self.can_port,
                    )
                    self.controller.set_teaching()
                    self._publish_gate_state(force=True)
                    return
                self.ros.logwarn(
                    "Task2 rear teach %s found teach-mode residue; returning it "
                    "to standby",
                    self.can_port,
                )
                try:
                    self._leave_teach_mode(keep_enabled=self.auto_enable)
                except Exception as exc:
                    self._latch_fault("could not leave teach mode: {}".format(exc))
                    return
            if self.idle_disabled:
                # The rear arms come up however the last session left them, and
                # what that is decides what is safe to do about it.
                if ctrl_mode == CAN_CONTROL_MODE and self._all_motors_enabled():
                    # Holding a homed pose.  Going limp here would drop it from
                    # wherever it was holding, which is exactly the fall the
                    # rest of this driver exists to avoid.  Adopt the state
                    # instead: it is legitimate, it just was not this process
                    # that established it.
                    self.ros.logwarn(
                        "Task2 rear teach %s came up holding a pose in CAN "
                        "control; adopting it rather than dropping the arm. "
                        "It is NOT limp - press the teach button, or home it, "
                        "before trying to move it by hand.",
                        self.can_port,
                    )
                    self._homed = True
                else:
                    if ctrl_mode == CAN_CONTROL_MODE:
                        # In CAN control but already limp.  Leaving the mode
                        # alone would make the readiness monitor keep reporting
                        # an arm that is "in control" with nothing to control.
                        self.ros.logwarn(
                            "Task2 rear teach %s came up in CAN control with "
                            "its motors off; returning it to standby",
                            self.can_port,
                        )
                        self.piper.MotionCtrl_2(STANDBY_MODE, 0x00, 0, 0x00)
                    self._go_limp()
                self.controller.set_ready()
            else:
                self.controller.set_not_ready()
            self._publish_gate_state(force=True)

    def _ride_through_entry_transient(self, raw) -> None:
        """Hold the standing target across the CAN-control entry transient.

        This used to broadcast EnableArm(7) every 50 ms for three seconds,
        because entering ctrl_mode 0x01 was observed to drop all six motors
        about a second later.  That observation belonged to the old exit path,
        which left teach mode with MotionCtrl_1(0x02, 0, 0) - a command that
        disables the motors by design.  Once the exit switched to
        MotionCtrl_1(0, 0, 0x00) the motors stopped dropping at all, and the
        ride-through was left flooding the bus for no reason.

        Flooding it is not harmless.  Enable broadcasts plus full target frames
        put roughly 140 frames/s on a bus already carrying 210 Hz of feedback,
        and a 50 Hz trace caught the result: 0.512 s after entering CAN control
        - about ten enable broadcasts in - both rear arms raised
        JOINT_COMMUNICATION_ERR with err_code 0x003F, were kicked back to
        standby for 124 ms, and sagged before recovering.  Enable bits stayed
        111111 the whole time, so the thing the ride-through existed to prevent
        was not even happening.

        Cutting the enable broadcast was tried and measured worse, so it stays.
        At 4 Hz and conditional, the same 50 Hz trace showed the fault arriving
        at exactly the same 0.508 s - bus load is not what triggers it - but
        now the enable bits really did reach 000000 for 126 ms.  The broadcast
        was not preventing the fault, it was outrunning the disable that the
        fault causes, and that is worth keeping on its own.

        So the rate is set from the event instead of from a guess: the arm is
        limp for roughly 126 ms, so a 20 ms cadence lands several enables
        inside the window.  This is symptom suppression, not a fix - the
        0.5 s JOINT_COMMUNICATION_ERR itself is still unexplained and belongs
        in a question to AgileX.
        """
        deadline = time.monotonic() + self.entry_ride_through_sec
        while time.monotonic() < deadline:
            with self._hardware_lock:
                self.piper.EnableArm(7)
            self._send_target(raw)
            time.sleep(self.entry_ride_through_period_sec)

    def _leave_teach_mode(self, keep_enabled: bool = False) -> bool:
        """Take the arm out of drag teaching WITHOUT dropping its motors.

        Four ways out were measured from inside real teach mode
        (ctrl_mode=0x02, teach_status=0x02, motors on):

          MotionCtrl_2(0x00)        leaves teach mode, DISABLES all six motors
          MotionCtrl_1(0x02, 0, 0)  clears teach_status, DISABLES all six motors
          MotionCtrl_1(0, 0, 0x02)  no effect at all from inside teach mode
          MotionCtrl_1(0, 0, 0x00)  leaves teach mode, clears teach_status,
                                    motors stay on.  0.3 s.

        Only the last one is the command this actually wants: grag_teach_ctrl
        0x00 means "turn drag teaching off".  The earlier implementations used
        "resume from emergency stop" and "switch to standby", which is why the
        rear arms went limp and visibly dropped on every single M/S cycle.

        MotionCtrl_2(0x01) is ignored while ctrl_mode is 0x02, so teach mode
        has to be left explicitly before position control can be re-entered.
        """
        status = self.piper.GetArmStatus()
        if self._feedback_is_fresh(status):
            if int(status.arm_status.ctrl_mode) != TEACHING_MODE:
                # Nothing to leave.  This runs on every re-arm, including ones
                # where the arm is already in standby or still in CAN control,
                # and demanding a transition there would just time out.
                return False

        request_wall = time.time()
        self.piper.MotionCtrl_1(0x00, 0x00, 0x00)
        deadline = time.monotonic() + self.mode_verify_timeout_sec
        while True:
            status = self.piper.GetArmStatus()
            if self._feedback_is_fresh(status, not_before_wall=request_wall):
                if int(status.arm_status.ctrl_mode) != TEACHING_MODE:
                    return True
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "arm did not leave teach mode after grag_teach off"
                )
            if keep_enabled:
                self._try_enable()
            time.sleep(self.mode_verify_poll_sec)

    def _send_target(self, raw) -> None:
        """Push one target, re-asserting CAN control mode around it.

        The stock piper_start_ms_node.py re-sends MotionCtrl_2 before AND
        after every command, and that is why the front arms never get stranded.
        Measured here: the arm emits a ~100 ms JOINT_COMMUNICATION_ERR burst,
        drops to standby, recovers its health - and then just stays in standby,
        because nothing tells it to go back. Re-asserting the mode with every
        command makes that blip self-healing instead of terminal.
        """
        with self._hardware_lock:
            self.piper.MotionCtrl_2(
                CAN_CONTROL_MODE, 0x01, self.move_speed_percent, 0x00
            )
            self.piper.JointCtrl(*raw[:6])
            if self.gripper_exist:
                self.piper.GripperCtrl(raw[6], 1000, 0x01, 0)
            self.piper.MotionCtrl_2(
                CAN_CONTROL_MODE, 0x01, self.move_speed_percent, 0x00
            )
            self._last_target = tuple(raw)
            self._last_target_sent = time.monotonic()

    def _stream_last_target(self, ctrl_mode: Optional[int] = None) -> None:
        """Re-send the standing target so the arm stays in CAN control mode.

        Two things must both be true to send.  Readiness stops a stale target
        being replayed into an arm the operator owns.  The RAW ctrl_mode stops
        the stream the instant the arm reports drag teaching: every sent frame
        re-asserts CAN control mode, so streaming through a teach engagement
        yanks the arm straight back out of it and the operator physically
        cannot hold it.  The debounced teach flag is far too slow for this -
        it settles in 200 ms, and the stream would have fought the operator
        a dozen times by then.
        """
        if self.idle_disabled or self.hold_in_standby:
            # Every frame _send_target emits re-asserts CAN control, which is
            # exactly the mode standby-hold exists to stay out of.
            return
        if ctrl_mode == TEACHING_MODE:
            return
        if self._last_target is None or not self.controller.motion_ready:
            return
        if time.monotonic() - self._last_target_sent < self.command_stream_period_sec:
            return
        self._send_target(self._last_target)

    def _rearm_with_hold(self, hold_positions: Sequence[float]) -> None:
        """Re-enter position control with `hold_positions` preloaded as the target.

        Ordering matters and was established on hardware: without the
        MotionCtrl_1(0x02) reset the arm silently refuses to leave standby, and
        without preloading the target first it would drive to whatever stale
        goal the previous session left behind.
        """
        measured = self._measured_positions()
        if not self.hold_in_standby:
            # Only meaningful when the arm is about to be commanded to the hold.
            # In standby-hold nothing drives the rear arms, so they drift away
            # from the front arms by design and this check would fail on a
            # perfectly healthy arm.
            self.controller.check_hold_is_reachable(hold_positions, measured)
        self._leave_teach_mode(keep_enabled=self.auto_enable)

        # Enable BEFORE loading a target.  Enabling in standby cannot move the
        # arm, and doing it first keeps the unfed window between the preload
        # and the streaming loop as short as possible.
        #
        # auto_enable decides whether this driver may enable the motors.  It
        # must never decide whether the enable state is CHECKED: motion_ready
        # has to mean "this arm will actually execute commands", otherwise the
        # coordinator routes policy commands to a limp arm and the four arms
        # desynchronize silently.
        # Dwell in standby before asking for CAN control, ALWAYS - not only
        # when coming out of teach mode.  Every clean entry measured on
        # hardware had a dwell of at least half a second; entering with ~30 ms
        # produced the joint-communication burst and the fall, and entering
        # with no dwell at all was simply refused ("CAN/MoveJ mode not
        # confirmed").  The dwell is free: standby holds position.
        #
        # Broadcast the enable through the whole dwell, UNCONDITIONALLY - even
        # when every motor already reports itself enabled.
        #
        # Arming out of a cold launch never showed the 0.5 s
        # JOINT_COMMUNICATION_ERR; leaving teach mode always did.  The two go
        # through this same function, and the only branch that differs is this
        # one: at launch the motors are off, so the enable path runs, while
        # after teach they are already on - MotionCtrl_1(0, 0, 0x00) keeps them
        # on - so it used to be skipped entirely.  The reading is that a joint
        # coming out of drag teaching needs to be re-initialised before it will
        # talk on a fresh CAN-control session, and that an enable is what does
        # the re-initialising.
        #
        # Enabling an already-enabled motor is a no-op that cannot move or drop
        # the arm, which is what makes this cheap enough to just always do.  It
        # is emphatically NOT the same as MotionCtrl_1(0x02, 0, 0), which also
        # clears the joint state but by dropping all six motors first.
        # This dwell once broadcast EnableArm on the theory that a joint leaving
        # drag teaching needs re-initialising before it will talk on a fresh
        # CAN-control session.  Measured on hardware: no effect whatsoever, the
        # fault still arrived at 0.495 s.  The broadcast is gone; the dwell
        # stays, because standby demonstrably holds position and costs nothing.
        time.sleep(self.post_teach_settle_sec)

        needed_enabling = not self._all_motors_enabled()
        if self.auto_enable:
            self._ensure_enabled()
        elif needed_enabling:
            raise RuntimeError(
                "motors are not enabled and auto_enable is false ({}); this arm "
                "cannot execute commands, so it will not report motion_ready".format(
                    self._disabled_motor_report()
                )
            )

        # Always wait for health now, because the dwell above always enables.
        # The old version skipped this wait whenever the motors were already on,
        # justified by standby "not holding position" - a 50 Hz trace has since
        # measured the opposite: across a full second of standby the joints did
        # not move at all, err_code stayed 0x0000, and every motor stayed on.
        # Standby holds. The sag comes from the 0.5 s fault after CAN-control
        # entry, not from time spent waiting here.
        if self.auto_enable and not self._wait_until_healthy():
            status = self.piper.GetArmStatus()
            raise RuntimeError(
                "arm did not settle after enable: arm_status=0x{:02X} "
                "err_code=0x{:04X}".format(
                    int(status.arm_status.arm_status),
                    int(status.arm_status.err_code),
                )
            )

        if self.hold_in_standby:
            # Done.  The arm is out of teach mode, enabled, healthy, and held by
            # its brakes exactly where the operator left it.  No target is sent
            # and CAN control is never entered, so there is nothing to drop.
            status = self.piper.GetArmStatus()
            ctrl_mode = int(status.arm_status.ctrl_mode)
            if ctrl_mode != STANDBY_MODE:
                raise RuntimeError(
                    "expected standby hold but arm is in ctrl_mode 0x{:02X}".format(
                        ctrl_mode
                    )
                )
            self.controller.set_ready()
            return

        raw = joint_positions_to_piper(hold_positions)
        self._send_target(raw)

        motion_wall = time.time()
        self.piper.MotionCtrl_2(
            CAN_CONTROL_MODE, 0x01, self.move_speed_percent, 0x00
        )
        if not self._wait_for_ctrl_mode(
            CAN_CONTROL_MODE, not_before_wall=motion_wall, keepalive=True
        ):
            # The arm can sit in a latched state that refuses CAN control while
            # every status field reads clean: ctrl_mode 0x00, teach_status 0x00,
            # arm_status 0x00, err_code 0x0000, all motors enabled, feedback at
            # 210 Hz - and five seconds of continuous MotionCtrl_2(0x01) does
            # nothing.  MotionCtrl_1(0x02, 0, 0) is the only thing measured to
            # clear it, at the cost of dropping all six motors.
            #
            # That cost is why this is a fallback and not part of the normal
            # path: a healthy teach-mode handover never needs it, so the arm
            # never goes limp during routine M/S.
            self.ros.logwarn(
                "Task2 rear teach %s refused CAN control; clearing the latched "
                "state (this drops the motors briefly)",
                self.can_port,
            )
            self.piper.MotionCtrl_1(0x02, 0, 0)
            time.sleep(0.5)
            if self.auto_enable:
                self._ensure_enabled()
            time.sleep(self.post_teach_settle_sec)
            self._send_target(raw)
            retry_wall = time.time()
            self.piper.MotionCtrl_2(
                CAN_CONTROL_MODE, 0x01, self.move_speed_percent, 0x00
            )
            if not self._wait_for_ctrl_mode(
                CAN_CONTROL_MODE, not_before_wall=retry_wall, keepalive=True
            ):
                raise RuntimeError(
                    "CAN/MoveJ mode not confirmed even after clearing the "
                    "latched state"
                )
        if self.auto_enable:
            self._ride_through_entry_transient(raw)
        if not self._all_motors_enabled():
            raise RuntimeError(
                "motor enable state was lost while entering CAN/MoveJ: {}".format(
                    self._disabled_motor_report()
                )
            )
        self.controller.set_ready()

    # -------------------------------------------------------------- callbacks

    def handle_home(self, _request: Any) -> TriggerResponse:
        """Drive this rear arm to its configured home pose and hold it there.

        This is the one place the rear arms are allowed into CAN control, and
        it is safe for a specific reason: the move starts from wherever the
        operator parked the limp arm, which is its gravity rest position.  The
        0.5 s JOINT_COMMUNICATION_ERR that follows every CAN-control entry is
        absorbed there, at rest, where 100 ms without torque changes nothing.
        Only then does the arm move.

        It stays in CAN control holding the pose afterwards, so that when the
        operator presses the teach button the clutch reference is captured with
        the rear arm already matching the front one - which is the whole reason
        to home it.

        The target comes from a ROS parameter rather than from the service call
        because std_srvs has no message that carries a pose, and a custom
        service would mean rebuilding the piper package.  The CLI writes the
        parameter immediately before calling.
        """
        param = "/task2/homing/rear_{}".format(
            "left" if self.can_port.endswith("left") else "right"
        )
        with self._state_lock:
            gate = self.controller.gate
        if gate is GateState.FAULT:
            return TriggerResponse(
                success=False, message="driver is faulted; reset it first"
            )
        if gate is GateState.TEACHING:
            return TriggerResponse(
                success=False,
                message="refusing to home while the operator is teaching this arm",
            )
        if not self.auto_enable:
            return TriggerResponse(
                success=False,
                message="auto_enable is false; this driver may not enable motors",
            )

        raw_target = self.ros.get_param(param, None)
        if raw_target is None:
            return TriggerResponse(success=False, message="{} is not set".format(param))
        try:
            target = homing.validate_pose(raw_target, param)
            joint_speed, gripper_speed, rate = homing.speed_settings(
                {"speed": self.ros.get_param("/task2/homing/speed", {})}
            )
            joint_step, gripper_step = homing.step_limits(
                joint_speed, gripper_speed, rate
            )
        except homing.HomingError as exc:
            return TriggerResponse(success=False, message=str(exc))

        try:
            if not self._all_motors_enabled():
                # Leaving teach mode latches this arm into refusing CAN control
                # nearly every time, and discovering that costs a full
                # mode_verify_timeout_sec (2 s by default) of waiting for a mode
                # change that will never arrive.  Clear it up front instead.
                #
                # Gated on the arm being limp, which is what "not enabled"
                # means here: resume-from-emergency-stop drops every motor, so
                # doing it to an arm that is holding a pose would drop it from
                # height.  A limp arm is already at its resting pose and cannot
                # fall any further.
                self.ros.loginfo(
                    "Task2 rear teach %s clearing the post-teach latch before "
                    "asking for CAN control (expected, free at rest)",
                    self.can_port,
                )
                with self._hardware_lock:
                    self.piper.MotionCtrl_1(0x02, 0, 0)
                time.sleep(0.3)
            self._ensure_enabled()
            if not self._wait_until_healthy():
                return TriggerResponse(
                    success=False, message="arm did not become healthy after enable"
                )

            # Declare the intent BEFORE asking for the mode.  The publish loop
            # runs at 200 Hz and will see ctrl_mode 0x01 the moment the arm
            # accepts it; if the flag is still false at that instant it reports
            # the arm as having fallen out of control and starts healing it.
            with self._state_lock:
                self._homed = True
            motion_wall = time.time()
            with self._hardware_lock:
                self.piper.MotionCtrl_2(
                    CAN_CONTROL_MODE, 0x01, self.move_speed_percent, 0x00
                )
            if not self._wait_for_ctrl_mode(
                CAN_CONTROL_MODE, not_before_wall=motion_wall, keepalive=True
            ):
                # Leaving teach mode can leave the arm in a latched state that
                # refuses CAN control while every status field reads clean:
                # ctrl_mode 0x00, teach_status 0x00, arm_status 0x00, err_code
                # 0x0000, all motors enabled, feedback at 210 Hz - and seconds
                # of continuous MotionCtrl_2(0x01) do nothing.  Resume-from-
                # emergency-stop is the only thing measured to clear it.
                #
                # In the arming path that command is a reluctant last resort
                # because it drops all six motors.  Here it is nearly free: this
                # whole service only ever runs with the arm lying at its gravity
                # rest position, which is exactly why homing is safe at all.
                self.ros.loginfo(
                    "Task2 rear teach %s still refused CAN control; clearing "
                    "the latch again (expected on this firmware, free at rest)",
                    self.can_port,
                )
                with self._hardware_lock:
                    self.piper.MotionCtrl_1(0x02, 0, 0)
                time.sleep(0.5)
                self._ensure_enabled()
                if not self._wait_until_healthy():
                    return TriggerResponse(
                        success=False,
                        message="arm did not recover after clearing the latch",
                    )
                retry_wall = time.time()
                with self._hardware_lock:
                    self.piper.MotionCtrl_2(
                        CAN_CONTROL_MODE, 0x01, self.move_speed_percent, 0x00
                    )
                if not self._wait_for_ctrl_mode(
                    CAN_CONTROL_MODE, not_before_wall=retry_wall, keepalive=True
                ):
                    return TriggerResponse(
                        success=False,
                        message="arm refused CAN control even after clearing the latch",
                    )

            # At rest, on purpose.  See the docstring.
            time.sleep(self.home_settle_sec)

            # Now recover FROM the fault, do not assume it healed itself.
            #
            # It looked self-healing in earlier traces - kicked to standby,
            # motors off, back in CAN control ~100 ms later - but that recovery
            # was being carried by _send_target re-asserting the mode on every
            # streamed frame.  The dwell above deliberately sends nothing, so
            # there is nothing to carry it: the arm stays in standby with its
            # six joints disabled, the gripper (a separate driver, untouched by
            # this fault) stays powered, and every subsequent JointCtrl lands on
            # a limp arm without producing an error.
            if not self._all_motors_enabled():
                self.ros.loginfo(
                    "Task2 rear teach %s lost its motors to the entry "
                    "transient; re-enabling before moving (expected: the "
                    "firmware faults 0.5s after every CAN-control entry)",
                    self.can_port,
                )
                self._ensure_enabled()
                if not self._wait_until_healthy():
                    with self._state_lock:
                        self._homed = False
                    return TriggerResponse(
                        success=False,
                        message="motors did not come back after the entry transient",
                    )
            recover_wall = time.time()
            with self._hardware_lock:
                self.piper.MotionCtrl_2(
                    CAN_CONTROL_MODE, 0x01, self.move_speed_percent, 0x00
                )
            if not self._wait_for_ctrl_mode(
                CAN_CONTROL_MODE, not_before_wall=recover_wall, keepalive=True
            ):
                with self._state_lock:
                    self._homed = False
                return TriggerResponse(
                    success=False,
                    message="arm did not return to CAN control after the entry transient",
                )

            start = homing.validate_pose(self._measured_positions(), "measured")
            waypoints = homing.plan_ramp(start, target, joint_step, gripper_step)
            self.ros.loginfo(
                "Task2 rear teach %s homing: %d waypoints, about %.1fs",
                self.can_port,
                len(waypoints),
                homing.ramp_duration_sec(len(waypoints), rate),
            )
            period = 1.0 / rate
            for point in waypoints:
                with self._state_lock:
                    if self.controller.gate is GateState.TEACHING:
                        # The operator grabbed the arm mid-move.  Their hands
                        # outrank a homing request; stop where we are.
                        return TriggerResponse(
                            success=False, message="homing aborted: teaching engaged"
                        )
                self._send_target(joint_positions_to_piper(point))
                time.sleep(period)
            if not waypoints:
                self._send_target(joint_positions_to_piper(target))
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            self._latch_fault("homing failed: {}".format(exc))
            return TriggerResponse(
                success=False, message="homing failed: {}".format(exc)
            )

        # Verify, do not assume.  Without this the service reports success
        # while the arm is lying limp exactly where it started.
        if not self._all_motors_enabled():
            with self._state_lock:
                self._homed = False
            return TriggerResponse(
                success=False,
                message="homing finished but the arm is not holding: {}".format(
                    self._disabled_motor_report()
                ),
            )
        status = self.piper.GetArmStatus()
        if int(status.arm_status.ctrl_mode) != CAN_CONTROL_MODE:
            with self._state_lock:
                self._homed = False
            return TriggerResponse(
                success=False,
                message="homing finished but the arm left CAN control "
                "(ctrl_mode=0x{:02X})".format(int(status.arm_status.ctrl_mode)),
            )
        with self._state_lock:
            self.controller.set_ready()
        return TriggerResponse(
            success=True,
            message="homed and holding in CAN control; press the teach button when ready",
        )

    def handle_reset_fault(self, _request: Any) -> TriggerResponse:
        """Re-open the command gate after a latched driver fault.

        Without this the only way out of a driver fault was restarting the
        whole launch, because a faulted gate silently drops every command -
        including the hold the coordinator sends to re-arm.

        This clears software state only.  It does not enable motors, does not
        leave drag teaching, does not command a position and does not move the
        arm: the gate returns to NOT_READY, so a fresh hold is still required
        before the arm will accept anything.
        """
        with self._state_lock:
            self.controller.clear_fault()
            self._last_target = None
            self.fault_pub.publish(String(data=""))
            self._publish_gate_state(force=True)
            gate = self.controller.gate.value
        self.ros.logwarn(
            "Task2 rear teach %s fault reset; gate=%s (a fresh hold is required)",
            self.can_port,
            gate,
        )
        return TriggerResponse(
            success=True,
            message="{} fault cleared; gate={}".format(self.can_port, gate),
        )

    def joint_command_callback(self, message: JointState) -> None:
        with self._hardware_lock:
            gate = self.controller.gate
            if gate is GateState.TEACHING:
                # Expected during manual takeover; not a fault, just refused.
                return
            if gate is GateState.FAULT:
                return
            try:
                positions = list(message.position)
                stamp = message.header.stamp.to_sec()
                if gate in (GateState.NOT_READY, GateState.UNKNOWN):
                    # The first fresh command after teaching is the re-arm hold.
                    if len(positions) != 7:
                        raise CommandValidationError(
                            "hold must contain exactly 7 values"
                        )
                    self._rearm_with_hold(positions)
                    self._publish_gate_state()
                    return
                if self.idle_disabled:
                    # There is nothing this driver is allowed to do with a
                    # position command any more.  The rear arm is an input
                    # device: the operator moves it, the coordinator reads it.
                    return
                if self.hold_in_standby:
                    # The arm is a sensor in this mode, not an actuator.  The
                    # coordinator is not supposed to send anything here, but a
                    # stray command must not be what puts it back into CAN
                    # control.
                    return
                raw = self.controller.encode_command(
                    positions, now_sec=time.time(), stamp_sec=stamp
                )
            except MotionGateError:
                return
            except Exception as exc:
                self._latch_fault("rejected joint command: {}".format(exc))
                return
            try:
                self._send_target(raw)
            except Exception as exc:
                self._latch_fault("failed to send joint command: {}".format(exc))

    # ------------------------------------------------------------- publishing

    def _publish_gate_state(self, force: bool = False) -> None:
        """Publish the gate flags on change, and periodically regardless.

        The coordinator only accepts teach reports that arrived after it began
        waiting, so a latched on-change-only topic would leave it waiting
        forever whenever the state it needs is already the current one.
        """
        teach = self.controller.gate is GateState.TEACHING
        ready = self.controller.motion_ready
        now = time.monotonic()
        if now - self._last_state_publish >= self.state_publish_period_sec:
            force = True
        if force or teach != self._published_teach:
            self.teach_active_pub.publish(Bool(data=teach))
            self._published_teach = teach
            self._last_state_publish = now
        if force or ready != self._published_ready:
            self.motion_ready_pub.publish(Bool(data=ready))
            self._published_ready = ready
            self._last_state_publish = now

    def _verify_still_ready(self, status: Any) -> None:
        """Keep motion_ready honest after it has been granted.

        Readiness was originally latched once and never revisited, so an arm
        that fell back to standby kept advertising itself as armed.  A fresh
        contradiction now closes the gate instead.
        """
        if not self.controller.motion_ready:
            self._unhealthy_since = None
            return
        ctrl_mode = int(status.arm_status.ctrl_mode)
        # TEACHING_MODE is excluded: the debounced teach tracker owns that
        # transition and closes the gate itself a moment later.
        if self.idle_disabled:
            # Idle is standby with the motors OFF.  Requiring them ON here is
            # what the check used to do, and it would fault this arm on every
            # single cycle now that limp IS the resting state.  What still
            # matters is that nothing put the arm back into CAN control behind
            # our backs, and that no motor is reporting a real fault.
            allowed = [STANDBY_MODE, TEACHING_MODE]
            if self._homed:
                allowed.append(CAN_CONTROL_MODE)
            bad_mode = ctrl_mode not in allowed
            bad_enable = bool(self._motor_faults())
        else:
            holding_mode = (
                STANDBY_MODE if self.hold_in_standby else CAN_CONTROL_MODE
            )
            bad_mode = ctrl_mode not in (holding_mode, TEACHING_MODE)
            bad_enable = not self._all_motors_enabled()
        if not (bad_mode or bad_enable):
            self._unhealthy_since = None
            return

        now = time.monotonic()
        if self._unhealthy_since is None:
            self._unhealthy_since = now
            self._last_reenable = 0.0
            self.ros.logwarn(
                "Task2 rear teach %s is in an unexpected state (ctrl_mode="
                "0x%02X, %s); trying to heal it",
                self.can_port,
                ctrl_mode,
                self._disabled_motor_report(),
            )

        # Re-enable, the same way the stream re-asserts the control mode.  The
        # arm disables its motors during the transient, clears the error and
        # then simply stays disabled: nothing brings it back on its own, and
        # the stock front-arm driver never hits this because nothing ever
        # disables the front arms.
        #
        # Only heal a HEALTHY arm.  If it is reporting a fault - a collision,
        # a limit - re-enabling would be fighting it, so let it fault instead.
        motor_faults = self._motor_faults()
        arm_healthy = (
            int(status.arm_status.arm_status) == 0x00
            and int(status.arm_status.err_code) == 0
            # A latched joint driver must never be re-enabled by software.
            # It tripped on overcurrent, collision or stall for a reason, and
            # hammering EnableArm at it just repeats whatever caused the trip.
            and motor_faults is not None
            and not motor_faults
        )
        if bad_enable and arm_healthy and (
            now - self._last_reenable >= self.reenable_period_sec
        ):
            self._last_reenable = now
            try:
                with self._hardware_lock:
                    self.piper.EnableArm(7)
            except Exception as exc:  # noqa: BLE001 - reported by the fault below
                self.ros.logwarn(
                    "Task2 rear teach %s re-enable attempt failed: %s",
                    self.can_port,
                    exc,
                )

        if now - self._unhealthy_since < self.ready_grace_sec:
            return
        reason = (
            "arm stayed in an unexpected control mode (ctrl_mode=0x{:02X})".format(ctrl_mode)
            if bad_mode
            else "armed arm lost motor enable: {} [{}]".format(
                self._disabled_motor_report(), self._motor_fault_report()
            )
        )
        self._latch_fault(
            "{} for more than {:.2f}s".format(reason, self.ready_grace_sec)
        )

    def _update_teach_state(self, status: Any) -> None:
        now = time.monotonic()
        self.teach_tracker.offer(
            status.arm_status.ctrl_mode, status.arm_status.teach_status, now
        )
        state = self.teach_tracker.state(now)
        # Edge-detect on the TRACKER, not on the gate.  During the transition
        # the tracker reports None for a moment while it re-debounces, and that
        # None drives the gate to UNKNOWN - so by the time the state settles to
        # False the gate is no longer TEACHING and an "if gate is TEACHING"
        # test silently misses the edge.  On hardware that means the arm never
        # gets its disable and stays braked in the pose the operator is trying
        # to move it out of.
        if state is not None:
            was_teaching = self._was_teaching
            self._was_teaching = bool(state)
            if state:
                # Teaching took the arm out of CAN control; it is no longer
                # holding the homed pose and must not be treated as if it were.
                self._homed = False
            if was_teaching and not state and self.idle_disabled:
                self.ros.loginfo(
                    "Task2 rear teach %s left physical drag teaching; going limp",
                    self.can_port,
                )
                self._go_limp()
                self.controller.set_ready()
                self._publish_gate_state()
                return
        if state is None:
            if self.controller.gate is not GateState.FAULT:
                self.controller.set_unknown()
            self._publish_gate_state()
            return
        if state and self.controller.gate is not GateState.TEACHING:
            self.ros.loginfo(
                "Task2 rear teach %s entered physical drag teaching", self.can_port
            )
            self.controller.set_teaching()
        elif not state and self.controller.gate is GateState.TEACHING:
            self.ros.loginfo(
                "Task2 rear teach %s left physical drag teaching; awaiting hold",
                self.can_port,
            )
            self.controller.set_not_ready()
        self._publish_gate_state()

    def spin(self) -> None:
        rate = self.ros.Rate(self.publish_rate)
        while not self.ros.is_shutdown():
            try:
                self._publish_once()
            except Exception as exc:
                self._latch_fault("publish loop failed: {}".format(exc))
            rate.sleep()

    def _publish_once(self) -> None:
        # Deliberately does NOT take _hardware_lock: feedback must keep
        # flowing at 200 Hz even while a re-arm holds the arm for seconds.
        status = self.piper.GetArmStatus()
        fresh = self._feedback_is_fresh(status)
        with self._state_lock:
            if fresh:
                self._update_teach_state(status)
                self._verify_still_ready(status)
            else:
                self.teach_tracker.invalidate()
                if self.controller.gate is not GateState.FAULT:
                    was_ready = self.controller.motion_ready
                    self.controller.set_unknown()
                    if was_ready:
                        # Losing readiness used to happen silently here: no
                        # log, no fault, no grace window.  An armed arm would
                        # just go quiet while the coordinator carried on
                        # believing it was executing commands.
                        self._latch_fault(
                            "armed arm lost fresh status feedback ({})".format(
                                self._disabled_motor_report()
                            )
                        )
                self._publish_gate_state()
        if not fresh:
            return

        self._stream_last_target(ctrl_mode=int(status.arm_status.ctrl_mode))

        message = PiperStatusMsg()
        message.ctrl_mode = status.arm_status.ctrl_mode
        message.arm_status = status.arm_status.arm_status
        message.mode_feedback = status.arm_status.mode_feed
        message.teach_status = status.arm_status.teach_status
        message.motion_status = status.arm_status.motion_status
        message.trajectory_num = status.arm_status.trajectory_num
        message.err_code = status.arm_status.err_code
        self.arm_status_pub.publish(message)

        try:
            positions = self._measured_positions()
        except Exception:
            return

        joint_state = JointState()
        joint_state.header.stamp = self.ros.Time.now()
        joint_state.name = list(JOINT_NAMES)
        joint_state.position = list(positions)
        joint_state.velocity = [0.0] * 7
        joint_state.effort = [0.0] * 7
        self.joint_state_pub.publish(joint_state)


def main() -> None:
    rospy.init_node("piper_rear_teach_task2_node", anonymous=False)
    node = PiperRearTeachNode()
    node.spin()


if __name__ == "__main__":
    main()

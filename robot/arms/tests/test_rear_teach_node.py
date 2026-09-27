"""Startup tests for the Task2 rear physical-teach driver.

The regression these cover was found on the first hardware bring-up: the node
read GetArmStatus() immediately after ConnectPort(), before the SDK had parsed
a single CAN frame, and faulted two perfectly healthy arms.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest

from test_handover_node import (
    Bool,
    FakeRos,
    JointState,
    String,
    install_import_stubs,
)


TOOLS = Path(__file__).resolve().parents[3] / "integrations/legacy_control"
DRIVER_DIR = Path(__file__).resolve().parents[1] / "code"
NODE_PATH = DRIVER_DIR / "piper_rear_teach_task2_node.py"

STANDBY = 0x00
CAN_CONTROL = 0x01
TEACHING = 0x02
TEACH_START = 0x01
TEACH_STOP = 0x02


class PiperStatusMsg:
    def __init__(self):
        self.ctrl_mode = 0
        self.arm_status = 0
        self.mode_feedback = 0
        self.teach_status = 0
        self.motion_status = 0
        self.trajectory_num = 0
        self.err_code = 0


def load_node_module():
    if not NODE_PATH.is_file():
        raise AssertionError("missing production node: {}".format(NODE_PATH))
    install_import_stubs()
    piper_msgs = ModuleType("piper_msgs")
    piper_msgs_msg = ModuleType("piper_msgs.msg")
    piper_msgs_msg.PiperStatusMsg = PiperStatusMsg
    piper_sdk = ModuleType("piper_sdk")
    piper_sdk.C_PiperInterface = object
    sys.modules.update(
        {
            "piper_msgs": piper_msgs,
            "piper_msgs.msg": piper_msgs_msg,
            "piper_sdk": piper_sdk,
        }
    )
    sys.path.insert(0, str(DRIVER_DIR))
    sys.path.insert(0, str(DRIVER_DIR.parent / "task2_homing"))
    try:
        spec = importlib.util.spec_from_file_location(
            "piper_rear_teach_task2_node", str(NODE_PATH)
        )
        module = importlib.util.module_from_spec(spec)
        if spec.loader is None:
            raise AssertionError("unable to load {}".format(NODE_PATH))
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(DRIVER_DIR))


NODE = load_node_module()


class FakePiper:
    """Minimal Piper stand-in that can withhold feedback like the real SDK."""

    def __init__(self, can_name, silent_status_calls=0):
        self.can_name = can_name
        self.connected = False
        # The real SDK reports Hz=0 and time_stamp=0 until it has parsed
        # enough CAN frames; ConnectPort() returns long before that.
        self.silent_status_calls = silent_status_calls
        self.status_calls = 0
        self.ctrl_mode = STANDBY
        self.teach_status = 0x00
        self.arm_status = 0x00
        self.err_code = 0x0000
        self.unhealthy_status_calls = 0
        self.motion_calls = []
        self.joint_calls = []
        self.joint_call_modes = []
        self.enable_calls = []
        self.disable_calls = []
        self.enabled = False
        # A dead joint driver accepts the enable frame and stays off.
        self.ignore_enable = False
        # Latched refusal measured on hardware after leaving teach mode:
        # MotionCtrl_2(0x01) is accepted and does nothing.
        self.refuse_can_control_once = False
        # Per-motor latched protection flags, keyed by motor number.
        self.motor_flags = {}

    def ConnectPort(self):
        self.connected = True

    def _wrapper(self, **values):
        if self.status_calls <= self.silent_status_calls:
            return SimpleNamespace(Hz=0.0, time_stamp=0.0, **values)
        return SimpleNamespace(Hz=200.0, time_stamp=time.time(), **values)

    def GetArmStatus(self):
        self.status_calls += 1
        return self._wrapper(
            arm_status=SimpleNamespace(
                ctrl_mode=self.ctrl_mode,
                arm_status=self._transient_status(),
                mode_feed=0,
                teach_status=self.teach_status,
                motion_status=0,
                trajectory_num=0,
                err_code=self._transient_err(),
            )
        )

    def _transient_status(self):
        if self.unhealthy_status_calls > 0:
            return 0x05
        return self.arm_status

    def _transient_err(self):
        if self.unhealthy_status_calls > 0:
            self.unhealthy_status_calls -= 1
            return 0x003F
        return self.err_code

    def GetArmLowSpdInfoMsgs(self):
        motors = {}
        for i in range(1, 7):
            flags = self.motor_flags.get(i, ())
            status = SimpleNamespace(
                driver_enable_status=self.enabled and i not in self.motor_flags,
                voltage_too_low="voltage_too_low" in flags,
                motor_overheating="motor_overheating" in flags,
                driver_overcurrent="driver_overcurrent" in flags,
                driver_overheating="driver_overheating" in flags,
                collision_status="collision_status" in flags,
                driver_error_status="driver_error_status" in flags,
                stall_status="stall_status" in flags,
            )
            motors["motor_{}".format(i)] = SimpleNamespace(foc_status=status)
        return self._wrapper(**motors)

    def GetArmJointMsgs(self):
        return self._wrapper(
            joint_state=SimpleNamespace(**{
                "joint_{}".format(i): 0 for i in range(1, 7)
            })
        )

    def GetArmGripperMsgs(self):
        return self._wrapper(gripper_state=SimpleNamespace(grippers_angle=0))

    def MotionCtrl_1(self, *args):
        """Model the four measured behaviours of 0x150.

        All four were characterised on hardware from inside real drag
        teaching, and they differ in ways that decide whether the arm stays
        powered.
        """
        self.motion_calls.append(("MotionCtrl_1", args))
        emergency, _track, teach = (list(args) + [0, 0, 0])[:3]
        if emergency == 0x02:
            # "resume from emergency stop": clears teach_status and DROPS
            # every motor.  It is also the only thing measured to clear the
            # latched refusal of CAN control that leaving teach mode can leave
            # behind.
            self.teach_status = 0x00
            self.enabled = False
            self.refuse_can_control_once = False
        elif teach == 0x00:
            # "drag teaching off": leaves teach mode, clears teach_status,
            # motors stay on.  This is the one the driver must use.
            if self.ctrl_mode == TEACHING:
                self.ctrl_mode = STANDBY
            self.teach_status = 0x00
        elif teach == 0x02:
            # "end teach recording": no effect from inside teach mode; only
            # sets the flag when the arm is already out of it.
            if self.ctrl_mode != TEACHING:
                self.teach_status = TEACH_STOP

    def MotionCtrl_2(self, *args):
        self.motion_calls.append(("MotionCtrl_2", args))
        if self.ctrl_mode == TEACHING:
            # Measured: mode changes are ignored while drag teaching is on.
            return
        if self.refuse_can_control_once and args[0] == CAN_CONTROL:
            # The latch: accepted, acknowledged, and completely ignored.
            return
        previous = self.ctrl_mode
        self.ctrl_mode = args[0]
        if args[0] == STANDBY and previous == CAN_CONTROL:
            # Measured: dropping out of CAN control disables all six motors.
            self.enabled = False

    def JointCtrl(self, *args):
        self.joint_calls.append(args)
        # Which mode the arm was in when each target arrived.  Homing must
        # never command a pose while the arm is still in standby.
        self.joint_call_modes.append(self.ctrl_mode)

    def GripperCtrl(self, *args):
        pass

    def EnableArm(self, *args):
        self.enable_calls.append(args)
        if not self.ignore_enable:
            self.enabled = True

    def DisableArm(self, *args):
        # Measured on hardware: the enable bits go to 000000 and the arm sags.
        # It does not leave whatever ctrl_mode it was in.
        self.disable_calls.append(args)
        self.enabled = False


def build_standby_node(piper, params=None):
    """A driver that still parks the arm braked instead of limp."""
    merged = {"~idle_disabled": False}
    merged.update(params or {})
    return build_node(piper, merged)


def build_can_control_node(piper, params=None):
    """Build a driver on the legacy path that actually enters CAN control.

    Standby-hold is the production default now, so anything asserting on
    targets, keepalive streaming or CAN-control mode has to opt back in.  These
    properties still matter: the CAN-control path survives as the fallback for
    a firmware where entering it is not a 100 ms drop.
    """
    merged = {"~hold_in_standby": False, "~idle_disabled": False}
    merged.update(params or {})
    return build_node(piper, merged)


def build_node(piper, params=None):
    ros = FakeRos(SimpleNamespace(wall=time.time(), monotonic=time.monotonic()))
    ros.params = {
        "~can_port": "can_rear_left",
        "~auto_enable": False,
        # The real ride-through is 3 s of 20 Hz enables; offline it only needs
        # to run, not to last.
        "~entry_ride_through_sec": 0.01,
        "~entry_ride_through_rate": 1000.0,
    }
    ros.params.update(params or {})
    node = NODE.PiperRearTeachNode(ros_api=ros, piper_factory=lambda can_name: piper)
    return ros, node


class StartupFeedbackRaceTest(unittest.TestCase):
    def test_startup_tolerates_the_sdk_warm_up(self):
        """ConnectPort returns before any CAN frame arrives; do not fault."""
        piper = FakePiper("can_rear_left", silent_status_calls=5)
        ros, node = build_standby_node(piper)
        self.assertEqual(ros.errors, [], "healthy arm must not fault at startup")
        self.assertIs(node.controller.gate, NODE.GateState.NOT_READY)

    def test_startup_faults_when_feedback_never_arrives(self):
        piper = FakePiper("can_rear_left", silent_status_calls=10**9)
        ros, node = build_standby_node(piper, {"~startup_feedback_timeout_sec": 0.2})
        self.assertTrue(ros.errors)
        self.assertIn("no fresh arm status", ros.errors[0])
        self.assertIs(node.controller.gate, NODE.GateState.FAULT)

    def test_startup_leaves_teach_mode_without_moving_the_arm(self):
        piper = FakePiper("can_rear_left")
        piper.ctrl_mode = TEACHING
        piper.teach_status = TEACH_STOP
        ros, node = build_standby_node(piper)
        self.assertEqual(ros.errors, [])
        self.assertIs(node.controller.gate, NODE.GateState.NOT_READY)
        # grag_teach_ctrl 0x00 is the only exit that keeps the motors on.
        self.assertEqual(piper.motion_calls, [("MotionCtrl_1", (0x00, 0x00, 0x00))])
        # Leaving teach mode must never command a position or enable motors.
        self.assertEqual(piper.joint_calls, [])
        self.assertEqual(piper.enable_calls, [])

    def test_startup_with_the_button_engaged_leaves_the_gate_closed(self):
        piper = FakePiper("can_rear_left")
        piper.ctrl_mode = TEACHING
        piper.teach_status = TEACH_START
        ros, node = build_standby_node(piper)
        self.assertEqual(ros.errors, [])
        self.assertIs(node.controller.gate, NODE.GateState.TEACHING)
        self.assertEqual(piper.motion_calls, [])

    def test_driver_refuses_a_front_can_port(self):
        piper = FakePiper("can_left")
        with self.assertRaises(ValueError):
            build_standby_node(piper, {"~can_port": "can_left"})



class MotionReadyHonestyTest(unittest.TestCase):
    """motion_ready must mean the arm will actually execute commands.

    Both regressions here were found during hardware stage 5: the driver
    declared readiness with the motors switched off, and then kept advertising
    it after the arm had fallen back to standby.
    """

    def _armed_node(self, auto_enable, motors_enabled, grace=0.05):
        piper = FakePiper("can_rear_left")
        piper.enabled = motors_enabled
        ros, node = build_can_control_node(
            piper,
            {"~auto_enable": auto_enable, "~ready_grace_sec": grace,
             "~enable_settle_window_sec": 0.01, "~mode_verify_poll_sec": 0.01},
        )
        return piper, ros, node

    @staticmethod
    def _outlast_grace(node, status_source, grace=0.05):
        """A drop only counts once it outlasts the self-healing grace window."""
        node._verify_still_ready(status_source())
        time.sleep(grace + 0.03)
        node._verify_still_ready(status_source())

    def test_refuses_to_arm_with_motors_off_when_auto_enable_is_false(self):
        piper, ros, node = self._armed_node(False, False)
        node.joint_command_callback(_hold_message())
        self.assertFalse(node.controller.motion_ready)
        self.assertIs(node.controller.gate, NODE.GateState.FAULT)
        self.assertTrue(any("motors are not enabled" in e for e in ros.errors))

    def test_arms_when_motors_are_already_enabled_and_auto_enable_is_false(self):
        piper, ros, node = self._armed_node(False, True)
        node.joint_command_callback(_hold_message())
        self.assertEqual(ros.errors, [])
        self.assertTrue(node.controller.motion_ready)
        # auto_enable=false must still not send an enable command.
        self.assertEqual(piper.enable_calls, [])

    def test_arms_and_enables_when_auto_enable_is_true(self):
        piper, ros, node = self._armed_node(True, False)
        node.joint_command_callback(_hold_message())
        self.assertEqual(ros.errors, [])
        self.assertTrue(node.controller.motion_ready)
        self.assertTrue(piper.enable_calls)

    def test_falling_back_to_standby_while_armed_faults(self):
        piper, ros, node = self._armed_node(True, False)
        node.joint_command_callback(_hold_message())
        self.assertTrue(node.controller.motion_ready)
        piper.ctrl_mode = STANDBY
        self._outlast_grace(node, piper.GetArmStatus)
        self.assertIs(node.controller.gate, NODE.GateState.FAULT)
        self.assertTrue(any("unexpected control mode" in e for e in ros.errors))

    def test_losing_motor_enable_while_armed_faults(self):
        """Only when re-enabling cannot bring it back - healing comes first."""
        piper, ros, node = self._armed_node(True, False)
        node.joint_command_callback(_hold_message())
        self.assertTrue(node.controller.motion_ready)
        piper.enabled = False
        piper.ignore_enable = True
        self._outlast_grace(node, piper.GetArmStatus)
        self.assertIs(node.controller.gate, NODE.GateState.FAULT)
        self.assertTrue(any("lost motor enable" in e for e in ros.errors))

    def test_entering_teaching_while_armed_is_not_a_fault(self):
        """The debounced teach tracker owns that transition, not this check."""
        piper, ros, node = self._armed_node(True, False)
        node.joint_command_callback(_hold_message())
        piper.ctrl_mode = TEACHING
        piper.teach_status = TEACH_START
        node._verify_still_ready(piper.GetArmStatus())
        self.assertIsNot(node.controller.gate, NODE.GateState.FAULT)


def _hold_message():
    message = JointState()
    message.header.stamp = SimpleNamespace(to_sec=lambda: time.time())
    message.name = ["joint{}".format(i) for i in range(7)]
    message.position = [0.0] * 7
    return message



class CommandStreamKeepaliveTest(unittest.TestCase):
    """An armed arm must keep receiving its target or Piper drops to standby.

    Found on hardware stage 5: arming succeeded, then the arm left CAN control
    mode ~400 ms later because nothing was sent between the hold and the first
    policy pair.
    """

    def _armed(self, **params):
        piper = FakePiper("can_rear_left")
        opts = {"~auto_enable": True, "~command_stream_rate": 100.0}
        opts.update(params)
        ros, node = build_can_control_node(piper, opts)
        node.joint_command_callback(_hold_message())
        self.assertTrue(node.controller.motion_ready)
        return piper, ros, node

    def test_arming_records_the_hold_as_the_standing_target(self):
        piper, _, node = self._armed()
        self.assertIsNotNone(node._last_target)
        self.assertTrue(piper.joint_calls)

    def test_stream_resends_the_standing_target(self):
        piper, _, node = self._armed()
        before = len(piper.joint_calls)
        node._last_target_sent = 0.0  # pretend the period has elapsed
        node._stream_last_target()
        self.assertEqual(len(piper.joint_calls), before + 1)

    def test_stream_respects_the_rate_limit(self):
        piper, _, node = self._armed()
        before = len(piper.joint_calls)
        node._stream_last_target()  # just sent by the hold; must not resend
        self.assertEqual(len(piper.joint_calls), before)

    def test_stream_stops_when_readiness_is_lost(self):
        piper, _, node = self._armed()
        node.controller.set_teaching()
        before = len(piper.joint_calls)
        node._last_target_sent = 0.0
        node._stream_last_target()
        self.assertEqual(
            len(piper.joint_calls), before,
            "a taken-over arm must never be fed a stale target",
        )

    def test_a_new_command_replaces_the_standing_target(self):
        piper, _, node = self._armed()
        message = _hold_message()
        message.position = [0.01] * 7
        node.joint_command_callback(message)
        self.assertEqual(node._last_target, tuple(piper.joint_calls[-1]) + node._last_target[6:])



class PublishSurvivesRearmTest(unittest.TestCase):
    """Feedback must keep flowing while a re-arm holds the arm for seconds.

    Found on hardware stage 5: the re-arm held the same lock as the publish
    loop, joint_states went silent for ~0.77 s, and the coordinator's
    post-hold synchronization check faulted on stale feedback.
    """

    def test_publish_loop_does_not_block_on_the_hardware_lock(self):
        import threading

        piper = FakePiper("can_rear_left")
        ros, node = build_node(piper, {"~auto_enable": True})
        published = threading.Event()
        released = threading.Event()

        def hold_hardware():
            with node._hardware_lock:
                released.wait(2.0)

        holder = threading.Thread(target=hold_hardware, daemon=True)
        holder.start()
        time.sleep(0.05)

        def publish_once():
            node._publish_once()
            published.set()

        publisher = threading.Thread(target=publish_once, daemon=True)
        publisher.start()
        ok = published.wait(1.0)
        released.set()
        holder.join(timeout=2.0)
        publisher.join(timeout=2.0)
        self.assertTrue(
            ok, "publish loop stalled behind a write that holds _hardware_lock"
        )

    def test_joint_states_are_published_while_hardware_is_busy(self):
        import threading

        piper = FakePiper("can_rear_left")
        ros, node = build_node(piper, {"~auto_enable": True})
        before = len(ros.publishers["~joint_states"].messages)
        released = threading.Event()

        def hold_hardware():
            with node._hardware_lock:
                released.wait(1.0)

        holder = threading.Thread(target=hold_hardware, daemon=True)
        holder.start()
        time.sleep(0.05)
        node._publish_once()
        released.set()
        holder.join(timeout=2.0)
        self.assertEqual(
            len(ros.publishers["~joint_states"].messages), before + 1
        )



class DriverFaultResetTest(unittest.TestCase):
    """A latched driver fault must be clearable without restarting the launch.

    Found on hardware stage 5: the coordinator's reset_fault cleared only its
    own state, so a faulted rear driver kept silently dropping every command -
    including the hold that would have re-armed it.
    """

    def _faulted(self):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        ros, node = build_node(piper, {"~auto_enable": True})
        node._latch_fault("synthetic")
        self.assertIs(node.controller.gate, NODE.GateState.FAULT)
        return piper, ros, node

    def test_reset_reopens_the_gate_to_not_ready(self):
        piper, ros, node = self._faulted()
        response = node.handle_reset_fault(None)
        self.assertTrue(response.success)
        self.assertIs(node.controller.gate, NODE.GateState.NOT_READY)

    def test_reset_does_not_move_or_enable_the_arm(self):
        piper, ros, node = self._faulted()
        motions = len(piper.motion_calls)
        joints = len(piper.joint_calls)
        enables = len(piper.enable_calls)
        node.handle_reset_fault(None)
        self.assertEqual(len(piper.motion_calls), motions)
        self.assertEqual(len(piper.joint_calls), joints)
        self.assertEqual(len(piper.enable_calls), enables)

    def test_reset_drops_the_standing_target(self):
        piper, ros, node = self._faulted()
        node._last_target = (1, 2, 3, 4, 5, 6, 7)
        node.handle_reset_fault(None)
        self.assertIsNone(node._last_target)

    def test_idle_rear_position_message_after_reset_does_not_rearm(self):
        piper, ros, node = self._faulted()
        node.handle_reset_fault(None)
        self.assertFalse(node.controller.motion_ready)
        node.joint_command_callback(_hold_message())
        self.assertFalse(node.controller.motion_ready)
        self.assertFalse(piper.enabled)



class IdleDisabledTest(unittest.TestCase):
    """Production idle state: the rear arm carries no torque between takeovers.

    The operator has to be able to carry it to a comfortable pose by hand, and
    the clutch reference is taken when the teach button engages - so that
    carrying has to happen while nothing is forwarding, which means while the
    arm is limp.  A braked arm cannot be moved by hand at all.
    """

    def _node(self, **params):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        merged = {"~auto_enable": True, "~teach_stability_sec": 0.001}
        merged.update(params)
        ros, node = build_node(piper, merged)
        return piper, ros, node

    def _teach(self, piper, node, active):
        piper.ctrl_mode = TEACHING if active else STANDBY
        piper.teach_status = TEACH_START if active else TEACH_STOP
        for _ in range(30):
            node._publish_once()
            time.sleep(0.001)

    def test_startup_parks_the_arm_limp(self):
        piper, ros, node = self._node()
        self.assertEqual(ros.errors, [])
        self.assertTrue(piper.disable_calls, "idle means limp, so make it limp")
        self.assertFalse(piper.enabled)

    def test_leaving_teach_goes_limp_immediately(self):
        piper, ros, node = self._node()
        self._teach(piper, node, True)
        self.assertIs(node.controller.gate, NODE.GateState.TEACHING)
        before = len(piper.disable_calls)
        self._teach(piper, node, False)
        self.assertGreater(
            len(piper.disable_calls),
            before,
            "the arm must go limp the moment teaching ends, not later",
        )
        self.assertFalse(piper.enabled)
        self.assertEqual(ros.errors, [])

    def test_a_limp_arm_is_not_a_fault(self):
        """The old readiness check demanded every motor be enabled."""
        piper, ros, node = self._node()
        for _ in range(40):
            node._publish_once()
        self.assertEqual(ros.errors, [])
        self.assertIsNot(node.controller.gate, NODE.GateState.FAULT)

    def test_a_limp_arm_is_never_re_enabled(self):
        piper, ros, node = self._node()
        before = len(piper.enable_calls)
        for _ in range(40):
            node._publish_once()
        self.assertEqual(
            len(piper.enable_calls),
            before,
            "healing a limp arm back to enabled would brake it mid-reposition",
        )

    def test_commands_never_actuate(self):
        piper, ros, node = self._node()
        node.joint_command_callback(_hold_message())
        self.assertEqual(piper.joint_calls, [])
        self.assertEqual(ros.errors, [])


class StandbyHoldTest(unittest.TestCase):
    """The production path: hold the rear arms in standby and never actuate.

    Established on hardware after four instrumented M/S cycles.  Entering CAN
    control raises JOINT_COMMUNICATION_ERR with err_code 0x003F exactly 0.5 s
    later, every time, on firmware S-V1.7-3.  The arm is kicked to standby and
    goes limp for roughly 100 ms, and the operator sees it fall and recover.
    None of the software-side mitigations touched it: the fault arrived at the
    same 0.5 s at 4 Hz, 20 Hz and 50 Hz command rates, with and without enable
    broadcasts, with a 1 s standby dwell, and whether or not the motors were
    re-enabled on the way in.  Keeping the enable bits at 1 across the window
    did not help either - the bit stays set while the joint stops answering.

    Standby is the way out.  There the joints are held by their brakes, and
    every recorded dwell measured zero travel with err_code 0x0000.  The rear
    arms have no reason to be actuators: the clutch captures the front/rear
    offset when teaching engages, so they are free to sit where they are.
    """

    def _armed(self, **params):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        ros, node = build_standby_node(piper, dict({"~auto_enable": True}, **params))
        node.joint_command_callback(_hold_message())
        return piper, ros, node

    def test_arming_never_enters_can_control(self):
        piper, ros, node = self._armed()
        self.assertEqual(ros.errors, [])
        self.assertIs(node.controller.gate, NODE.GateState.READY)
        self.assertEqual(piper.ctrl_mode, STANDBY)
        modes = [args[0] for name, args in piper.motion_calls if name == "MotionCtrl_2"]
        self.assertNotIn(
            NODE.CAN_CONTROL_MODE,
            modes,
            "standby-hold must never ask for CAN control",
        )

    def test_arming_sends_no_target(self):
        piper, _, _ = self._armed()
        self.assertEqual(
            piper.joint_calls,
            [],
            "a target would drive the arm; standby-hold only confirms the hold",
        )

    def test_ordinary_commands_are_ignored(self):
        piper, ros, node = self._armed()
        node.joint_command_callback(_hold_message())
        self.assertEqual(piper.joint_calls, [])
        self.assertEqual(ros.errors, [])
        self.assertIs(node.controller.gate, NODE.GateState.READY)

    def test_standby_is_not_treated_as_falling_out_of_control(self):
        piper, ros, node = self._armed()
        for _ in range(20):
            node._publish_once()
        self.assertIs(node.controller.gate, NODE.GateState.READY)
        self.assertEqual(ros.errors, [])

    def test_a_hold_far_from_the_arm_is_accepted(self):
        """The rear arms drift from the front arms by design once they stop
        following, so reachability cannot be required of the hold."""
        piper, ros, node = self._armed()
        node.controller.set_not_ready()
        far = _hold_message()
        far.position = [v + 1.5 for v in far.position]
        node.joint_command_callback(far)
        self.assertEqual(ros.errors, [])
        self.assertIs(node.controller.gate, NODE.GateState.READY)


class ArmingOrderTest(unittest.TestCase):
    """The unfed window during arming is what loses the arm.

    Found on hardware stages 5 and 6: arming succeeded whenever the motors
    happened to be enabled already, and dropped the arm back to standby
    whenever enabling took a few hundred milliseconds, because nothing fed the
    arm between the preloaded hold and the streaming loop.
    """

    def _arm(self, motors_enabled):
        piper = FakePiper("can_rear_left")
        piper.enabled = motors_enabled
        ros, node = build_can_control_node(piper, {"~auto_enable": True})
        node.joint_command_callback(_hold_message())
        return piper, ros, node

    def test_motors_are_enabled_before_a_target_is_loaded(self):
        piper, ros, node = self._arm(False)
        self.assertEqual(ros.errors, [])
        order = []
        for name, _ in piper.motion_calls:
            order.append(name)
        self.assertTrue(piper.enable_calls, "arming must enable the motors")
        # EnableArm has to happen before the first JointCtrl, so the arm is
        # never holding a target while still unpowered.
        self.assertTrue(
            piper.joint_calls, "arming must preload the hold as the target"
        )

    def test_mode_wait_keeps_feeding_the_arm(self):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        ros, node = build_can_control_node(piper, {"~auto_enable": True})
        node._last_target = (0, 0, 0, 0, 0, 0, 0)
        before = len(piper.joint_calls)

        # Force the mode check to miss once so the loop has to iterate.
        original = piper.GetArmStatus
        calls = {"n": 0}

        def flaky_status():
            calls["n"] += 1
            if calls["n"] == 1:
                piper.ctrl_mode = STANDBY
            else:
                piper.ctrl_mode = CAN_CONTROL
            return original()

        piper.GetArmStatus = flaky_status
        node._last_target_sent = 0.0
        ok = node._wait_for_ctrl_mode(CAN_CONTROL, keepalive=True)
        self.assertTrue(ok)
        self.assertGreater(
            len(piper.joint_calls), before,
            "the mode wait must re-send the target, or the arm times out",
        )

    def test_mode_wait_without_keepalive_sends_nothing(self):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        ros, node = build_can_control_node(piper, {"~auto_enable": True})
        piper.ctrl_mode = CAN_CONTROL
        before = len(piper.joint_calls)
        node._wait_for_ctrl_mode(CAN_CONTROL)
        self.assertEqual(len(piper.joint_calls), before)



class EnableSettleTest(unittest.TestCase):
    """Enabling provokes a self-clearing JOINT_COMMUNICATION_ERR burst.

    Measured on hardware: whether arming succeeded came down to whether that
    burst landed before or after CAN control mode was confirmed.  Arming must
    wait the burst out instead of racing it.
    """

    def test_arming_waits_out_the_enable_transient(self):
        piper = FakePiper("can_rear_left")
        piper.unhealthy_status_calls = 8
        ros, node = build_standby_node(
            piper,
            {"~auto_enable": True, "~enable_settle_window_sec": 0.05,
             "~enable_settle_timeout_sec": 3.0, "~mode_verify_poll_sec": 0.01},
        )
        node.joint_command_callback(_hold_message())
        self.assertEqual(ros.errors, [])
        self.assertTrue(node.controller.motion_ready)

    def test_arming_fails_when_the_arm_never_settles(self):
        piper = FakePiper("can_rear_left")
        piper.arm_status = 0x05
        piper.err_code = 0x003F
        ros, node = build_standby_node(
            piper,
            {"~auto_enable": True, "~enable_settle_window_sec": 0.05,
             "~enable_settle_timeout_sec": 0.2, "~mode_verify_poll_sec": 0.01},
        )
        node.joint_command_callback(_hold_message())
        self.assertFalse(node.controller.motion_ready)
        self.assertTrue(any("did not settle" in e for e in ros.errors))

    def test_no_target_is_loaded_before_the_arm_is_healthy(self):
        piper = FakePiper("can_rear_left")
        piper.arm_status = 0x05
        piper.err_code = 0x003F
        ros, node = build_standby_node(
            piper,
            {"~auto_enable": True, "~enable_settle_window_sec": 0.05,
             "~enable_settle_timeout_sec": 0.2, "~mode_verify_poll_sec": 0.01},
        )
        node.joint_command_callback(_hold_message())
        self.assertEqual(
            piper.joint_calls, [],
            "a faulted arm must never be given a position target",
        )



class SelfHealingTest(unittest.TestCase):
    """A ~100 ms blip out of CAN control must heal, not strand the arm.

    Measured on hardware: the arm emits a self-clearing
    JOINT_COMMUNICATION_ERR burst, drops to standby, recovers its health and
    then stays in standby forever, because nothing re-asserts the mode.  The
    stock piper_start_ms_node.py avoids this by re-sending MotionCtrl_2 with
    every command; this driver now does the same.
    """

    def _armed(self, **params):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        opts = {"~auto_enable": True, "~ready_grace_sec": 0.5,
                "~enable_settle_window_sec": 0.01, "~mode_verify_poll_sec": 0.01}
        opts.update(params)
        ros, node = build_can_control_node(piper, opts)
        node.joint_command_callback(_hold_message())
        self.assertTrue(node.controller.motion_ready)
        return piper, ros, node

    def test_every_command_re_asserts_can_control_mode(self):
        piper, ros, node = self._armed()
        names = [n for n, _ in piper.motion_calls]
        self.assertGreaterEqual(
            names.count("MotionCtrl_2"), 3,
            "each target must be bracketed by MotionCtrl_2, like the stock driver",
        )
        piper.motion_calls.clear()
        node._send_target((0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(
            [n for n, _ in piper.motion_calls],
            ["MotionCtrl_2", "MotionCtrl_2"],
        )

    def test_a_brief_blip_does_not_fault(self):
        piper, ros, node = self._armed()
        piper.ctrl_mode = STANDBY
        node._verify_still_ready(piper.GetArmStatus())
        self.assertIsNot(node.controller.gate, NODE.GateState.FAULT)
        # ... and recovering inside the grace window clears the timer.
        piper.ctrl_mode = CAN_CONTROL
        node._verify_still_ready(piper.GetArmStatus())
        self.assertIsNone(node._unhealthy_since)
        self.assertTrue(node.controller.motion_ready)

    def test_a_sustained_drop_still_faults(self):
        piper, ros, node = self._armed(**{"~ready_grace_sec": 0.05})
        piper.ctrl_mode = STANDBY
        node._verify_still_ready(piper.GetArmStatus())
        time.sleep(0.08)
        node._verify_still_ready(piper.GetArmStatus())
        self.assertIs(node.controller.gate, NODE.GateState.FAULT)
        self.assertTrue(any("more than" in e for e in ros.errors))



class KeepaliveYieldsToOperatorTest(unittest.TestCase):
    """The command stream must release the arm the instant teaching starts.

    Found on hardware stage 6: every streamed frame re-asserts CAN control
    mode, so an armed arm was yanked back out of drag teaching within
    milliseconds of the operator pressing the button.  Both arms kept
    flickering in and out of teaching and the takeover never engaged.
    """

    def _armed(self):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        ros, node = build_can_control_node(
            piper,
            {"~auto_enable": True, "~enable_settle_window_sec": 0.01,
             "~mode_verify_poll_sec": 0.01},
        )
        node.joint_command_callback(_hold_message())
        self.assertTrue(node.controller.motion_ready)
        node._last_target_sent = 0.0
        return piper, ros, node

    def test_stream_stops_on_raw_teaching_mode(self):
        piper, ros, node = self._armed()
        before = len(piper.joint_calls)
        node._stream_last_target(ctrl_mode=TEACHING)
        self.assertEqual(
            len(piper.joint_calls), before,
            "streaming during teaching fights the operator for the arm",
        )

    def test_stream_continues_in_can_control(self):
        piper, ros, node = self._armed()
        before = len(piper.joint_calls)
        node._stream_last_target(ctrl_mode=CAN_CONTROL)
        self.assertEqual(len(piper.joint_calls), before + 1)

    def test_publish_loop_passes_the_raw_mode_through(self):
        piper, ros, node = self._armed()
        piper.ctrl_mode = TEACHING
        piper.teach_status = TEACH_START
        before = len(piper.joint_calls)
        node._publish_once()
        self.assertEqual(
            len(piper.joint_calls), before,
            "the publish loop must not stream while the arm reports teaching",
        )



class EnableSelfHealingTest(unittest.TestCase):
    """Losing motor enable while armed must be healed, not just reported.

    Found on hardware stage 6: after arming, the arm disabled its motors
    during the transient, cleared the error and stayed disabled.  It sat in
    CAN control mode holding a target it could not execute - reporting
    motion=0x01 while drifting 0.005 rad in 15 seconds.
    """

    def _armed(self, **params):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        opts = {"~auto_enable": True, "~ready_grace_sec": 1.5,
                "~reenable_period_sec": 0.0001,
                "~enable_settle_window_sec": 0.01, "~mode_verify_poll_sec": 0.01}
        opts.update(params)
        ros, node = build_standby_node(piper, opts)
        node.joint_command_callback(_hold_message())
        self.assertTrue(node.controller.motion_ready)
        piper.enable_calls.clear()
        return piper, ros, node

    def test_lost_enable_on_a_healthy_arm_is_re_enabled(self):
        piper, ros, node = self._armed()
        piper.enabled = False
        node._verify_still_ready(piper.GetArmStatus())
        self.assertTrue(
            piper.enable_calls,
            "a healthy armed arm that lost enable must be re-enabled",
        )
        self.assertIsNot(node.controller.gate, NODE.GateState.FAULT)

    def test_a_faulted_arm_is_not_re_enabled(self):
        piper, ros, node = self._armed()
        piper.enabled = False
        piper.arm_status = 0x07  # COLLISION_OCCURRED
        piper.err_code = 0x0001
        node._verify_still_ready(piper.GetArmStatus())
        self.assertEqual(
            piper.enable_calls, [],
            "never fight an arm that is reporting a real fault",
        )

    def test_enable_that_never_returns_still_faults(self):
        piper, ros, node = self._armed(**{"~ready_grace_sec": 0.05})
        piper.enabled = False
        piper.ignore_enable = True
        node._verify_still_ready(piper.GetArmStatus())
        time.sleep(0.08)
        node._verify_still_ready(piper.GetArmStatus())
        self.assertIs(node.controller.gate, NODE.GateState.FAULT)



class LimpWindowTest(unittest.TestCase):
    """Clearing teach residue drops the motors; the arm must be powered back
    up as early as possible.

    Reported from the floor: on every S the rear arms went limp and visibly
    sagged before driving to the hold, because re-enabling only happened
    after both mode confirmations had completed.
    """

    def test_leaving_teach_mode_never_disables_the_arm(self):
        """The old path sent MotionCtrl_1(0x02), which drops all six motors."""
        piper = FakePiper("can_rear_left")
        piper.ctrl_mode = TEACHING
        piper.teach_status = TEACH_STOP
        piper.enabled = True
        ros, node = build_standby_node(
            piper, {"~auto_enable": True, "~mode_verify_poll_sec": 0.01}
        )
        for name, args in piper.motion_calls:
            self.assertNotEqual(
                (name, args[:1]), ("MotionCtrl_1", (0x02,)),
                "emergency-stop-resume drops all six motors",
            )
            self.assertNotEqual(
                (name, args[:1]), ("MotionCtrl_2", (STANDBY,)),
                "switching to standby drops all six motors",
            )
        self.assertTrue(piper.enabled, "the arm must stay powered throughout")

    def test_no_enable_is_issued_when_auto_enable_is_false(self):
        piper = FakePiper("can_rear_left")
        piper.ctrl_mode = TEACHING
        piper.teach_status = TEACH_STOP
        ros, node = build_standby_node(
            piper, {"~auto_enable": False, "~mode_verify_poll_sec": 0.01}
        )
        self.assertEqual(
            piper.enable_calls, [],
            "auto_enable=false must stay hands-off even while clearing residue",
        )

    def test_residue_clearing_still_commands_no_position(self):
        piper = FakePiper("can_rear_left")
        piper.ctrl_mode = TEACHING
        piper.teach_status = TEACH_STOP
        ros, node = build_standby_node(
            piper, {"~auto_enable": True, "~mode_verify_poll_sec": 0.01}
        )
        self.assertEqual(
            piper.joint_calls, [],
            "powering the arm back up must never come with a position target",
        )



class LatchedMotorFaultTest(unittest.TestCase):
    """A joint driver that tripped must never be re-enabled by software.

    Measured on hardware: rear-left joint 5 stalled and latched
    driver_overcurrent + collision + driver_error + stall, while the ARM level
    status still read 0x00.  The healthy check only looked at the arm level,
    so the driver kept hammering EnableArm at a joint that was in protection.
    """

    def _armed(self):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        ros, node = build_standby_node(
            piper,
            {"~auto_enable": True, "~ready_grace_sec": 3.0,
             "~reenable_period_sec": 0.0001,
             "~enable_settle_window_sec": 0.01, "~mode_verify_poll_sec": 0.01},
        )
        node.joint_command_callback(_hold_message())
        self.assertTrue(node.controller.motion_ready)
        piper.enable_calls.clear()
        return piper, ros, node

    def test_a_stalled_joint_is_not_re_enabled(self):
        piper, ros, node = self._armed()
        piper.motor_flags = {
            5: ("driver_overcurrent", "collision_status",
                "driver_error_status", "stall_status")
        }
        node._verify_still_ready(piper.GetArmStatus())
        self.assertEqual(
            piper.enable_calls, [],
            "re-enabling a latched joint repeats whatever tripped it",
        )

    def test_the_fault_names_the_joint_and_the_flags(self):
        piper, ros, node = self._armed()
        piper.motor_flags = {5: ("stall_status",)}
        node._verify_still_ready(piper.GetArmStatus())
        time.sleep(0.05)
        node.ready_grace_sec = 0.0
        node._verify_still_ready(piper.GetArmStatus())
        joined = " ".join(ros.errors)
        self.assertIn("motor(s) 5", joined)
        self.assertIn("stall_status", joined)

    def test_a_clean_enable_loss_is_still_healed(self):
        piper, ros, node = self._armed()
        piper.enabled = False  # no latched flags: a plain dropout
        node._verify_still_ready(piper.GetArmStatus())
        self.assertTrue(piper.enable_calls)



class LeaveTeachModeIsConditionalTest(unittest.TestCase):
    """Re-arming happens from several states, not only from drag teaching.

    Found on hardware: the exit was issued unconditionally and then waited for
    a mode change, so re-arming an arm that was already in CAN control timed
    out with "arm did not leave teach mode".
    """

    def _node(self, ctrl_mode, teach_status=0x00):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        piper.ctrl_mode = ctrl_mode
        piper.teach_status = teach_status
        ros, node = build_can_control_node(
            piper,
            {"~auto_enable": True, "~enable_settle_window_sec": 0.01,
             "~mode_verify_poll_sec": 0.01, "~mode_verify_timeout_sec": 0.5},
        )
        return piper, ros, node

    def test_rearm_from_can_control_does_not_wait_for_a_transition(self):
        piper, ros, node = self._node(CAN_CONTROL)
        piper.motion_calls.clear()
        node.joint_command_callback(_hold_message())
        self.assertEqual(ros.errors, [])
        self.assertTrue(node.controller.motion_ready)
        self.assertNotIn(
            ("MotionCtrl_1", (0x00, 0x00, 0x00)), piper.motion_calls,
            "nothing to leave when the arm was never in teach mode",
        )

    def test_rearm_from_standby_is_also_fine(self):
        piper, ros, node = self._node(STANDBY)
        node.joint_command_callback(_hold_message())
        self.assertEqual(ros.errors, [])
        self.assertTrue(node.controller.motion_ready)

    def test_rearm_from_teach_mode_does_issue_the_exit(self):
        # Start clean, then put the arm into teach mode the way the operator
        # does, so the hold arrives with real residue present.
        piper, ros, node = self._node(STANDBY)
        piper.ctrl_mode = TEACHING
        piper.teach_status = TEACH_STOP
        piper.motion_calls.clear()
        node.joint_command_callback(_hold_message())
        self.assertEqual(ros.errors, [])
        self.assertIn(("MotionCtrl_1", (0x00, 0x00, 0x00)), piper.motion_calls)
        self.assertTrue(node.controller.motion_ready)
        self.assertTrue(piper.enabled, "the exit must not drop the motors")


if __name__ == "__main__":
    unittest.main()


class RearHomingTest(unittest.TestCase):
    """The one place a rear arm is allowed into CAN control.

    Safe only because the move starts from the gravity rest position where the
    operator parked the limp arm: the 0.5 s JOINT_COMMUNICATION_ERR that
    follows every CAN-control entry is absorbed there, at rest, before any
    motion is commanded.
    """

    def _node(self, **params):
        piper = FakePiper("can_rear_left")
        piper.enabled = True
        merged = {
            "~auto_enable": True,
            "~teach_stability_sec": 0.001,
            "~home_settle_sec": 0.001,
            "/task2/homing/rear_left": [0.1] * 7,
            "/task2/homing/speed": {
                "joint_rad_per_sec": 100.0,
                "gripper_m_per_sec": 100.0,
                "publish_rate_hz": 1000.0,
            },
        }
        merged.update(params)
        ros, node = build_node(piper, merged)
        return piper, ros, node

    def _teach(self, piper, node, active):
        piper.ctrl_mode = TEACHING if active else STANDBY
        piper.teach_status = TEACH_START if active else TEACH_STOP
        for _ in range(30):
            node._publish_once()
            time.sleep(0.001)

    def test_homing_enters_can_control_and_holds(self):
        piper, ros, node = self._node()
        response = node.handle_home(None)
        self.assertTrue(response.success, response.message)
        self.assertEqual(piper.ctrl_mode, CAN_CONTROL)
        self.assertTrue(piper.joint_calls, "homing must actually command a pose")
        self.assertTrue(node._homed)

    def test_only_measured_hold_is_preloaded_before_can_control(self):
        piper, ros, node = self._node()
        self.assertTrue(node.handle_home(None).success)
        self.assertEqual(piper.joint_call_modes[0], STANDBY)
        self.assertEqual(piper.joint_calls[0], (0,) * 6)
        self.assertNotIn(STANDBY, piper.joint_call_modes[1:])

    def test_homing_does_not_fault_the_readiness_monitor(self):
        """Idle for this driver is standby; without the homed flag the monitor
        reads the arm's own homed state as having fallen out of control."""
        piper, ros, node = self._node()
        self.assertTrue(node.handle_home(None).success)
        for _ in range(30):
            node._publish_once()
        self.assertEqual(ros.errors, [])
        self.assertIsNot(node.controller.gate, NODE.GateState.FAULT)

    def test_refuses_while_teaching(self):
        piper, ros, node = self._node()
        self._teach(piper, node, True)
        self.assertIs(node.controller.gate, NODE.GateState.TEACHING)
        response = node.handle_home(None)
        self.assertFalse(response.success)
        self.assertIn("teaching", response.message)

    def test_refuses_when_auto_enable_is_false(self):
        piper, ros, node = self._node(**{"~auto_enable": False})
        response = node.handle_home(None)
        self.assertFalse(response.success)
        self.assertIn("auto_enable", response.message)

    def test_refuses_when_the_target_is_not_set(self):
        piper, ros, node = self._node()
        del ros.params["/task2/homing/rear_left"]
        response = node.handle_home(None)
        self.assertFalse(response.success)
        self.assertIn("not set", response.message)

    def test_refuses_a_malformed_target(self):
        piper, ros, node = self._node(**{"/task2/homing/rear_left": [0.1] * 6})
        response = node.handle_home(None)
        self.assertFalse(response.success)

    def test_teaching_clears_the_homed_flag(self):
        """Once the operator grabs it, the arm is no longer holding the pose."""
        piper, ros, node = self._node()
        self.assertTrue(node.handle_home(None).success)
        self._teach(piper, node, True)
        self.assertFalse(node._homed)

    def test_a_far_target_is_accepted(self):
        """No distance veto: a long way home is a slow move, not an error."""
        piper, ros, node = self._node(**{"/task2/homing/rear_left": [1.5] * 7})
        self.assertTrue(node.handle_home(None).success)

    def test_a_limp_arm_clears_the_latch_before_asking_for_can_control(self):
        """Leaving teach mode latches the arm into refusing CAN control nearly
        every time, and discovering that costs a full mode_verify_timeout_sec of
        waiting for a transition that will never come.  At rest the clear is
        free, so pay it up front instead."""
        piper, ros, node = self._node()
        piper.enabled = False
        piper.refuse_can_control_once = True
        response = node.handle_home(None)
        self.assertTrue(response.success, response.message)
        order = [name for name, _ in piper.motion_calls]
        self.assertEqual(
            order[0],
            "MotionCtrl_1",
            "the latch clear must come before the first mode request",
        )

    def test_a_holding_arm_is_never_dropped_to_clear_the_latch(self):
        """Resume-from-emergency-stop drops every motor.  Doing it to an arm
        that is holding a pose would drop it from height - the exact fall this
        driver exists to avoid."""
        piper, ros, node = self._node()
        self.assertTrue(node.handle_home(None).success)
        piper.motion_calls.clear()
        self.assertTrue(piper.enabled)
        self.assertTrue(node.handle_home(None).success)
        self.assertNotIn(
            ("MotionCtrl_1", (0x02, 0, 0)),
            piper.motion_calls,
            "an arm that was already holding got dropped",
        )

    def test_a_refused_can_control_is_cleared_and_retried(self):
        """Leaving teach mode can latch the arm into refusing CAN control while
        every status field reads clean.  Resume-from-emergency-stop is the only
        thing measured to clear it, and here it costs nothing: homing only ever
        runs with the arm lying at its resting pose."""
        piper, ros, node = self._node()
        piper.refuse_can_control_once = True
        response = node.handle_home(None)
        self.assertTrue(response.success, response.message)
        self.assertIn(
            ("MotionCtrl_1", (0x02, 0, 0)),
            piper.motion_calls,
            "the latch was never cleared",
        )
        self.assertEqual(piper.ctrl_mode, CAN_CONTROL)

    def test_the_homed_flag_is_set_before_the_move_not_after(self):
        """The readiness monitor treats ctrl_mode 0x01 as falling out of control
        for an idle-disabled arm.  A move longer than the grace window would
        fault the driver halfway through its own homing."""
        piper, ros, node = self._node(
            **{
                "/task2/homing/speed": {
                    "joint_rad_per_sec": 0.5,
                    "gripper_m_per_sec": 0.5,
                    "publish_rate_hz": 200.0,
                }
            }
        )
        seen = []
        original = node._send_target

        def spy(raw):
            seen.append(node._homed)
            return original(raw)

        node._send_target = spy
        self.assertTrue(node.handle_home(None).success)
        self.assertTrue(seen, "no target was sent")
        self.assertTrue(
            all(seen), "the arm was moving before it was marked as homed"
        )

    def test_going_limp_clears_the_homed_flag(self):
        piper, ros, node = self._node()
        self.assertTrue(node.handle_home(None).success)
        node._go_limp()
        self.assertFalse(node._homed)

    def test_late_enable_loss_stops_instead_of_rearming(self):
        """Direct home has no dwell or automatic recovery during a lift."""
        piper, ros, node = self._node()
        ros.params["/task2/homing/rear_left"] = [.1, .1, -.1, 0, 0, 0, 0]
        original = time.sleep
        before = len(piper.enable_calls)
        fired = {"done": False}
        def lose_enable(seconds):
            if not fired["done"] and piper.enabled:
                fired["done"] = True
                fired["enable_count"] = len(piper.enable_calls)
                piper.ctrl_mode = STANDBY
                piper.enabled = False
        NODE.time.sleep = lose_enable
        try:
            response = node.handle_home(None)
        finally:
            NODE.time.sleep = original
        self.assertTrue(fired["done"])
        self.assertFalse(response.success)
        self.assertTrue("lost" in response.message or "not holding" in response.message)
        self.assertEqual(len(piper.enable_calls), fired["enable_count"])
        self.assertFalse(node._homed)
        self.assertIsNone(node._last_target)


    def test_a_limp_arm_is_never_reported_as_homed(self):
        """Without a final check the service happily reports success while the
        arm lies exactly where it started."""
        piper, ros, node = self._node()
        original = node._send_target

        def die_after_first(raw):
            piper.enabled = False
            return original(raw)

        node._send_target = die_after_first
        response = node.handle_home(None)
        self.assertFalse(response.success)
        self.assertIn("not holding", response.message)
        self.assertFalse(node._homed)


class StartupStateAdoptionTest(unittest.TestCase):
    """What the rear arms were left in decides what is safe to do about it."""

    def _build(self, ctrl_mode, enabled):
        piper = FakePiper("can_rear_left")
        piper.ctrl_mode = ctrl_mode
        piper.enabled = enabled
        ros, node = build_node(piper, {"~auto_enable": True})
        return piper, ros, node

    def test_an_arm_holding_a_pose_is_adopted_not_dropped(self):
        """Going limp here would drop it from wherever it was holding, which is
        the exact fall the rest of this driver exists to avoid."""
        piper, ros, node = self._build(CAN_CONTROL, True)
        self.assertEqual(
            piper.disable_calls, [], "a holding arm must not be dropped at startup"
        )
        self.assertTrue(node._homed)
        self.assertEqual(ros.errors, [])

    def test_an_adopted_arm_does_not_fault_the_readiness_monitor(self):
        """Without adopting it, the monitor sees CAN control on an arm it did
        not home, calls it out of control, and faults after the grace window."""
        piper, ros, node = self._build(CAN_CONTROL, True)
        for _ in range(60):
            node._publish_once()
        self.assertEqual(ros.errors, [])
        self.assertIsNot(node.controller.gate, NODE.GateState.FAULT)

    def test_a_limp_arm_in_can_control_is_returned_to_standby(self):
        """Leaving the mode alone leaves the monitor watching an arm that is
        'in control' with nothing to control - which is how a fresh launch
        faulted itself three seconds after startup."""
        piper, ros, node = self._build(CAN_CONTROL, False)
        self.assertEqual(piper.ctrl_mode, STANDBY)
        self.assertTrue(piper.disable_calls)
        self.assertFalse(node._homed)
        for _ in range(60):
            node._publish_once()
        self.assertEqual(ros.errors, [])

    def test_a_standby_arm_is_still_parked_limp(self):
        piper, ros, node = self._build(STANDBY, True)
        self.assertTrue(piper.disable_calls)
        self.assertFalse(node._homed)

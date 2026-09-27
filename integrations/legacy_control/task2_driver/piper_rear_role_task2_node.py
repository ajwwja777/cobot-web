#!/usr/bin/env python3
"""ROS1 driver for one independently connected Task2 rear Piper arm."""

import threading
import time
import math
from typing import Any, Optional

import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, SetBoolResponse

from piper_msgs.msg import PiperStatusMsg
from piper_sdk import C_PiperInterface

from task2_rear_role_core import (
    CommandValidationError,
    RearRoleController,
    Role,
    RoleError,
    StaleCommandError,
    feedback_to_joint_positions,
    normalize_role,
    parse_bool_param,
    validate_can_port,
)


JOINT_NAMES = [
    "joint0",
    "joint1",
    "joint2",
    "joint3",
    "joint4",
    "joint5",
    "joint6",
]


class PiperRearRoleNode:
    """Own one rear CAN interface and expose a runtime master/slave role service."""

    def __init__(self, ros_api: Any = rospy, piper_factory: Any = C_PiperInterface) -> None:
        self.ros = ros_api
        self.can_port = validate_can_port(self.ros.get_param("~can_port", ""))
        self.initial_role = normalize_role(
            self.ros.get_param("~initial_role", Role.SLAVE.value)
        )
        self.auto_enable = parse_bool_param(
            self.ros.get_param("~auto_enable", False), "auto_enable"
        )
        self.gripper_exist = parse_bool_param(
            self.ros.get_param("~gripper_exist", True), "gripper_exist"
        )
        self.publish_rate = float(self.ros.get_param("~publish_rate", 200.0))
        self.command_timeout_sec = float(
            self.ros.get_param("~command_timeout_sec", 0.25)
        )
        self.role_verify_timeout_sec = float(
            self.ros.get_param("~role_verify_timeout_sec", 1.0)
        )
        self.role_verify_poll_sec = float(
            self.ros.get_param("~role_verify_poll_sec", 0.02)
        )
        self.enable_verify_timeout_sec = float(
            self.ros.get_param("~enable_verify_timeout_sec", 5.0)
        )
        self.enable_verify_poll_sec = float(
            self.ros.get_param("~enable_verify_poll_sec", 0.1)
        )
        self.feedback_timeout_sec = float(
            self.ros.get_param("~feedback_timeout_sec", 0.25)
        )
        for field, value in (
            ("publish_rate", self.publish_rate),
            ("command_timeout_sec", self.command_timeout_sec),
            ("role_verify_timeout_sec", self.role_verify_timeout_sec),
            ("role_verify_poll_sec", self.role_verify_poll_sec),
            ("enable_verify_timeout_sec", self.enable_verify_timeout_sec),
            ("enable_verify_poll_sec", self.enable_verify_poll_sec),
            ("feedback_timeout_sec", self.feedback_timeout_sec),
        ):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError("{} must be finite and greater than zero".format(field))

        self.piper = piper_factory(can_name=self.can_port)
        self.piper.ConnectPort()
        self._hardware_lock = threading.RLock()
        self.controller = RearRoleController(
            self.piper, command_timeout_sec=self.command_timeout_sec
        )
        self._motion_ready = False
        self._slave_ready_monotonic = None

        self.joint_state_pub = self.ros.Publisher(
            "~joint_states", JointState, queue_size=1
        )
        self.master_joint_pub = self.ros.Publisher(
            "~master_joint", JointState, queue_size=1
        )
        self.role_pub = self.ros.Publisher(
            "~role", String, queue_size=1, latch=True
        )
        self.arm_status_pub = self.ros.Publisher(
            "~arm_status", PiperStatusMsg, queue_size=1
        )
        self.motion_ready_pub = self.ros.Publisher(
            "~motion_ready", Bool, queue_size=1, latch=True
        )
        self.command_sub = self.ros.Subscriber(
            "~joint_cmd", JointState, self.joint_command_callback, queue_size=1
        )
        self.role_service = self.ros.Service(
            "~set_master", SetBool, self.handle_set_master
        )

        self._set_motion_ready(False)
        self._apply_role(self.initial_role)
        self.ros.loginfo(
            "Task2 rear arm ready: can_port=%s role=%s auto_enable=%s",
            self.can_port,
            self.role.value,
            self.auto_enable,
        )

    @property
    def role(self) -> Optional[Role]:
        return self.controller.role

    def _publish_role(self) -> None:
        role_name = self.role.value if self.role is not None else "fault"
        self.role_pub.publish(String(data=role_name))

    def _set_motion_ready(self, ready: bool) -> None:
        self._motion_ready = bool(ready)
        self.motion_ready_pub.publish(Bool(data=self._motion_ready))

    def _apply_role(self, role: Role) -> Role:
        with self._hardware_lock:
            self._set_motion_ready(False)
            self._slave_ready_monotonic = None
            transition_started_wall = time.time()
            try:
                selected = self.controller.switch_role(
                    role,
                    verifier=lambda target: self._verify_role(
                        target, not_before_wall=transition_started_wall
                    ),
                )
            except Exception:
                self._publish_role()
                raise
            if selected is Role.SLAVE:
                self._slave_ready_monotonic = time.monotonic()
            self._publish_role()
            return selected

    def _feedback_wrapper_is_fresh(
        self, wrapper: Any, label: str, not_before_wall: Optional[float] = None
    ) -> bool:
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

    def _wait_for_ctrl_mode(
        self, expected_ctrl_mode: int, not_before_wall: Optional[float] = None
    ) -> bool:
        deadline = time.monotonic() + self.role_verify_timeout_sec
        while True:
            status = self.piper.GetArmStatus()
            if self._feedback_wrapper_is_fresh(
                status, "arm status", not_before_wall=not_before_wall
            ):
                if int(status.arm_status.ctrl_mode) == expected_ctrl_mode:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.role_verify_poll_sec)

    def _verify_role(
        self, role: Role, not_before_wall: Optional[float] = None
    ) -> bool:
        if role is Role.MASTER:
            # In master mode Piper remaps its outgoing CAN frames from the
            # slave feedback IDs (0x2A*) to the master control IDs
            # (0x155/0x156/0x157).  ArmStatus therefore stops updating; fresh
            # master joint-control feedback is the hardware confirmation.
            deadline = time.monotonic() + self.role_verify_timeout_sec
            while True:
                master_joint = self.piper.GetArmJointCtrl()
                if self._feedback_wrapper_is_fresh(
                    master_joint,
                    "master joint control",
                    not_before_wall=not_before_wall,
                ):
                    return True
                if time.monotonic() >= deadline:
                    return False
                time.sleep(self.role_verify_poll_sec)

        # MasterSlaveConfig(0xFC) returns the arm to standby.  Do not enable
        # motors or enter CAN/MoveJ here: doing so could execute an old cached
        # target before a fresh post-transition command is available.
        return self._wait_for_ctrl_mode(0x00, not_before_wall=not_before_wall)

    def _all_motors_enabled(self) -> bool:
        motors = self.piper.GetArmLowSpdInfoMsgs()
        if not self._feedback_wrapper_is_fresh(motors, "motor status"):
            return False
        return all(
            bool(getattr(motors, "motor_{}".format(index)).foc_status.driver_enable_status)
            for index in range(1, 7)
        )

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
                    "enable not confirmed for {} within {:.2f}s".format(
                        self.can_port, self.enable_verify_timeout_sec
                    )
                )
            time.sleep(self.enable_verify_poll_sec)

    def _preload_current_pose(self) -> None:
        """Replace any cached target with the measured pose while in standby."""
        joint_feedback = self.piper.GetArmJointMsgs()
        if not self._feedback_wrapper_is_fresh(joint_feedback, "joint feedback"):
            raise RuntimeError("joint feedback is missing or stale")
        joint = joint_feedback.joint_state
        raw_joints = (
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
            if not self._feedback_wrapper_is_fresh(
                gripper_feedback, "gripper feedback"
            ):
                raise RuntimeError("gripper feedback is missing or stale")
            gripper_angle = gripper_feedback.gripper_state.grippers_angle
        # Reuse the conversion validator to reject missing/non-finite raw data.
        feedback_to_joint_positions(raw_joints, gripper_angle)
        self.piper.JointCtrl(
            *raw_joints
        )
        if self.gripper_exist:
            self.piper.GripperCtrl(gripper_angle, 1000, 0x01, 0)

    def _activate_motion_for_fresh_command(self) -> None:
        self._preload_current_pose()
        if self.auto_enable:
            self._ensure_enabled()
        motion_request_wall = time.time()
        self.piper.MotionCtrl_2(0x01, 0x01, 100)
        if not self._wait_for_ctrl_mode(
            0x01, not_before_wall=motion_request_wall
        ):
            raise RuntimeError("CAN/MoveJ mode not confirmed by fresh hardware feedback")
        if self.auto_enable and not self._all_motors_enabled():
            raise RuntimeError("motor enable state was lost while entering CAN/MoveJ")
        self._set_motion_ready(True)

    def handle_set_master(self, request: Any) -> SetBoolResponse:
        target = Role.MASTER if bool(request.data) else Role.SLAVE
        try:
            selected = self._apply_role(target)
        except Exception as exc:
            self.ros.logerr(
                "Task2 rear arm %s role switch to %s failed: %s",
                self.can_port,
                target.value,
                exc,
            )
            return SetBoolResponse(success=False, message=str(exc))
        return SetBoolResponse(
            success=True,
            message="{} role requested on {}".format(selected.value, self.can_port),
        )

    @staticmethod
    def _message_stamp_sec(message: JointState) -> Optional[float]:
        stamp = getattr(getattr(message, "header", None), "stamp", None)
        if stamp is None or not hasattr(stamp, "to_sec"):
            return None
        value = float(stamp.to_sec())
        return None if value == 0.0 else value

    def joint_command_callback(self, message: JointState) -> None:
        arrival_monotonic = time.monotonic()
        with self._hardware_lock:
            callback_age = time.monotonic() - arrival_monotonic
            if callback_age > self.command_timeout_sec:
                self.ros.logwarn_throttle(
                    1.0,
                    "Task2 rear arm %s dropped command after %.3fs callback wait",
                    self.can_port,
                    callback_age,
                )
                return
            if (
                self._slave_ready_monotonic is None
                or arrival_monotonic < self._slave_ready_monotonic
            ):
                self.ros.logwarn_throttle(
                    1.0,
                    "Task2 rear arm %s dropped command received before slave transition completed",
                    self.can_port,
                )
                return
            if list(message.name) != JOINT_NAMES:
                self.ros.logwarn_throttle(
                    1.0,
                    "Task2 rear arm %s dropped command: joint names must be %s",
                    self.can_port,
                    JOINT_NAMES,
                )
                return
            try:
                now_sec = float(self.ros.Time.now().to_sec())
                encoded = self.controller.encode_command(
                    message.position,
                    now_sec=now_sec,
                    stamp_sec=self._message_stamp_sec(message),
                )
            except (RoleError, StaleCommandError, CommandValidationError) as exc:
                self.ros.logwarn_throttle(
                    1.0, "Task2 rear arm %s dropped command: %s", self.can_port, exc
                )
                return

            try:
                activated_now = False
                if not self._motion_ready:
                    self._activate_motion_for_fresh_command()
                    activated_now = True
                if activated_now:
                    # Enabling and mode verification may block.  Do not send a
                    # command that became stale during that safety sequence.
                    callback_age = time.monotonic() - arrival_monotonic
                    if callback_age > self.command_timeout_sec:
                        self.ros.logwarn_throttle(
                            1.0,
                            "Task2 rear arm %s dropped post-activation command after %.3fs",
                            self.can_port,
                            callback_age,
                        )
                        return
                    try:
                        encoded = self.controller.encode_command(
                            message.position,
                            now_sec=float(self.ros.Time.now().to_sec()),
                            stamp_sec=self._message_stamp_sec(message),
                        )
                    except (
                        RoleError,
                        StaleCommandError,
                        CommandValidationError,
                    ) as exc:
                        self.ros.logwarn_throttle(
                            1.0,
                            "Task2 rear arm %s dropped post-activation command: %s",
                            self.can_port,
                            exc,
                        )
                        return
                self.piper.JointCtrl(*encoded[:6])
                if self.gripper_exist:
                    self.piper.GripperCtrl(encoded[6], 1000, 0x01, 0)
            except Exception as exc:
                self._set_motion_ready(False)
                self.controller.latch_fault()
                self._publish_role()
                self.ros.logerr(
                    "Task2 rear arm %s command send failed: %s", self.can_port, exc
                )

    def _new_joint_state(self, positions, gripper_effort: float = 0.0) -> JointState:
        message = JointState()
        message.header.stamp = self.ros.Time.now()
        message.name = list(JOINT_NAMES)
        message.position = list(positions)
        message.velocity = [0.0] * 7
        message.effort = [0.0] * 6 + [float(gripper_effort)]
        return message

    def _publish_actual_joint_state(self) -> None:
        joint = self.piper.GetArmJointMsgs().joint_state
        raw_joints = [
            joint.joint_1,
            joint.joint_2,
            joint.joint_3,
            joint.joint_4,
            joint.joint_5,
            joint.joint_6,
        ]
        gripper_angle = 0
        gripper_effort = 0.0
        if self.gripper_exist:
            gripper = self.piper.GetArmGripperMsgs().gripper_state
            gripper_angle = gripper.grippers_angle
            gripper_effort = float(gripper.grippers_effort) / 1000.0
        positions = feedback_to_joint_positions(raw_joints, gripper_angle)
        self.joint_state_pub.publish(
            self._new_joint_state(positions, gripper_effort=gripper_effort)
        )

    def _publish_master_joint_state(self) -> None:
        joint = self.piper.GetArmJointCtrl().joint_ctrl
        raw_joints = [
            joint.joint_1,
            joint.joint_2,
            joint.joint_3,
            joint.joint_4,
            joint.joint_5,
            joint.joint_6,
        ]
        gripper_angle = 0
        if self.gripper_exist:
            gripper_angle = (
                self.piper.GetArmGripperCtrl().gripper_ctrl.grippers_angle
            )
        positions = feedback_to_joint_positions(raw_joints, gripper_angle)
        self.master_joint_pub.publish(self._new_joint_state(positions))

    def _publish_arm_status(self) -> None:
        source = self.piper.GetArmStatus().arm_status
        message = PiperStatusMsg()
        for field in (
            "ctrl_mode",
            "arm_status",
            "mode_feedback",
            "teach_status",
            "motion_status",
            "trajectory_num",
            "err_code",
        ):
            source_field = "mode_feed" if field == "mode_feedback" else field
            setattr(message, field, getattr(source, source_field))
        for index in range(1, 7):
            setattr(
                message,
                "joint_{}_angle_limit".format(index),
                getattr(source.err_status, "joint_{}_angle_limit".format(index)),
            )
            setattr(
                message,
                "communication_status_joint_{}".format(index),
                getattr(
                    source.err_status,
                    "communication_status_joint_{}".format(index),
                ),
            )
        self.arm_status_pub.publish(message)

    def publish_feedback_once(self) -> None:
        with self._hardware_lock:
            try:
                self._publish_actual_joint_state()
                self._publish_arm_status()
                if self.role is Role.MASTER:
                    self._publish_master_joint_state()
            except Exception as exc:
                self.ros.logwarn_throttle(
                    1.0, "Task2 rear arm %s feedback read failed: %s", self.can_port, exc
                )

    def run(self) -> None:
        rate = self.ros.Rate(self.publish_rate)
        while not self.ros.is_shutdown():
            self.publish_feedback_once()
            rate.sleep()


def main() -> None:
    rospy.init_node("piper_rear_role_task2_node", anonymous=False)
    node = PiperRearRoleNode()
    node.run()


if __name__ == "__main__":
    main()

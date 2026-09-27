#!/usr/bin/env python3
"""Pause-aware Task2 adapter for the deployed local Pi0.5 client."""

import copy
from collections import deque
import threading
import time
from typing import Iterable, Optional

import numpy as np
import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import Header
from std_srvs.srv import SetBool, SetBoolResponse

import inference_pi05 as base


LEFT_POLICY_TOPIC = "/task2/policy/joint_left"
RIGHT_POLICY_TOPIC = "/task2/policy/joint_right"
PAUSE_SERVICE = "/task2/policy/set_paused"


class Task2RosInterface(base.RosInterface):
    """Reuse Pi0.5 observations while making action lifecycle interruptible."""

    def __init__(self, args, ros_api=rospy):
        task_args = copy.copy(args)
        requested_shadow = bool(task_args.shadow_mode)
        task_args.puppet_arm_left_cmd_topic = LEFT_POLICY_TOPIC
        task_args.puppet_arm_right_cmd_topic = RIGHT_POLICY_TOPIC

        # Prevent the base class from ever constructing its command publishers.
        # This adapter constructs only the two coordinator input publishers.
        task_args.shadow_mode = True
        super().__init__(task_args)
        self.args.shadow_mode = requested_shadow

        self.ros = ros_api
        self._task2_lock = threading.RLock()
        self._paused = True
        self._generation = 0
        self.pending_actions = deque()
        self.left_publisher = None
        self.right_publisher = None
        if not requested_shadow:
            self.left_publisher = self.ros.Publisher(
                LEFT_POLICY_TOPIC, JointState, queue_size=10
            )
            self.right_publisher = self.ros.Publisher(
                RIGHT_POLICY_TOPIC, JointState, queue_size=10
            )
        self.pause_service = self.ros.Service(
            PAUSE_SERVICE, SetBool, self.handle_set_paused
        )

    @property
    def paused(self) -> bool:
        with self._task2_lock:
            return self._paused

    @property
    def generation(self) -> int:
        with self._task2_lock:
            return self._generation

    def _clear_runtime_state_locked(self) -> None:
        self.front_images.clear()
        self.left_images.clear()
        self.right_images.clear()
        self.left_joints.clear()
        self.right_joints.clear()
        self.pending_actions.clear()
        self.last_command = None

    def handle_set_paused(self, request) -> SetBoolResponse:
        requested_pause = bool(request.data)
        with self._task2_lock:
            with self.lock:
                self._generation += 1
                self._paused = requested_pause
                self._clear_runtime_state_locked()
            generation = self._generation
        return SetBoolResponse(
            success=True,
            message=("paused" if requested_pause else "fresh resume")
            + " generation={}".format(generation),
        )

    def capture_generation(self) -> Optional[int]:
        with self._task2_lock:
            return None if self._paused else self._generation

    def generation_is_current(self, generation: Optional[int]) -> bool:
        with self._task2_lock:
            return (
                generation is not None
                and not self._paused
                and generation == self._generation
            )

    def accept_inference_actions(
        self, generation: Optional[int], actions: Iterable
    ) -> bool:
        with self._task2_lock:
            if (
                generation is None
                or self._paused
                or generation != self._generation
            ):
                return False
            self.pending_actions.clear()
            self.pending_actions.extend(actions)
            return True

    def pop_next_action(self, generation: Optional[int]):
        with self._task2_lock:
            if (
                generation is None
                or self._paused
                or generation != self._generation
            ):
                self.pending_actions.clear()
                return None
            if not self.pending_actions:
                return None
            return self.pending_actions.popleft()

    @staticmethod
    def _paired_joint_message(position, stamp) -> JointState:
        message = JointState()
        message.header = Header()
        message.header.stamp = stamp
        message.name = list(base.JOINT_NAMES)
        message.position = np.asarray(position, dtype=np.float64).tolist()
        return message

    def publish_action(self, left, right):
        command = np.concatenate(
            [
                np.asarray(left, dtype=np.float64),
                np.asarray(right, dtype=np.float64),
            ]
        )
        if command.shape != (14,):
            raise ValueError("published command must have shape (14,)")
        if not np.isfinite(command).all():
            raise ValueError("published command contains NaN or infinity")

        with self._task2_lock:
            if self._paused:
                return False
            with self.lock:
                if self.args.shadow_mode:
                    print(
                        "[pi05-task2] SHADOW left=%s right=%s"
                        % (
                            np.asarray(left).round(6).tolist(),
                            np.asarray(right).round(6).tolist(),
                        ),
                        flush=True,
                    )
                else:
                    stamp = self.ros.Time.now()
                    if float(stamp.to_sec()) <= 0.0:
                        raise RuntimeError("ROS clock returned a zero action timestamp")
                    self.left_publisher.publish(
                        self._paired_joint_message(left, stamp)
                    )
                    self.right_publisher.publish(
                        self._paired_joint_message(right, stamp)
                    )
                self.last_command = command
            return True

    def publish_policy_action_for_generation(self, action, generation) -> bool:
        """Limit and publish one action under the same generation lock."""
        with self._task2_lock:
            if (
                generation is None
                or self._paused
                or generation != self._generation
            ):
                return False
            with self.lock:
                previous = (
                    None
                    if self.last_command is None
                    else np.asarray(self.last_command, dtype=np.float64).copy()
                )
            if previous is None:
                positions = self._latest_joint_positions()
                if positions is None:
                    return False
                previous = np.concatenate(positions)
            limited = base.limit_action_step(
                action, previous, self.args.arm_steps_length
            )
            return bool(self.publish_action(limited[:7], limited[7:14]))


def main():
    args = base.get_arguments()
    if args.use_init_pose:
        raise RuntimeError(
            "Task2 handover requires --use-init-pose false; initial motion must not "
            "bypass the coordinator lifecycle"
        )
    print(
        "[pi05-task2] connecting to policy server at %s:%d"
        % (args.host, args.port),
        flush=True,
    )
    policy = base.websocket_client_policy.WebsocketClientPolicy(
        host=args.host, port=args.port
    )
    metadata = policy.get_server_metadata()
    print(
        "[pi05-task2] policy server metadata keys: %s" % sorted(metadata),
        flush=True,
    )

    ros = Task2RosInterface(args)
    rate = rospy.Rate(args.publish_rate)
    published_steps = 0
    while published_steps < args.max_publish_step and not rospy.is_shutdown():
        generation = ros.capture_generation()
        if generation is None:
            rate.sleep()
            continue
        if not args.auto_continue and not base.wait_for_chunk_approval(
            args.execute_steps
        ):
            return

        observation = ros.wait_for_observation()
        if not ros.generation_is_current(generation):
            continue
        request_start = time.time()
        actions = base.validate_actions(policy.infer(observation))
        print(
            "[pi05-task2] received %d actions in %.3fs"
            % (actions.shape[0], time.time() - request_start),
            flush=True,
        )
        steps_to_execute = min(
            args.execute_steps,
            actions.shape[0],
            args.max_publish_step - published_steps,
        )
        if not ros.accept_inference_actions(
            generation, actions[:steps_to_execute]
        ):
            continue

        while True:
            action = ros.pop_next_action(generation)
            if action is None or rospy.is_shutdown():
                break
            action = base.apply_right_gripper(
                action,
                threshold=args.right_gripper_threshold,
                closed=args.right_gripper_closed,
                opened=args.right_gripper_open,
                mode=args.right_gripper_mode,
            )
            if not ros.generation_is_current(generation):
                break
            if not ros.publish_policy_action_for_generation(action, generation):
                break
            published_steps += 1
            rate.sleep()

    print("[pi05-task2] reached max publish step: %d" % published_steps, flush=True)


if __name__ == "__main__":
    main()

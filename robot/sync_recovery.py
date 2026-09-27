#!/usr/bin/env python3
"""Reset handover software latch; no CAN, enable, homing, gripper or policy resume."""
import math
import threading
import time
import rospy
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool
from std_srvs.srv import Trigger

TOPICS = {
    "front_left": ("/puppet/joint_left", JointState),
    "front_right": ("/puppet/joint_right", JointState),
    "rear_left": ("/task2/teach/rear_left/joint_states", JointState),
    "rear_right": ("/task2/teach/rear_right/joint_states", JointState),
    "button_left": ("/task2/teach/rear_left/teach_active", Bool),
    "button_right": ("/task2/teach/rear_right/teach_active", Bool),
}
SERVICE = "/task2/teach_handover/reset_fault"

def validate_snapshot(snapshot, now_monotonic, now_wall, max_age=.1):
    for key in TOPICS:
        if key not in snapshot:
            raise RuntimeError("缺少反馈："+key)
        message, arrival = snapshot[key]
        if now_monotonic-arrival > max_age:
            raise RuntimeError("反馈未刷新："+key)
        if key.startswith("button_"):
            if message.data:
                raise RuntimeError("请先释放两侧后臂示教按钮")
        else:
            values = list(message.position)
            stamp = message.header.stamp.to_sec()
            if len(values) != 7 or not all(math.isfinite(v) for v in values):
                raise RuntimeError("关节反馈无效："+key)
            if stamp and (now_wall-stamp > max_age or stamp-now_wall > max_age):
                raise RuntimeError("关节反馈时间戳过期："+key)

def recover_sync(timeout_sec=2.):
    if not rospy.core.is_initialized():
        rospy.init_node("platform_sync_recovery", anonymous=True, disable_signals=True)
    rospy.wait_for_service(SERVICE, timeout=timeout_sec)
    snapshot = {}
    lock = threading.Lock()
    subscribers = []
    def callback(key):
        def receive(message):
            with lock:
                snapshot[key] = (message, time.monotonic())
        return receive
    try:
        for key, (topic, kind) in TOPICS.items():
            subscribers.append(rospy.Subscriber(topic, kind, callback(key), queue_size=1))
        deadline = time.monotonic()+timeout_sec
        last = RuntimeError("等待关节反馈")
        while time.monotonic() < deadline and not rospy.is_shutdown():
            with lock:
                current = dict(snapshot)
            if any(current.get(k) and current[k][0].data for k in ("button_left", "button_right")):
                raise RuntimeError("请先释放两侧后臂示教按钮")
            try:
                validate_snapshot(current, time.monotonic(), time.time())
            except RuntimeError as exc:
                last = exc
                time.sleep(.02)
                continue
            response = rospy.ServiceProxy(SERVICE, Trigger)()
            if not response.success:
                raise RuntimeError(response.message)
            return response
        raise last
    finally:
        for subscriber in subscribers:
            subscriber.unregister()

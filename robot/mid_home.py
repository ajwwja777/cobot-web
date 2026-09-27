"""Middle arm only; use its existing ROS driver, never open CAN/enable."""
import errno
import fcntl
import json
import os
import threading
import time
import urllib.error
import urllib.request
import rosgraph
import rospy
from piper_msgs.msg import PiperStatusMsg
from sensor_msgs.msg import JointState
import task2_homing_core as homing

FEEDBACK = "/puppet/joint_mid"
COMMAND = "/master/joint_mid"
STATUS = "/puppet/arm_status_mid"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_target(config, pose):
    entry = config.get("poses", {}).get(pose, {})
    if not isinstance(entry, dict) or "mid" not in entry:
        raise homing.HomingError("位姿 {!r} 没有中臂记录；先用 home.sh capture --arm mid --pose {}".format(pose, pose))
    return homing.validate_pose(entry["mid"], "mid")


def check_publishers(master):
    publishers, subscribers, _ = master.getSystemState()
    owners = [node for topic, nodes in publishers if topic == COMMAND
              for node in nodes if node != rospy.get_name()]
    if owners:
        raise RuntimeError("中臂已有控制发布者 {}；先在其终端 Ctrl-C 停止（如 fix_mid_camera_pose.py）".format(", ".join(owners)))
    if not any(topic == COMMAND and nodes for topic, nodes in subscribers):
        raise RuntimeError("中臂驱动未订阅 {}；先按原流程启动机械臂".format(COMMAND))


def check_session():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8026/api/session", timeout=1.0) as response:
            session = json.loads(response.read())
    except urllib.error.URLError as exc:
        if getattr(exc.reason, "errno", None) == errno.ECONNREFUSED:
            return
        raise RuntimeError("无法确认 RLT 策略已暂停：{}".format(exc))
    phase = session.get("phase")
    paused_phases = {
        "stopped", "idle", "disarmed", "armed", "ready", "paused",
        "terminal_pending", "finalizing", "replay_committing", "waiting_scene", "fault",
    }
    # A loaded model and an open Session do not own the middle-arm publisher.
    # Permit homing between trials or during an explicit policy pause. Check
    # the actual pause flag as well as phase: no motion during rollout/start/HIL
    # or if a stale/partial response cannot establish a paused policy.
    if phase not in paused_phases or session.get("policy_paused") is not True:
        raise RuntimeError("先暂停策略，再移动中臂相机（当前 {}）；无需结束 Session 或释放模型".format(phase))
    return phase


class Feedback:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()
        self.subs = [rospy.Subscriber(FEEDBACK, JointState, self.joints, queue_size=1),
                     rospy.Subscriber(STATUS, PiperStatusMsg, self.status, queue_size=1)]

    def update(self, key, message):
        with self.lock:
            self.values[key] = (message, time.monotonic())

    def joints(self, message):
        self.update("joints", message)

    def status(self, message):
        self.update("status", message)

    def read(self):
        with self.lock:
            data = dict(self.values)
        now = time.monotonic()
        for key in ("joints", "status"):
            if key not in data or now-data[key][1] > 0.5:
                raise RuntimeError("中臂{}反馈未就绪或过期；停止移动".format(key))
        joint, status = data["joints"][0], data["status"][0]
        stamp = joint.header.stamp.to_sec()
        age = rospy.Time.now().to_sec()-stamp
        if stamp <= 0 or age > 0.5 or age < -0.1:
            raise RuntimeError("中臂关节反馈时间戳异常；停止移动")
        if status.ctrl_mode != 1 or status.arm_status != 0 or status.err_code != 0 or status.teach_status != 0:
            raise RuntimeError("中臂未就绪：mode={} status={} err={} teach={}；不自动使能/清错".format(
                status.ctrl_mode, status.arm_status, status.err_code, status.teach_status))
        return homing.validate_pose(list(joint.position)[:7], "mid feedback")

    def close(self):
        for sub in self.subs:
            sub.unregister()


def move(target, config, assume_yes=False):
    directory = os.path.join(ROOT, "runtime", "arms")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "home-mid.lock"), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有中臂归位命令在运行")
        return _move(target, config, assume_yes=assume_yes)


def _move(target, config, assume_yes=False):
    master = rosgraph.Master(rospy.get_name())
    check_publishers(master)
    check_session()
    feedback, pub = Feedback(), None
    try:
        deadline = time.monotonic()+5.0
        while True:
            try:
                feedback.read()
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        if not assume_yes:
            input("仅移动中臂；确认相机/线缆路径安全后按 Enter，取消按 Ctrl-C：")
        joint_speed, gripper_speed, rate = homing.speed_settings(config)
        pub = rospy.Publisher(COMMAND, JointState, queue_size=1)
        deadline = time.monotonic()+3.0
        while pub.get_num_connections() == 0:
            if time.monotonic() >= deadline:
                raise RuntimeError("中臂驱动连接超时；未发移动指令")
            feedback.read()
            time.sleep(0.02)

        # Match the front/all-home trajectory contract: complete every remote
        # ownership/session check before planning, then publish one continuous
        # speed-limited ramp at the configured fixed rate.  The old loop called
        # ROS Master and the RLT HTTP endpoint every 0.2 seconds; their variable
        # latency blocked this control thread and made the middle arm pause in
        # visible steps even though its waypoints were continuous.
        check_publishers(master)
        check_session()
        start = feedback.read()
        target = tuple(target[:6]) + (start[6],)
        ramp = homing.plan_ramp(start, target, joint_speed/rate, gripper_speed/rate)
        period = 1.0/rate

        def publish(values):
            if rospy.is_shutdown():
                raise RuntimeError("ROS 已退出；停止移动")
            current = feedback.read()
            message = JointState()
            message.header.stamp = rospy.Time.now()
            message.name = ["joint"+str(i) for i in range(7)]
            message.position = list(values)
            pub.publish(message)
            return current

        for waypoint in ramp:
            publish(waypoint)
            time.sleep(period)
        # Standalone mid homing publishes the requested target without angle
        # clipping. Arrival is inspected by the onsite operator, not a CLI gate.
        for _ in range(max(1, int(rate*0.2))):
            publish(target)
            time.sleep(period)
        current = feedback.read()
        errors = [abs(a-b) for a,b in zip(current[:6], target[:6])]
        worst = max(range(6), key=errors.__getitem__)
        print("中臂目标发送完成；实测最大偏差 {:.6f} rad（关节{}）。夹爪开度保持不变。".format(errors[worst], worst+1))
        print("关节{}：目标 {:.6f} rad，实测 {:.6f} rad；是否到位由现场确认。".format(worst+1, target[worst], current[worst]))
        return True

    finally:
        if pub is not None:
            pub.unregister()
        feedback.close()

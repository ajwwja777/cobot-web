"""Read-only display health, independent of the motion safety state machine."""
import math
import time

ARMS = ("front-left", "front-right", "mid", "rear-left", "rear-right")
MESSAGES = {
 "ready": ("反馈正常", "Feedback ready", "", ""),
 "ros": ("ROS 未连接", "ROS disconnected", "启动 ROS；若已启动，检查 ROS_MASTER_URI 和网络。", "Start ROS; if running, check ROS_MASTER_URI and the network."),
 "can": ("CAN 未连接或接口未启用", "CAN disconnected or interface down", "检查电源、USB/CAN 线，再配置 CAN。", "Check power and USB/CAN cables, then configure CAN."),
 "feedback": ("未收到机械臂反馈", "No arm feedback", "检查机械臂电源和对应 CAN 线；确认接口配置。", "Check arm power, its CAN cable and interface configuration."),
 "node": ("机械臂节点未启动", "Arm node offline", "启动机械臂节点；失败时查看机械臂输出。", "Start arm nodes; inspect Arms output if launch fails."),
 "hardware": ("机械臂报告保护或故障", "Arm reports protection or a fault", "查看错误码，排除碰撞或接线问题，再执行恢复。", "Inspect the error code, clear collision/wiring problems, then recover."),
 "disabled": ("关节未全部使能", "Not all joints enabled", "检查机械臂输出并恢复对应臂；不要重复 launch。", "Inspect Arms output and recover this arm; do not launch duplicate nodes."),
 "front_teach": ("前臂独立进入示教", "Front arm entered local teach mode", "退出前臂本地示教；使用对应后臂的示教按钮。", "Exit local teach on the front arm; use the paired rear teach button."),
 "rear_release": ("退出示教后仍使能", "Still enabled after leaving teach mode", "松开示教按钮，检查后臂输出；排除故障后恢复该后臂。", "Release the teach button, inspect rear-arm output and recover after resolving the fault."),
 "rear_mode": ("后臂模式异常", "Unexpected rear-arm mode", "检查示教按钮和后臂输出；确认退出后失能，再恢复。", "Check the teach button and rear-arm output; verify disable on release before recovery."),
 "pair": ("对应后臂尚未就绪，无法示教同步", "Paired rear arm unavailable for teaching", "检查对应后臂的 CAN、节点和故障提示。", "Check the paired rear arm CAN, node and fault details."),
 "mode": ("前臂控制模式异常", "Unexpected front-arm control mode", "退出本地控制并检查机械臂输出，再恢复对应前臂。", "Exit local control, inspect Arms output and recover the front arm."),
 "route": ("前臂控制通路未就绪", "Front-arm control route unavailable", "检查协调器与前臂节点；排除重复发布者后重新启动机械臂节点。", "Check the coordinator and front driver; remove duplicate publishers before restarting arm nodes."),
 "handover": ("示教协调器反馈缺失或故障", "Teach coordinator feedback missing or faulty", "查看机械臂输出；检查同步通路并执行同步恢复。", "Inspect Arms output, check the control route and run sync recovery."),
 "sync": ("示教已进入，前后臂同步尚未确认", "Teach active; paired synchronization not verified", "检查前后臂与协调器反馈；若持续异常，松开示教并检查输出。", "Check front/rear/coordinator feedback; if this persists, release teach and inspect output."),
 "teaching": ("前后臂示教同步正常", "Paired teach synchronization ready", "", ""),
 "idle": ("后臂已退出示教，正常失能", "Rear arm idle and disabled", "", ""),
 "gripper": ("夹爪报告故障", "Gripper reports a fault", "检查夹爪错误码、线缆和机械卡滞，再恢复夹爪。", "Inspect the gripper error code, cable and mechanical obstruction, then recover it."),
}
def result(code, phase="warning", raw=""):
    zh, en, remedy_zh, remedy_en = MESSAGES[code]
    return dict(code=code, phase=phase, reason_zh=zh, reason_en=en,
                remedy_zh=remedy_zh, remedy_en=remedy_en, detail=raw)

class DeviceHealth:
    def __init__(self):
        self.rear_modes = {}
        self.rear_exited = {}

    def evaluate(self, systems, snapshot, *, home_started=0, now=None):
        now = time.time() if now is None else now
        feedback = systems.get("arms_feedback", {})
        nodes = systems.get("arm_nodes", {})
        can = systems.get("can_interfaces", {})
        routes = systems.get("control_routes", {})
        values = {}
        for name in ARMS:
            arm = feedback.get(name, {})
            mode = arm.get("rear_mode")
            if name.startswith("rear-"):
                if self.rear_modes.get(name) == "teaching" and mode != "teaching":
                    self.rear_exited[name] = now
                if mode in ("teaching", "idle_disabled") or home_started > self.rear_exited.get(name, float("inf")):
                    self.rear_exited.pop(name, None)
                self.rear_modes[name] = mode
            raw = arm.get("detail", "")
            if not can.get(name, systems.get("can", {}).get("phase") == "ready"):
                value = result("can", raw=raw)
            elif not arm.get("fresh"):
                value = result("feedback", "offline", raw)
            elif systems.get("roscore", {}).get("phase") != "ready":
                value = result("ros", raw=raw)
            elif not nodes.get(name):
                value = result("node", raw=raw)
            elif arm.get("error_code") or arm.get("protected_joints") or arm.get("arm_status"):
                value = result("hardware", raw=raw)
            elif name.startswith("front-") or name == "mid":
                if arm.get("teach_status") or arm.get("ctrl_mode") == 2:
                    value = result("front_teach", raw=raw)
                elif arm.get("ctrl_mode") not in (0, 1):
                    value = result("mode", raw=raw)
                elif arm.get("enabled_joints") != 6:
                    value = result("disabled", raw=raw)
                elif name != "mid" and not routes.get(name.split("-")[1], {}).get("ready"):
                    value = result("route", raw=raw)
                elif name != "mid" and (not snapshot.has_value("handover_fault") or snapshot.get("handover_fault")):
                    value = result("handover", raw=raw + " " + str(snapshot.get("handover_fault") or ""))
                else:
                    value = result("ready", "ready", raw)
            elif name in self.rear_exited and now - self.rear_exited[name] > 1:
                value = result("rear_release", raw=raw)
            elif mode == "idle_disabled":
                value = result("idle", "ready", raw)
            elif mode in ("teaching", "can_holding"):
                value = result("ready", "ready", raw)
            else:
                value = result("rear_mode", raw=raw)
            values[name] = value
        for side in ("left","right"):
            front, rear = "front-"+side, "rear-"+side
            if values[front]["phase"]=="ready" and values[rear]["phase"] not in ("ready","teaching"):
                values[front]=result("pair",raw=values[rear]["detail"])
        mode = snapshot.get("handover_mode") or ""
        for side in ("left", "right"):
            front, rear = "front-" + side, "rear-" + side
            hardware_teach = feedback.get(rear, {}).get("rear_mode") == "teaching"
            teach = snapshot.get("teach_" + side) is True
            if not hardware_teach and not teach:
                continue
            routed = mode.startswith("manual:") and side in mode.split(":", 1)[1].split("+")
            keys = ("rear_" + side, "front_" + side, "coordinator_" + side, "teach_" + side)
            fresh = all(snapshot.is_fresh(key) for key in keys)
            tracking = False
            if fresh:
                actual = snapshot.get("front_" + side)["position"]
                command = snapshot.get("coordinator_" + side)["position"]
                tracking = len(actual) >= 6 and len(command) >= 6 and all(math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a)-float(b)) < .15
                               for a, b in zip(actual[:6], command[:6]))
            synchronized = hardware_teach and teach and routed and fresh and tracking and all(values[n]["phase"] == "ready" for n in (front,rear))
            for name in (front, rear):
                if values[name]["phase"] == "ready":
                    values[name] = result("teaching" if synchronized else "sync", "teaching" if synchronized else "warning",
                                          feedback.get(name, {}).get("detail", ""))
        for side in ("left", "right"):
            gripper = feedback.get("front-" + side, {}).get("gripper", {})
            values["gripper-" + side] = (result("gripper", raw=gripper.get("detail", "")) if gripper.get("error_bits")
                                        else dict(values["front-" + side]))
        return values

"""Presentation labels for cobot-control health codes."""
from .control_import import control_package
control_package()
from cobot_control.device_health import DeviceHealth as HardwareHealth, ARMS
MESSAGES = {
 "ready": ("反馈正常", "Feedback ready", "", ""),
 "ros": ("ROS 未连接", "ROS disconnected", "启动 ROS；若已启动，检查 ROS_MASTER_URI 和网络。", "Start ROS; if running, check ROS_MASTER_URI and the network."),
 "can": ("CAN 未连接或接口未启用", "CAN disconnected or interface down", "检查电源、USB/CAN 线，再配置 CAN。", "Check power and USB/CAN cables, then configure CAN."),
 "feedback": ("未收到机械臂反馈", "No arm feedback", "检查机械臂电源和对应 CAN 线；确认接口配置。", "Check arm power, its CAN cable and interface configuration."),
 "can_tx": ("CAN 发送队列堵塞，接收正常不代表能控制", "CAN transmit queue stalled; reception does not prove control",
            "停止推理/示教并支撑机械臂，再对该臂执行恢复以复位其 CAN；不要连续发送动作。", "Stop policy/teaching and support the arm, then recover this arm to reset its CAN; do not keep sending commands."),
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
 "teach_exit": ("正在退出示教", "Leaving teaching mode", "", ""),
 "teach_transition": ("示教接管中", "Teaching takeover in progress", "", ""),
 "sync_recovering": ("同步正在恢复确认", "Confirming synchronization recovery", "", ""),
 "teaching": ("前后臂示教同步中", "Paired teaching active", "", ""),
 "idle": ("后臂已退出示教，正常失能", "Rear arm idle and disabled", "", ""),
 "gripper": ("夹爪报告故障", "Gripper reports a fault", "检查夹爪错误码、线缆和机械卡滞，再恢复夹爪。", "Inspect the gripper error code, cable and mechanical obstruction, then recover it."),
}

SYNC_MESSAGES = {
    "release": ("退出示教尚未完成", "Teaching release has not completed"),
    "teach_feedback": ("后臂 CAN 尚未确认示教模式", "Rear CAN has not confirmed teaching mode"),
    "teach_button": ("后臂已进入示教，但按钮 ROS 状态未接通", "Rear teaching active but its ROS button signal is missing"),
    "stale": ("反馈或指令过期", "Stale feedback or commands"),
    "routing": ("协调器未接管此侧", "Coordinator has not taken over this side"),
    "joints": ("关节反馈/指令不完整或无效", "Incomplete or invalid joint feedback/command"),
    "tracking": ("前臂未跟上协调器目标", "Front arm is not tracking the coordinator target"),
    "paired_health": ("配对机械臂有异常", "Paired arm has a fault"),
}

class DeviceHealth(HardwareHealth):
    def evaluate(self, *args, **kwargs):
        values = super().evaluate(*args, **kwargs)
        for value in values.values():
            zh, en, remedy_zh, remedy_en = MESSAGES[value["code"]]
            issue = value.get("sync_issue")
            if value["code"] == "sync" and issue in SYNC_MESSAGES:
                zh, en = SYNC_MESSAGES[issue]
                if issue == "stale":
                    suffix = ": " + ", ".join(value["stale_topics"])
                    zh += suffix; en += suffix
                elif issue == "routing":
                    zh += ": " + value["handover_mode"]; en += ": " + value["handover_mode"]
                elif issue == "tracking":
                    zh += "（最大关节误差 {:.3f} rad）".format(value["max_joint_error"])
                    en += " (max joint error {:.3f} rad)".format(value["max_joint_error"])
            paired = value.get("paired_code")
            if paired in MESSAGES:
                pair_zh, pair_en, pair_remedy_zh, pair_remedy_en = MESSAGES[paired]
                zh += "：" + pair_zh; en += ": " + pair_en
                remedy_zh, remedy_en = pair_remedy_zh, pair_remedy_en
            value.update(reason_zh=zh, reason_en=en, remedy_zh=remedy_zh, remedy_en=remedy_en)
        return values

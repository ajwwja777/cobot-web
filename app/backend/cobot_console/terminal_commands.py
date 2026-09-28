"""Readable terminal recipes; this module only formats text, never runs commands."""
import shlex
from pathlib import Path
from .paths import PROJECT, CONTROL

HARDWARE_SCRIPTS = frozenset((
    "arms_up.sh", "cameras_up.sh", "can_up.sh", "roscore_up.sh",
    "home.sh", "recover.sh", "front_mode.sh", "front_reset.sh",
    "teleop.sh", "can_recover_one.sh",
))

def in_directory(root, command):
    return "cd " + shlex.quote(str(root)) + "\n" + command

def script_command(command):
    name = command.split()[0]
    root = CONTROL if name in HARDWARE_SCRIPTS else PROJECT
    return in_directory(root, "./scripts/" + command)

def cli_command(arguments):
    return in_directory(PROJECT, "python3 scripts/console.py " + arguments)

def ros_command_prefix():
    return in_directory(CONTROL, 'source ./scripts/environment.sh\nsource "$TASK5_ROS_SETUP"\n')

def can_command(action):
    if action == "configure":
        return script_command("can_up.sh")
    if action != "reset":
        return ""
    # Same five interfaces and kernel parameters as the web reset operation.
    # Terminal sudo owns its password prompt; no credential shell variable.
    return in_directory(CONTROL, "\n".join((
        "sudo -v",
        "sudo modprobe gs_usb",
        "for iface in can_left can_right can_mid can_rear_left can_rear_right; do",
        '  sudo ip link set "$iface" down || break',
        '  sudo ip link set "$iface" type can bitrate 1000000 restart-ms 100 || break',
        '  sudo ip link set "$iface" up || break',
        "done",
    )))

def implementation_help(command):
    if "./scripts/can_up.sh" in command:
        zh = "scripts/can_up.sh → integrations/legacy_control/can_config_cobot.sh task2；配置接口并检查链路。网页配置失败还会复位并重试一次，终端入口执行一次检查。"
        en = "scripts/can_up.sh → integrations/legacy_control/can_config_cobot.sh task2; configure interfaces and check links. The web action additionally resets and retries once after failure; this terminal entry runs one check."
    elif 'ip link set "$iface" type can' in command:
        zh = "直接执行与网页重置相同的五臂 CAN 内核参数：1 Mbps、restart-ms 100；sudo 会自行提示密码。完成后可单独执行 ./scripts/can_up.sh 检查链路。"
        en = "Apply the same five-arm CAN kernel settings as web reset: 1 Mbps, restart-ms 100. sudo prompts normally. Afterwards, run ./scripts/can_up.sh separately to check the links."
    elif "./scripts/arms_up.sh" in command:
        zh = "scripts/arms_up.sh → robot/arms/arms.launch → piper_start_ms_node.py、piper_rear_teach_task2_node.py、teach_handover.py；此终端前台运行 roslaunch。"
        en = "scripts/arms_up.sh → robot/arms/arms.launch → piper_start_ms_node.py, piper_rear_teach_task2_node.py and teach_handover.py; roslaunch runs in this terminal."
    elif "./scripts/cameras_up.sh" in command:
        zh = "scripts/cameras_up.sh → integrations/legacy_control/launch/multi_camera_shuai.launch → astra_camera/launch/dabai.launch → astra_camera_node；此终端前台运行。"
        en = "scripts/cameras_up.sh → integrations/legacy_control/launch/multi_camera_shuai.launch → astra_camera/launch/dabai.launch → astra_camera_node; runs in this terminal."
    elif "./scripts/roscore_up.sh" in command:
        zh = "scripts/roscore_up.sh → /opt/ros/noetic/bin/roscore -p 11311；脚本在后台启动 ROS Master。"
        en = "scripts/roscore_up.sh → /opt/ros/noetic/bin/roscore -p 11311; the script starts ROS Master in the background."
    elif "./scripts/home.sh" in command:
        zh = "scripts/home.sh → robot/home.py；由已有协调器／后臂服务和中臂实现处理选臂归位或位姿操作。"
        en = "scripts/home.sh → robot/home.py; existing coordinator/rear-arm services and mid-arm code handle selected-arm homing or pose operations."
    elif "./scripts/recover.sh" in command:
        zh = "scripts/recover.sh → robot/recover.py；按目标选择对应恢复逻辑。"
        en = "scripts/recover.sh → robot/recover.py; selects the recovery implementation for the requested target."
    elif "console.py model load" in command:
        zh = "共享模型 API → deployment_run.sh → rl-platform/scripts/rlt_up.sh 或 vla-platform 的 π0.5 入口；后台加载，不在本终端运行模型。"
        en = "Shared model API → deployment_run.sh → rl-platform/scripts/rlt_up.sh or the vla-platform pi0.5 entry; loading runs in the background."
    elif "console.py capture " in command or "console.py --timeout " in command and " capture " in command:
        zh = "调用现有采集 API／状态机；普通采集由 segmented_capture 与 capture_core 实现，RLT 请求转给当前 Session，不另开录制进程。"
        en = "Uses the existing capture API/state machine: segmented_capture and capture_core for ordinary capture, or the current RLT Session; no new recorder process."
    elif "console.py" in command:
        zh = "scripts/console.py 调用共享后端 API；recovery 子命令提供独立排障。关闭此命令终端不等于停止后台任务。"
        en = "scripts/console.py uses shared backend APIs; recovery provides independent diagnostics. Closing this terminal does not stop background tasks."
    else:
        return {}
    return {"zh": "实际实现：" + zh, "en": "Implementation: " + en}

def task_terminal_details(row):
    argv = [str(item) for item in (row.get("command") or [])]
    command = ""
    if argv:
        name = Path(argv[0]).name
        if name == "can_web.sh" and len(argv) == 2:
            command = can_command(argv[1])
        elif name in HARDWARE_SCRIPTS:
            command = script_command(name + (" " + " ".join(shlex.quote(x) for x in argv[1:]) if len(argv) > 1 else ""))
        elif name == "deployment_run.sh" and len(argv) >= 2:
            command = cli_command("model load --id " + shlex.quote(argv[1]))
    return {"terminal_command": command, "implementation": implementation_help(command)}

#!/usr/bin/env python3
"""One open/close cycle on a stationary front gripper, without joint/mode commands."""
import math
from contextlib import ExitStack
import socket
import struct
import sys
import time

CONTROL_IDS = {*range(0x150, 0x15a), 0x470, 0x471}
FEEDBACK_IDS = {0x2a1, 0x2a5, 0x2a6, 0x2a7, 0x2a8, *range(0x261, 0x267)}
FRESH_SEC = 0.5
OPEN_UM = 70000  # Installed Piper SDK nominal maximum: 0.07 m.



def gripper_report(data):
    if len(data)!=8:raise ValueError("夹爪反馈必须为8字节")
    code=data[6]
    labels=["电压过低","电机过温","驱动器过流","驱动器过温","传感器异常","驱动器错误"]
    return {"status_hex":"0x%02x"%code,"opening_mm":struct.unpack(">i",data[:4])[0]/1000,
            "effort_nm":struct.unpack(">h",data[4:6])[0]/1000,
            "enabled":bool(code&0x40),"homed":bool(code&0x80),
            "error_flags":[name for bit,name in enumerate(labels) if code&(1<<bit)]}


def passive_status(bus,seconds=.6):
    ids={0x2a1,0x2a8,*range(0x261,0x267)};latest={}
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        sock.setsockopt(socket.SOL_CAN_RAW,socket.CAN_RAW_FILTER,
                        b"".join(struct.pack("=II",i,0x7ff) for i in sorted(ids)))
        sock.bind((bus,));sock.settimeout(.02)
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            try:raw=sock.recv(16)
            except socket.timeout:continue
            canid,length,data=struct.unpack("=IB3x8s",raw)
            if canid in ids and length==8:latest[canid]=data
    if not ids.issubset(latest):raise RuntimeError(bus+"反馈不完整")
    arm=latest[0x2a1]
    return {"bus":bus,"arm_ctrl_mode":arm[0],"arm_status":arm[1],
            "arm_err_code":int.from_bytes(arm[6:8],"big"),
            "joints_enabled":[bool(latest[i][5]&0x40) for i in range(0x261,0x267)],
            "joint_protection_bits":[hex(latest[i][5]&0xbf) for i in range(0x261,0x267)],
            "gripper":gripper_report(latest[0x2a8])}

def command_frame(opening_um, *, clear_error=False):
    if not 0 <= opening_um <= 80000:
        raise ValueError("夹爪开度超出 0–80 mm")
    # SDK GripperCtrl: int32 opening, uint16 effort=1000, enable=1, zero=0.
    # Error-clear is only used once at the measured hold opening during supervised reinit.
    # Never set zero, disable the gripper, or send arm/joint commands.
    data = struct.pack(">iHBB", opening_um, 1000, 3 if clear_error else 1, 0)
    return struct.pack("=IB3x8s", 0x159, 8, data)


def recovery_frame(opening_um, operation):
    """Build one vendor-documented recovery command at the measured opening.

    Operation 0x02 disables and clears errors; 0x01 enables normally.  Zero is
    never set and the opening target never changes, so this cannot perform the
    reinit open/close cycle.
    """
    if not 0 <= opening_um <= 80000:
        raise ValueError("夹爪开度超出 0–80 mm")
    if operation not in (0x01, 0x02):
        raise ValueError("夹爪恢复只允许 0x02 清错或 0x01 使能")
    data = struct.pack(">iHBB", opening_um, 1000, operation, 0)
    return struct.pack("=IB3x8s", 0x159, 8, data)


class Monitor:
    def __init__(self, sock, clock=time.monotonic, label="夹爪"):
        self.sock, self.clock = sock, clock
        self.label = label
        self.latest = {}
        self.anchor = None

    def poll(self):
        try:
            raw = self.sock.recv(16)
        except socket.timeout:
            return
        canid, length, data = struct.unpack("=IB3x8s", raw)
        if canid & 0xe0000000:
            raise RuntimeError("收到异常 CAN 帧，停止夹爪测试")
        if canid in CONTROL_IDS:
            raise RuntimeError("检测到其他控制指令；停止自主动作/示教/归位后再运行")
        if canid in FEEDBACK_IDS:
            if length != 8:
                raise RuntimeError("CAN 反馈长度异常")
            self.latest[canid] = (self.clock(), data)

    def fresh(self, canid, since=None):
        entry = self.latest.get(canid)
        if entry is None or self.clock() - entry[0] > FRESH_SEC:
            raise RuntimeError("机械臂/夹爪反馈缺失或过期：" + hex(canid))
        if since is not None and entry[0] <= since:
            return None
        return entry[1]

    def check_arm(self):
        status = self.fresh(0x2a1)
        if status[0] != 1 or status[1] or status[3] or int.from_bytes(status[6:8], "big"):
            raise RuntimeError("前臂未处于健康 CAN 保持状态；本脚本不恢复机械臂")
        for canid in range(0x261, 0x267):
            bits = self.fresh(canid)[5]
            if bits & 0xbf or not bits & 0x40:
                raise RuntimeError("前臂关节失能或保护；本脚本不使能关节/清错")
        joints = tuple(v for canid in [0x2a5, 0x2a6, 0x2a7]
                       for v in struct.unpack(">ii", self.fresh(canid)))
        if self.anchor is not None and any(abs(a-b) > math.degrees(0.03)*1000
                                           for a,b in zip(joints, self.anchor)):
            raise RuntimeError("机械臂位置发生变化，停止后续夹爪指令")
        return joints

    def check(self, allow_gripper_latch=False):
        joints = self.check_arm()
        gripper = self.fresh(0x2a8)
        # Some installed Piper grippers keep the advisory 0x10/0x20 latch even
        # while enabled and responding normally.  Treat low four protection
        # bits or a disabled gripper as fatal; verify every requested opening
        # from fresh feedback instead of rejecting a persistent 0x30 alone.
        if gripper[6] & 0x0f or (gripper[6] & 0x30 and not gripper[6] & 0x40):
            report=gripper_report(gripper)
            raise RuntimeError("%s反馈异常：status=%s，标志=%s，开度=%.3fmm；回放/重置已停止" %
                               (self.label,report["status_hex"],"、".join(report["error_flags"]),report["opening_mm"]))
        return joints


def poll_all(monitors, check=True, allow_gripper_latch=False):
    for monitor in monitors.values():
        monitor.poll()
    if check:
        for monitor in monitors.values():
            monitor.check(allow_gripper_latch=allow_gripper_latch)


def recover_one_gripper(sock, monitor, clock=time.monotonic):
    """Recover one front gripper in place with one bounded vendor sequence."""
    deadline=clock()+.6
    while clock()<deadline:
        monitor.poll()
    monitor.anchor=monitor.check_arm()
    before=gripper_report(monitor.fresh(0x2a8))
    opening_um=round(before["opening_mm"]*1000)
    if not 0<=opening_um<=80000:
        raise RuntimeError("%s当前开度 %.3fmm 无法构造原位保持指令" %
                           (monitor.label,before["opening_mm"]))
    initial_code=int(before["status_hex"],16)
    if before["enabled"] and not initial_code&0x3f:
        print("%s状态已正常：%s；未发送恢复指令。" % (monitor.label,before),flush=True)
        return before

    # Piper SDK's installed vendor demo uses 0x02 followed by 0x01.  Preserve
    # the measured opening instead of its hard-coded zero to avoid a recovery
    # command becoming an unintended close motion.
    if initial_code&0x3f:
        sent_clear=monitor.clock()
        sock.sendall(recovery_frame(opening_um,0x02))
        print("%s：保持 %.3fmm，发送一次失能清错 0x02（不设零点）。" %
              (monitor.label,opening_um/1000),flush=True)
        clear_feedback=None
        deadline=clock()+.8
        while clock()<deadline:
            monitor.poll();monitor.check_arm()
            clear_feedback=monitor.fresh(0x2a8,sent_clear)
            if clear_feedback is not None:
                break
        if clear_feedback is None:
            raise RuntimeError(monitor.label+"清错后没有新反馈；不继续使能")

    sent_enable=monitor.clock()
    sock.sendall(recovery_frame(opening_um,0x01))
    print("%s：保持 %.3fmm，发送一次使能 0x01。" %
          (monitor.label,opening_um/1000),flush=True)
    deadline=clock()+3.0;stable_since=None;after=None
    while clock()<deadline:
        monitor.poll();monitor.check_arm()
        data=monitor.fresh(0x2a8,sent_enable)
        if data is None:
            continue
        after=gripper_report(data)
        code=int(after["status_hex"],16)
        if after["enabled"] and not code&0x3f:
            if stable_since is None:
                stable_since=clock()
            if clock()-stable_since>=.2:
                print("%s恢复完成：status=%s，开度=%.3fmm。" %
                      (monitor.label,after["status_hex"],after["opening_mm"]),flush=True)
                return after
        else:
            stable_since=None
    if after is None:
        raise RuntimeError(monitor.label+"使能后没有新反馈；不重试")
    flags="、".join(after["error_flags"]) or "无错误标志"
    advice="；电机过温仍存在，请等待冷却并检查夹持阻力后再运行一次" if "电机过温" in after["error_flags"] else ""
    raise RuntimeError("%s单次恢复后仍异常：status=%s，标志=%s，开度=%.3fmm%s；不循环重试" %
                       (monitor.label,after["status_hex"],flags,after["opening_mm"],advice))


def recover_gripper(side, clock=time.monotonic):
    if side not in ("left","right"):
        raise ValueError("夹爪只允许 left/right")
    from front_mode import operator_guard
    from front_reset import prepare_can_tx
    operator_guard(side)
    bus="can_"+side
    prepare_can_tx(bus)
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        ids=sorted(CONTROL_IDS|FEEDBACK_IDS)
        sock.setsockopt(socket.SOL_CAN_RAW,socket.CAN_RAW_FILTER,
                        b"".join(struct.pack("=II",canid,0x7ff) for canid in ids))
        sock.setsockopt(socket.SOL_CAN_RAW,socket.CAN_RAW_RECV_OWN_MSGS,0)
        sock.bind((bus,));sock.settimeout(.02)
        label="左前夹爪" if side=="left" else "右前夹爪"
        return recover_one_gripper(sock,Monitor(sock,clock=clock,label=label),clock)


def clear_gripper_latches(sockets, monitors, clock=time.monotonic):
    poll_all(monitors, allow_gripper_latch=True)
    targets={}
    for side,monitor in monitors.items():
        data=monitor.fresh(0x2a8)
        if not data[6]&0x30:continue
        if not data[6]&0x40:raise RuntimeError(monitor.label+"当前失能，本流程不恢复失能夹爪")
        opening=struct.unpack(">i",data[:4])[0]
        if not -1000<=opening<=81000:raise RuntimeError(monitor.label+"当前开度无法构造原地保持指令")
        targets[side]=max(0,min(80000,opening))
    if not targets:
        poll_all(monitors)
        return
    sent={}
    for side,opening in targets.items():
        poll_all(monitors,allow_gripper_latch=True)
        sent[side]=monitors[side].clock()
        sockets[side].sendall(command_frame(opening,clear_error=True))
        print("%s：保持 %.3fmm，单次清除夹爪错误（不失能/设零点）。" % (monitors[side].label,opening/1000),flush=True)
    deadline=clock()+2.0;normal_since=None
    while clock()<deadline:
        poll_all(monitors,allow_gripper_latch=True)
        normal=True
        for side,monitor in monitors.items():
            data=monitor.fresh(0x2a8,sent.get(side))
            normal=normal and data is not None and not data[6]&0x3f and bool(data[6]&0x40)
        if normal:
            if normal_since is None:normal_since=clock()
            if clock()-normal_since>=.2:
                poll_all(monitors)
                print("夹爪状态已恢复正常，继续张开/闭合。",flush=True)
                return
        else:normal_since=None
    reports={side:gripper_report(monitor.fresh(0x2a8)) for side,monitor in monitors.items()}
    if all(report["enabled"] and not (int(report["status_hex"],16)&0x0f)
           for report in reports.values()):
        print("夹爪保留0x10/0x20提示位；关节健康且夹爪已使能，继续并用实测开度验证每一步："+str(reports),flush=True)
        return
    raise RuntimeError("单次清状态后夹爪仍未恢复，不继续张开/闭合、不重试："+str(reports))


def run_cycle(sockets, monitors, clock=time.monotonic):
    if set(sockets) != {"left", "right"} or set(monitors) != set(sockets):
        raise ValueError("必须同时提供左右前臂夹爪")
    # Collect and validate both buses before the first command on either bus.
    deadline = clock() + 0.6
    while clock() < deadline:
        poll_all(monitors, check=False)
    for monitor in monitors.values():
        monitor.anchor = monitor.check(allow_gripper_latch=True)
    clear_gripper_latches(sockets,monitors,clock)

    def command_both(target):
        poll_all(monitors)
        sent = {}
        for side in ("left", "right"):
            sent[side] = monitors[side].clock()
            sockets[side].sendall(command_frame(target))
        return sent

    def wait_both(target, sent):
        deadline = clock() + 5.0
        arrived = {}
        while clock() < deadline:
            poll_all(monitors)
            for side, monitor in monitors.items():
                data = monitor.fresh(0x2a8, sent[side])
                if data is not None and data[6] & 0x40:
                    opening = struct.unpack(">i", data[:4])[0]
                    arrived[side] = opening if abs(opening-target) <= 1000 else None
            if all(arrived.get(side) is not None for side in monitors):
                return {side: value/1000 for side, value in arrived.items()}
        measured = {side: struct.unpack(">i", monitor.fresh(0x2a8)[:4])[0]/1000
                    for side, monitor in monitors.items()}
        raise RuntimeError("未确认左右夹爪均到达 %.1f mm，实测 %s mm（可能受夹持物/阻挡影响）；不重试" % (target/1000, measured))

    opened = wait_both(OPEN_UM, command_both(OPEN_UM))
    print("左右前夹爪已张开：实测 %s mm；停半秒。" % opened, flush=True)
    deadline = clock() + 0.5
    while clock() < deadline:
        poll_all(monitors)
    closed = wait_both(0, command_both(0))
    print("左右前夹爪已闭合：实测 %s mm；未发送机械臂关节目标。" % closed, flush=True)


def main(assume_yes=False):
    if not assume_yes and not sys.stdin.isatty():
        raise RuntimeError("请在现场交互终端运行")
    if not assume_yes:
        input("若夹爪报告传感器/驱动器错误，将先保持当前开度并单次清状态；正常后左右张开70mm，停半秒再闭合。确认臂静止、夹持物已处理、手已离开夹爪后按 Enter，取消按 Ctrl-C：")
    from front_mode import operator_guard
    for side in ("left", "right"):
        operator_guard(side)
    with ExitStack() as stack:
        sockets, monitors = {}, {}
        for side in ("left", "right"):
            sock = stack.enter_context(socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW))
            ids = sorted(CONTROL_IDS | FEEDBACK_IDS)
            sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                            b"".join(struct.pack("=II", canid, 0x7ff) for canid in ids))
            sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_RECV_OWN_MSGS, 0)
            sock.bind(("can_" + side,))
            sock.settimeout(0.02)
            sockets[side], monitors[side] = sock, Monitor(sock,label="左前夹爪" if side=="left" else "右前夹爪")
        run_cycle(sockets, monitors)
    return 0


def cli(assume_yes=False):
    try:
        return main(assume_yes=assume_yes)
    except (KeyboardInterrupt, EOFError):
        print("已取消；不自动补发闭合/归位指令。", flush=True)
        return 130
    except Exception as exc:
        print("夹爪复位已停止：" + str(exc), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())

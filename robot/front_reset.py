#!/usr/bin/env python3
"""One-shot front-controller resume/rearm at measured pose; operator support required."""
import argparse,errno,json,socket,struct,subprocess,time,math
from pathlib import Path
from front_mode import feedback,check_ready,operator_guard,rospy
LIMITS=[(-150000,150000),(0,180000),(-170000,0),(-100000,100000),(-70000,70000),(-120000,120000)]
CAN_RESET_HELPER=Path(__file__).resolve().parents[1]/"scripts"/"can_recover_one.sh"
RECOVERABLE_BUSES={"can_left","can_right","can_mid"}

def measured_angles(bus):
    latest={}
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        sock.setsockopt(socket.SOL_CAN_RAW,socket.CAN_RAW_FILTER,b"".join(struct.pack("=II",i,0x7ff) for i in [0x2a5,0x2a6,0x2a7]))
        sock.bind((bus,));sock.settimeout(.15)
        end=time.monotonic()+.5
        while time.monotonic()<end:
            try:f=sock.recv(16)
            except socket.timeout:continue
            cid,n,data=struct.unpack("=IB3x8s",f);cid &=0x1fffffff
            if n==8:latest[cid]=list(struct.unpack(">ii",data))
    if len(latest)!=3:raise RuntimeError("Missing measured joint angles")
    raw=sum((latest[i] for i in [0x2a5,0x2a6,0x2a7]),[])
    return raw

# Bounded correction uses the existing RLT command-step magnitude, not a wider angle range.
MAX_HOLD_CORRECTION_RAD = 0.01

def project_hold(raw):
    if len(raw)!=6:
        raise RuntimeError("保持目标需要6个实测关节角")
    hold=[min(max(v,lo),hi) for v,(lo,hi) in zip(raw,LIMITS)]
    cap=math.degrees(MAX_HOLD_CORRECTION_RAD)*1000.0
    differences = [
        "关节{} 实测{:.3f}°，名义范围[{:.1f}, {:.1f}]°".format(i+1,v/1000.0,lo/1000.0,hi/1000.0)
        for i,(v,h,(lo,hi)) in enumerate(zip(raw,hold,LIMITS)) if abs(v-h)>cap
    ]
    if differences:
        raise RuntimeError("保持目标边界修正超过0.01rad，拒绝恢复："+"; ".join(differences))
    for i,(v,h) in enumerate(zip(raw,hold)):
        if v!=h:
            print("关节{}：实测{:.3f}°，恢复保持目标{:.3f}°；会做不超过0.01rad的小范围边界修正。".format(i+1,v/1000.0,h/1000.0),flush=True)
    return hold

def hold_angles(bus):
    return project_hold(measured_angles(bus))


def send(sock,cid,data):
    if len(data)!=8:raise ValueError("CAN payload must be 8 bytes")
    sock.send(struct.pack("=IB3x8s",cid,8,data))

def send_hold(sock,hold):
    for i,cid in enumerate([0x155,0x156,0x157]):
        send(sock,cid,struct.pack(">ii",*hold[2*i:2*i+2]))


def probe_can_tx(bus):
    """Send one inert standard frame before any recovery control write."""
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        sock.bind((bus,))
        sock.send(struct.pack("=IB3x8s",0x7ff,0,bytes(8)))


def reset_can_link(bus,sudo_password=None):
    if bus not in RECOVERABLE_BUSES:
        raise RuntimeError("拒绝复位未登记的 CAN 接口："+str(bus))
    command=[str(CAN_RESET_HELPER),bus]
    kwargs={"check":False}
    if sudo_password is not None:
        command.insert(1,"--sudo-stdin")
        kwargs.update(input=sudo_password+"\n",text=True)
    result=subprocess.run(command,**kwargs)
    if result.returncode:
        raise RuntimeError("CAN 单接口自动复位失败；接口="+bus)


def prepare_can_tx(bus,sudo_password=None,*,resetter=reset_can_link,prober=probe_can_tx):
    """Heal a pre-existing full TX queue once, before disabling any motor."""
    try:
        prober(bus)
        return False
    except OSError as exc:
        if exc.errno != errno.ENOBUFS:
            raise
    print("检测到 {} 发送队列已满；尚未发送恢复控制帧，正在单独复位该接口。".format(bus),flush=True)
    resetter(bus,sudo_password)
    time.sleep(.25)
    prober(bus)
    print(bus+" 发送队列已恢复；继续原 Recover 序列。",flush=True)
    return True

def healthy(state,require_enabled=True):
    if state["arm_status"] or state["err_code"] or any(int(x["fault_bits"],16) for x in state["motors"]):
        raise RuntimeError("Protection/error feedback: stop without clearing it")
    if require_enabled and any(not x["enabled"] for x in state["motors"]):
        raise RuntimeError("Motor did not enable; stop without automatic retries")

def recovery_ready(state):
    healthy(state,require_enabled=False)
    if state["control_frames"]:
        raise RuntimeError("存在其他控制指令；先停止归位、rollout和示教接管")
    if state["ctrl_mode"] not in (0,1,2):
        raise RuntimeError("不支持当前控制模式，停止恢复")

def reset_at_hold(bus,hold):
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        sock.bind((bus,))
        # Support must be confirmed by the local operator before this point.
        send(sock,0x471,bytes([7,1,0,0,0,0,0,0]))  # scoped disable, no gripper command
        send(sock,0x150,bytes(8))                  # close physical drag teaching
        send(sock,0x150,bytes([2,0,0,0,0,0,0,0]))  # controller resume, no fault clear
        time.sleep(.35)
        healthy(feedback(bus,.3),require_enabled=False)
        send(sock,0x151,bytes([1,1,5,0,0,0,0,0]))
        send_hold(sock,hold)                       # replace old target before enable
        time.sleep(.6)
        healthy(feedback(bus,.3),require_enabled=False)
        send_hold(sock,hold)
        send(sock,0x471,bytes([7,2,0,0,0,0,0,0]))  # bounded single rearm
        time.sleep(.4)
        healthy(feedback(bus,.3))
        send(sock,0x151,bytes([1,1,5,0,0,0,0,0]))
        send_hold(sock,hold)
    after=feedback(bus,1.0);healthy(after)
    if after["ctrl_mode"]!=1:raise RuntimeError("未恢复CAN控制模式，停止重复尝试")
    measured=measured_angles(bus)
    if max(abs(a-b) for a,b in zip(measured,hold))>1700:
        raise RuntimeError("保持位姿偏差超过1.7度，停止重复尝试")
    return after

def wait_operator(message):
    try:
        answer=input(message)
        if answer.strip():
            print("已停止；机械臂保持失能，本次不会重新使能。")
            return False
        return True
    except (EOFError,KeyboardInterrupt):
        print("\n已停止；机械臂保持失能，本次不会重新使能。")
        return False

def command_hold(raw):
    if len(raw)!=6:
        raise RuntimeError("保持目标需要6个实测关节角")
    # Match SDK command-range clipping, without a pose gate on disabling.
    return [min(max(v,lo),hi) for v,(lo,hi) in zip(raw,LIMITS)]

def recover_controller(bus, guard, label, sudo_password=None):
    guard()
    state=feedback(bus)
    if state["control_frames"]:
        raise RuntimeError("存在其他控制指令，先停止归位/rollout/示教接管")
    # ENOBUFS is safe to heal only here: no disable/mode/hold frame has been
    # sent yet. Never replay a partly completed recovery sequence.
    prepare_can_tx(bus,sudo_password)
    # Disable stage has no angle read, target computation or position-limit gate.
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        sock.bind((bus,))
        send(sock,0x471,bytes([7,1,0,0,0,0,0,0]))
        send(sock,0x150,bytes(8))
        send(sock,0x471,bytes([7,1,0,0,0,0,0,0]))
    state=feedback(bus,.6)
    if any(x["enabled"] for x in state["motors"]):
        raise RuntimeError("电机未完全失能；本次不继续使能，请查看现场状态")
    print(label+"六个关节已失能，夹爪未操作。",flush=True)
    guard()
    state=feedback(bus,.3);healthy(state,require_enabled=False)
    if state["control_frames"]:
        raise RuntimeError("失能后出现其他控制指令，本次不重新使能")
    raw=measured_angles(bus)
    hold=command_hold(raw)
    corrections=[(i+1,v,h) for i,(v,h) in enumerate(zip(raw,hold)) if v!=h]
    if corrections:
        for joint,v,h in corrections:
            print("关节{}：实测{:.3f}°，合法保持目标{:.3f}°。".format(joint,v/1000.0,h/1000.0),flush=True)
    guard()
    state=feedback(bus,.3);healthy(state,require_enabled=False)
    if state["control_frames"]:
        raise RuntimeError("重新使能前出现其他控制指令，保持失能")
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        sock.bind((bus,))
        send(sock,0x150,bytes([2,0,0,0,0,0,0,0]))
        time.sleep(.35)
        healthy(feedback(bus,.3),require_enabled=False)
        send(sock,0x151,bytes([1,1,5,0,0,0,0,0]))
        send_hold(sock,hold)
        time.sleep(.6)
        healthy(feedback(bus,.3),require_enabled=False)
        send_hold(sock,hold)
        send(sock,0x471,bytes([7,2,0,0,0,0,0,0]))
        time.sleep(.4)
        healthy(feedback(bus,.3))
        send(sock,0x151,bytes([1,1,5,0,0,0,0,0]))
        send_hold(sock,hold)
    after=feedback(bus,1.0);healthy(after)
    if after["ctrl_mode"]!=1:
        raise RuntimeError("尚未恢复CAN控制模式，停止重复尝试")
    # Recovery checks controller readiness; arrival belongs to home.sh.
    return after

def recover_front(side,sudo_password=None):
    return recover_controller("can_"+side, lambda: operator_guard(side, allow_coordinator_fault=True), "所选前臂",sudo_password)


def mid_recovery_guard():
    import rosgraph
    from mid_home import check_publishers, check_session
    if not rospy.core.is_initialized():
        rospy.init_node("platform_mid_recovery", anonymous=True, disable_signals=True)
    master = rosgraph.Master(rospy.get_name())
    graph = master.getSystemState()
    check_publishers(master)
    phase = check_session()
    # An ended Session may keep its services and model alive. Services alone
    # are not evidence of active motion; require a verified idle state instead.
    services = dict(graph[2])
    if phase is None and ("/task2/policy/set_paused" in services or "/task2/policy/arm" in services):
        raise RuntimeError("RLT 策略服务仍在但 Session 状态不可用；恢复网页后确认 Session 已结束，不需要卸载模型")
    pubs, subs, _ = graph
    owners = dict(pubs).get("/puppet/arm_status_mid", [])
    if len(owners) != 1:
        raise RuntimeError("无法确定唯一中臂驱动；检查机械臂节点")
    owner = owners[0]
    if rospy.get_param(owner+"/can_port", None) != "can_mid":
        raise RuntimeError("中臂 CAN 配置不匹配；不执行恢复")
    if rospy.get_param(owner+"/mode", None) != 1:
        raise RuntimeError("中臂节点不是从臂模式；不执行恢复")
    if owner not in dict(subs).get("/master/joint_mid", []):
        raise RuntimeError("中臂驱动未订阅控制话题；不执行恢复")
    # The legacy driver also accepts Cartesian commands on pos_cmd.
    command_topics = [topic for topic, nodes in subs
                      if owner in nodes and topic.endswith("/pos_cmd")]
    if any(dict(pubs).get(topic) for topic in command_topics):
        raise RuntimeError("存在中臂末端位置控制发布者；先停止其他控制")


def recover_mid(sudo_password=None):
    import fcntl, os
    from mid_home import ROOT
    directory = os.path.join(ROOT, "runtime", "arms")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "home-mid.lock"), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("中臂归位/恢复命令正在运行；本次不执行恢复")
        return recover_controller("can_mid", mid_recovery_guard, "中臂",sudo_password)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("side",choices=["left","right"])
    ap.add_argument("--apply",action="store_true")
    ap.add_argument("--supported",action="store_true",help="现场已可靠支撑机械臂，允许恢复期间短暂失能")
    args=ap.parse_args()
    if args.apply and not args.supported:ap.error("恢复可能短暂失能，先可靠支撑机械臂，再加 --supported")
    bus="can_"+args.side
    state=feedback(bus)
    print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
    if not args.apply:return 0
    operator_guard(args.side)
    state=feedback(bus);recovery_ready(state)
    hold=hold_angles(bus)
    # Final quiet-bus/healthy check immediately before the one-shot maintenance.
    state=feedback(bus,.3);recovery_ready(state)
    print("现场恢复所选前臂；可能短暂失能；保持当前测量位姿，不归位、不操作夹爪。",flush=True)
    after=reset_at_hold(bus,hold)
    print(json.dumps(after,ensure_ascii=False,indent=2))
    print("控制器恢复序列完成，保持位姿反馈通过；尚未证明归位或示教跟随恢复，需现场验收。")
    return 0

if __name__=="__main__":
    try:raise SystemExit(main())
    except (RuntimeError,OSError,rospy.ROSException) as e:print("恢复未完成："+str(e));raise SystemExit(1)

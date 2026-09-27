#!/usr/bin/env python3
"""Inspect/close front-arm physical teach mode; no enable, fault-clear, target or homing."""
import argparse,json,socket,struct,time
import rospy,rosgraph
from std_msgs.msg import Bool,String

def feedback(bus, seconds=1.0):
    ids=[0x2a1,*range(0x261,0x267),0x150,0x151,0x155,0x156,0x157]
    latest={};counts={}
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        sock.setsockopt(socket.SOL_CAN_RAW,socket.CAN_RAW_FILTER,b"".join(struct.pack("=II",i,0x7ff) for i in ids))
        sock.bind((bus,));sock.settimeout(.15)
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            try:raw=sock.recv(16)
            except socket.timeout:continue
            canid,n,data=struct.unpack("=IB3x8s",raw);canid &= 0x1fffffff
            latest[canid]=data[:n];counts[canid]=counts.get(canid,0)+1
    if 0x2a1 not in latest or len(latest[0x2a1])!=8:
        raise RuntimeError("Missing current arm feedback")
    for i in range(0x261,0x267):
        if i not in latest or len(latest[i])!=8:raise RuntimeError("Missing motor feedback "+hex(i))
    status=latest[0x2a1]
    motors=[{"joint":i-0x260,"enabled":bool(latest[i][5]&0x40),"fault_bits":hex(latest[i][5]&0xbf)} for i in range(0x261,0x267)]
    return {"bus":bus,"ctrl_mode":status[0],"arm_status":status[1],"teach_status":status[3],
            "err_code":int.from_bytes(status[6:8],"big"),"motors":motors,
            "control_frames":sum(counts.get(i,0) for i in [0x150,0x151,0x155,0x156,0x157])}

def check_ready(state):
    if state["arm_status"] or state["err_code"]:
        raise RuntimeError("Arm fault persists; this command does not clear protection")
    for m in state["motors"]:
        if int(m["fault_bits"],16) or not m["enabled"]:
            raise RuntimeError("Motor is disabled or protected; this command refuses enable/fault-clear")
    if state["control_frames"]:
        raise RuntimeError("Control traffic detected; stop commands/rollout before recovery")

def operator_guard(side, allow_coordinator_fault=False):
    if not rospy.core.is_initialized():
        rospy.init_node("platform_front_mode_recovery",anonymous=True,disable_signals=True)
    graph=rosgraph.Master(rospy.get_name()).getSystemState()
    # Onsite operator confirms policy inactivity; service presence does not block recovery.
    for rear in ["left","right"]:
        msg=rospy.wait_for_message("/task2/teach/rear_"+rear+"/teach_active",Bool,timeout=2)
        if msg.data:raise RuntimeError("Rear teach takeover active; release both rear teach buttons first")
    fault=rospy.wait_for_message("/task2/teach_handover/fault",String,timeout=2)
    if fault.data and not allow_coordinator_fault:raise RuntimeError("Task2 coordinator fault: "+fault.data)
    pubs,subs,_=graph
    node_prefix="/piper_"+side+"_"
    owner=[x for x in dict(pubs).get("/puppet/arm_status_"+side,[]) if x.startswith(node_prefix)]
    if len(owner)!=1:raise RuntimeError("Cannot identify the original front CAN owner")
    if rospy.get_param(owner[0]+"/can_port",None)!="can_"+side:
        raise RuntimeError("Unexpected CAN assignment")

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("side",choices=["left","right"])
    ap.add_argument("--apply",action="store_true",help="现场确认无动作且工作区安全后，关闭所选前臂的本体示教模式")
    args=ap.parse_args()
    state=feedback("can_"+args.side)
    print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
    if not args.apply:return 0
    operator_guard(args.side)
    state=feedback("can_"+args.side);check_ready(state)
    if state["ctrl_mode"]!=2:
        print("当前已不在本体示教模式；未发送任何CAN指令。");return 0
    # SDK MotionCtrl_1(0,0,0): explicitly turn physical drag teaching off.
    # One scoped frame only; no EnableArm, JointConfig, mode-switch or JointCtrl.
    with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
        sock.bind(("can_"+args.side,))
        sock.send(struct.pack("=IB3x8s",0x150,8,bytes(8)))
    after=feedback("can_"+args.side,2.0)
    print(json.dumps(after,ensure_ascii=False,indent=2),flush=True)
    if after["ctrl_mode"]==2:raise RuntimeError("Teach mode did not close; stop here without further retries")
    if after["arm_status"] or after["err_code"] or any(not m["enabled"] or int(m["fault_bits"],16) for m in after["motors"]):
        raise RuntimeError("Unhealthy feedback after mode exit; stop here without enabling or homing")
    print("本体示教模式已关闭；未归位、未自动恢复策略。下一步由现场操作员决定。")
    return 0

if __name__=="__main__":
    try:raise SystemExit(main())
    except (RuntimeError,rospy.ROSException,OSError) as e:
        print("恢复未完成："+str(e));raise SystemExit(1)

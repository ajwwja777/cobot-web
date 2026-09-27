#!/usr/bin/env python3
"""Small measured-state recording and supervised playback through the existing coordinator."""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import uuid

import teleop_core as core

ROOT=Path(__file__).resolve().parents[1]
DATA=Path("/media/agilex/Getea1/jiaan/data/cobot-platform/teleop")


@contextmanager
def operation_lock():
    path=ROOT/"runtime/teleop.lock"
    with path.open("a+") as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError("已有录制/回放命令运行，请先结束它")
        yield


class State:
    def __init__(self,replay=False):
        import rospy
        from sensor_msgs.msg import JointState
        self.ros=rospy;self.lock=threading.RLock();self.cache={};self.subs=[]
        for side in core.SIDES:
            topic="/puppet/joint_"+side
            self.subs.append(rospy.Subscriber(topic,JointState,self.callback("front_"+side),queue_size=1))
        if replay:
            from std_msgs.msg import Bool,String
            from piper_msgs.msg import PiperStatusMsg
            for side in core.SIDES:
                topics=[("teach_"+side,"/task2/teach/rear_"+side+"/teach_active",Bool),
                        ("status_"+side,"/puppet/arm_status_"+side,PiperStatusMsg),
                        ("fault_"+side,"/task2/teach/rear_"+side+"/fault",String)]
                for key,topic,kind in topics:
                    self.subs.append(rospy.Subscriber(topic,kind,self.callback(key),queue_size=1))
            self.subs.append(rospy.Subscriber("/task2/teach_handover/fault",String,self.callback("fault"),queue_size=1))

    def callback(self,key):
        def accept(msg):
            if key.startswith("front_"):
                try:
                    if list(msg.name)!=core.JOINT_NAMES:return
                    core.position(list(msg.position))
                except ValueError:return
            with self.lock:self.cache[key]=(time.monotonic(),msg)
        return accept

    def get(self,key,max_age=None):
        entry=self.cache.get(key)
        if entry is None or (max_age is not None and time.monotonic()-entry[0]>max_age):
            raise RuntimeError("反馈缺失/过期："+key)
        return entry[1]

    def joint(self,key,strict=False):
        msg=self.get(key,.2 if strict else None)
        if list(msg.name)!=core.JOINT_NAMES:raise RuntimeError("关节顺序不匹配："+key)
        stamp=msg.header.stamp.to_sec()
        if strict and stamp and not -.05<=time.time()-stamp<=.2:raise RuntimeError("机器人源时间戳过期："+key)
        def optional(values):
            return list(values) if values and all(math.isfinite(v) for v in values) else []
        return {"position":core.position(list(msg.position)),"velocity":optional(msg.velocity),
                "effort":optional(msg.effort),"stamp":stamp,
                "feedback_age_sec":max(0,time.monotonic()-self.cache[key][0])}

    def sample(self,replay=False):
        with self.lock:
            if self.ros.is_shutdown():raise RuntimeError("ROS已退出")
            front={side:self.joint("front_"+side,strict=replay) for side in core.SIDES}
            if not replay:
                return {"front":front}  # Recording has no rear, teach or hardware-status dependency.
            for key in ["fault","fault_left","fault_right"]:
                error=self.get(key).data
                if error:raise RuntimeError("机械臂节点故障："+error)
            for side in core.SIDES:
                if self.get("teach_"+side,.5).data:
                    raise RuntimeError("示教按钮已接管，停止回放；不会自动继续")
                status=self.get("status_"+side,.2)
                if status.ctrl_mode!=1 or status.arm_status or status.teach_status or status.err_code:
                    raise RuntimeError("前臂不是健康CAN保持状态："+side)
            return {"front":front}

    def wait_ready(self,replay=False):
        deadline=time.monotonic()+5
        while True:
            try:return self.sample(replay)
            except RuntimeError:
                if time.monotonic()>=deadline:raise
                time.sleep(.02)

    def close(self):
        for sub in self.subs:sub.unregister()


def graph_check():
    import rospy,rosgraph
    pubs,subs,services=rosgraph.Master(rospy.get_name()).getSystemState()
    services=dict(services);pubs=dict(pubs);subs=dict(subs)
    if any(s in services for s in ["/task2/policy/arm","/task2/policy/set_paused"]):
        raise RuntimeError("请先结束RLT Session并rlt_down，再运行回放")
    owners=services.get("/task2/teach_handover/home_front",[])
    if len(owners)!=1:raise RuntimeError("未找到唯一前臂协调器，请先启动机械臂节点")
    for side in core.SIDES:
        if pubs.get("/master/joint_"+side,[])!=owners:
            raise RuntimeError("前臂控制通道存在其他发布者")
        topic="/task2/policy/joint_"+side
        if owners[0] not in subs.get(topic,[]):raise RuntimeError("协调器没有接入回放控制通道")
        others=[name for name in pubs.get(topic,[]) if name!=rospy.get_name()]
        if others:raise RuntimeError("有其他策略/回放占用控制通道："+str(others))


class Health:
    """Passive CAN feedback only; no CAN write or SDK connection is created."""
    def __init__(self):
        self.stop=threading.Event();self.latest={};self.error=None;self.threads=[]

    def __enter__(self):
        from gripper_cycle import Monitor,FEEDBACK_IDS
        def observe(side):
            try:
                with socket.socket(socket.AF_CAN,socket.SOCK_RAW,socket.CAN_RAW) as sock:
                    sock.setsockopt(socket.SOL_CAN_RAW,socket.CAN_RAW_FILTER,b"".join(struct.pack("=II",i,0x7ff) for i in sorted(FEEDBACK_IDS)))
                    sock.bind(("can_"+side,));sock.settimeout(.02)
                    monitor=Monitor(sock,label="左前夹爪" if side=="left" else "右前夹爪");warmup=time.monotonic()+.6
                    while not self.stop.is_set():
                        monitor.poll()
                        if time.monotonic()<warmup:continue
                        monitor.check();self.latest[side]=time.monotonic()
            except Exception as exc:self.error=str(exc)
        for side in core.SIDES:
            thread=threading.Thread(target=observe,args=(side,),daemon=True);thread.start();self.threads.append(thread)
        deadline=time.monotonic()+3
        try:
            while len(self.latest)<2:
                if self.error:raise RuntimeError(self.error)
                if time.monotonic()>deadline:raise RuntimeError("双前臂CAN健康反馈未就绪")
                time.sleep(.02)
        except Exception:
            self.__exit__(None,None,None);raise
        return self

    def check(self):
        if self.error:raise RuntimeError("CAN健康检查失败："+self.error)
        if any(time.monotonic()-self.latest.get(s,0)>.2 for s in core.SIDES):
            raise RuntimeError("双前臂CAN健康检查已过期")

    def __exit__(self,*args):
        self.stop.set()
        for thread in self.threads:thread.join(timeout=.3)


def remove_recordings():
    # Fixed recording root only: do not follow symlinks or recursively delete.
    if DATA.resolve()!=DATA:
        raise RuntimeError("录制目录含符号链接，拒绝删除："+str(DATA))
    if not DATA.exists():
        print("没有遥操录制文件。",flush=True)
        return 0
    if not DATA.is_dir():raise RuntimeError("录制路径不是目录")
    paths=sorted(p for p in DATA.iterdir()
                 if (p.name.startswith("teleop-") and
                     (p.name.endswith(".jsonl") or p.name.endswith(".jsonl.incomplete")))
                 or p.name=="latest.txt"
                 or (p.name.startswith("latest-") and p.name.endswith(".tmp")))
    for p in paths:
        if p.is_symlink() or not p.is_file() or p.resolve().parent!=DATA:
            raise RuntimeError("录制条目不是目录内的普通文件，拒绝删除："+str(p))
    count=sum(p.name.startswith("teleop-") for p in paths)
    for p in paths:p.unlink()
    print("已删除%d个遥操录制文件，并清理最新记录指针；目录：%s" % (count,DATA),flush=True)
    return 0


def latest_file():
    pointer=DATA/"latest.txt"
    if not pointer.is_file():raise RuntimeError("还没有完整的遥操记录")
    name=pointer.read_text().strip()
    path=(DATA/name).resolve()
    if path.parent!=DATA.resolve() or path.suffix!=".jsonl":raise RuntimeError("最新记录指针无效")
    return path


def record(state):
    if not sys.stdin.isatty():raise RuntimeError("请在交互终端运行录制")
    state.wait_ready()
    input("按 Enter 开始记录双前臂状态；再按 Enter 结束保存：")
    state.sample()
    DATA.mkdir(parents=True,exist_ok=True)
    name=datetime.now(timezone.utc).strftime("teleop-%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8]+".jsonl"
    final=DATA/name;pending=DATA/(name+".incomplete")
    stopped=threading.Event()
    def wait_enter():
        try:input()
        except EOFError:pass
        stopped.set()
    threading.Thread(target=wait_enter,daemon=True).start()
    frames=0;error=None;started=time.monotonic()
    print("正在记录50Hz robot state，按 Enter 保存。",flush=True)
    with pending.open("x") as handle:
        header={"type":"header","schema":core.SCHEMA,"source":"front_measured","joint_names":core.JOINT_NAMES,
                "units":core.UNITS,"rate_hz":core.RATE,"created_utc":datetime.now(timezone.utc).isoformat()}
        handle.write(json.dumps(header)+"\n")
        deadline=started
        try:
            while not stopped.is_set():
                sample={"front":state.sample()["front"],"type":"sample","t":time.monotonic()-started}
                handle.write(json.dumps(sample,separators=(",",":"))+"\n");frames+=1
                if frames%50==0:handle.flush()
                deadline+=1/core.RATE
                if deadline<time.monotonic():deadline=time.monotonic()+1/core.RATE
                stopped.wait(max(0,deadline-time.monotonic()))
        except KeyboardInterrupt:pass
        except Exception as exc:error=str(exc)
        valid=error is None and frames>=1
        footer={"type":"footer","complete":error is None,"valid_for_replay":valid,"frames":frames,
                "duration_sec":time.monotonic()-started,"error":error}
        handle.write(json.dumps(footer)+"\n");handle.flush();os.fsync(handle.fileno())
    os.replace(pending,final)
    if valid:
        pointer=DATA/("latest-"+uuid.uuid4().hex+".tmp");pointer.write_text(name+"\n");os.replace(pointer,DATA/"latest.txt")
    print("已保存 %s（%d帧）。" % (final,frames),flush=True)
    if not valid:raise RuntimeError(error or "没有收到前臂样本，未替换最新记录")
    return 0


def replay(state,path,frames):
    import rospy
    from sensor_msgs.msg import JointState
    from front_mode import operator_guard, feedback, check_ready
    if not sys.stdin.isatty():raise RuntimeError("请在现场交互终端运行回放")
    times,poses=core.retime(frames)
    graph_check();state.wait_ready(replay=True)
    print("记录：%s；轨迹 %.1f 秒（必要时限速放慢）。" % (path,times[-1]),flush=True)
    input("将先缓慢回到录制起点，再回放双前臂和夹爪；确认物体/路径安全、释放示教按钮后按 Enter，取消按 Ctrl-C：")
    for side in core.SIDES:
        operator_guard(side)
        check_ready(feedback("can_"+side,seconds=.3))
    graph_check()
    with ExitStack() as stack:
        health=stack.enter_context(Health())
        current=state.sample(replay=True)
        last={s:core.position(current["front"][s]["position"]) for s in core.SIDES}
        pubs={s:rospy.Publisher("/task2/policy/joint_"+s,JointState,queue_size=1,tcp_nodelay=True) for s in core.SIDES}
        for pub in pubs.values():stack.callback(pub.unregister)
        deadline=time.monotonic()+3
        while not all(pub.get_num_connections()>0 for pub in pubs.values()):
            state.sample(replay=True);health.check()
            if time.monotonic()>deadline:raise RuntimeError("回放控制通道未连接")
            time.sleep(.02)
        tick=time.monotonic();graph_due=0
        def emit(pair):
            nonlocal last,tick,graph_due
            if time.monotonic()-tick>.05:raise RuntimeError("回放调度延迟过大；停止，不跳帧追赶")
            if time.monotonic()>=graph_due:
                graph_check();graph_due=time.monotonic()+1
            sample=state.sample(replay=True);health.check()
            if time.monotonic()-tick>.05:raise RuntimeError("回放调度延迟过大；停止")
            for s in core.SIDES:
                if max(abs(a-b) for a,b in zip(sample["front"][s]["position"][:6],last[s][:6]))>.2:
                    raise RuntimeError("前臂跟踪偏差过大："+s)
                for i,(a,b) in enumerate(zip(last[s],pair[s])):
                    limit=(core.GRIPPER_SPEED if i==6 else core.JOINT_SPEED)/core.RATE
                    if abs(b-a)>limit+1e-8:raise RuntimeError("回放指令步长越限；停止")
            stamp=rospy.Time.now()
            for s in core.SIDES:
                msg=JointState();msg.header.stamp=stamp;msg.name=core.JOINT_NAMES;msg.position=pair[s];pubs[s].publish(msg)
            last={s:list(pair[s]) for s in core.SIDES}
            tick+=1/core.RATE;time.sleep(max(0,tick-time.monotonic()))
        def settle(target):
            until=time.monotonic()+5
            while time.monotonic()<until:
                emit(target);sample=state.sample(replay=True)
                if all(max(abs(a-b) for a,b in zip(sample["front"][s]["position"][:6],target[s][:6]))<=.03
                       and abs(sample["front"][s]["position"][6]-target[s][6])<=.003 for s in core.SIDES):return
            raise RuntimeError("未确认双前臂/夹爪到达目标；停止")
        print("缓慢回到录制起点…",flush=True)
        for pair in core.start_path(last,poses[0]):emit(pair)
        settle(poses[0]);print("开始轨迹回放。",flush=True)
        for i in range(math.ceil(times[-1]*core.RATE)+1):emit(core.interpolate(times,poses,i/core.RATE))
        settle(poses[-1])
    print("回放完成，前双臂保持终点；未归位/失能后臂。",flush=True)
    return 0


def main(argv=None):
    ap=argparse.ArgumentParser(description="双前臂robot state录制/回放，Enter开始/结束")
    ap.add_argument("action",choices=["record","replay","info","status","rf"])
    ap.add_argument("file",nargs="?",help="回放/检查指定jsonl；默认最新完整记录")
    args=ap.parse_args(argv)
    if args.action=="rf":
        if args.file:ap.error("rf不接受路径参数；只清空本平台遥操录制目录")
        with operation_lock():return remove_recordings()
    if args.action=="status":
        if args.file:ap.error("status不需要文件参数")
        from gripper_cycle import passive_status
        for side in core.SIDES:
            print(json.dumps(passive_status("can_"+side),ensure_ascii=False,indent=2),flush=True)
        return 0
    if args.action=="record" and args.file:ap.error("record不需要文件参数")
    frames=None;path=None
    if args.action!="record":
        path=Path(args.file).resolve() if args.file else latest_file();frames=core.load_trace(path)
        times,_=core.retime(frames)
        print("%s：%d帧，录制轨迹%.1fs，限速回放%.1fs。" % (path,len(frames),frames[-1]["t"]-frames[0]["t"],times[-1]),flush=True)
        if args.action=="info":return 0
    import rospy
    rospy.init_node("platform_teleop_state",anonymous=True,disable_signals=True)
    with operation_lock():
        state=State(replay=args.action=="replay")
        try:return record(state) if args.action=="record" else replay(state,path,frames)
        finally:state.close()


if __name__=="__main__":
    try:raise SystemExit(main())
    except (KeyboardInterrupt,EOFError):
        print("已停止；不自动归位或恢复策略。",flush=True);raise SystemExit(130)
    except Exception as exc:
        print("遥操记录/回放已停止："+str(exc),flush=True);raise SystemExit(1)

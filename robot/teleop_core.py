"""Pure validation and timing for small, measured-state teleoperation traces."""
import bisect
import json
import math
from pathlib import Path

SCHEMA = "cobot-platform-teleop-state-v2"
LEGACY_SCHEMA = "cobot-platform-teleop-state-v1"
SIDES = ("left", "right")
JOINT_NAMES = ["joint%d" % i for i in range(7)]
UNITS = {"joints": "rad", "gripper": "m", "time": "s"}
RATE = 50.0
JOINT_SPEED = 0.3
GRIPPER_SPEED = 0.05


def position(values):
    if not isinstance(values,(list,tuple)) or len(values)!=7:
        raise ValueError("每臂需要6个关节和1个夹爪位置")
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):
        raise ValueError("robot state包含无效数值")
    return list(values)  # Preserve measured values; no nominal range check or clamp.


def load_trace(path):
    path=Path(path)
    if not path.is_file():
        raise ValueError("记录不存在")
    with path.open() as handle:
        rows=[json.loads(line) for line in handle if line.strip()]
    if len(rows)<3:
        raise ValueError("记录过短")
    header,footer=rows[0],rows[-1]
    if header.get("schema") not in (SCHEMA,LEGACY_SCHEMA) or header.get("source")!="front_measured":
        raise ValueError("不是本脚本的前臂实测记录")
    if header.get("units")!=UNITS or header.get("joint_names")!=JOINT_NAMES:
        raise ValueError("记录单位或关节顺序不匹配")
    if footer.get("type")!="footer" or footer.get("complete") is not True or footer.get("valid_for_replay") is not True:
        raise ValueError("记录未完整结束或不适合回放")
    frames=rows[1:-1]
    if footer.get("frames")!=len(frames):
        raise ValueError("记录帧数与终结信息不一致")
    previous=None
    for f in frames:
        t=f.get("t")
        if f.get("type")!="sample" or isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) or t<0:
            raise ValueError("记录时间无效")
        if previous is not None and not 0<t-previous:
            raise ValueError("记录时间倒退/重复")
        previous=t
        for side in SIDES:
            f["front"][side]["position"]=position(f["front"][side]["position"])
    # Replay the full Enter-to-Enter interval. No rear/teach fields are required.
    return frames


def retime(frames):
    times=[0.0]
    poses=[{s:frames[0]["front"][s]["position"] for s in SIDES}]
    for before,after in zip(frames,frames[1:]):
        dt=after["t"]-before["t"]
        pair={s:after["front"][s]["position"] for s in SIDES}
        for s in SIDES:
            for i,(a,b) in enumerate(zip(poses[-1][s],pair[s])):
                speed=GRIPPER_SPEED if i==6 else JOINT_SPEED
                dt=max(dt,abs(b-a)/speed)
        times.append(times[-1]+dt)
        poses.append(pair)
    return times,poses


def interpolate(times,poses,t):
    if t<=0:return poses[0]
    if t>=times[-1]:return poses[-1]
    i=bisect.bisect_right(times,t)-1
    fraction=(t-times[i])/(times[i+1]-times[i])
    return {s:[a+(b-a)*fraction for a,b in zip(poses[i][s],poses[i+1][s])] for s in SIDES}


def start_path(current,target):
    duration=.02
    for s in SIDES:
        for i,(a,b) in enumerate(zip(current[s],target[s])):
            speed=GRIPPER_SPEED if i==6 else JOINT_SPEED
            # Cubic easing has peak slope 1.5. A second bound limits startup acceleration.
            duration=max(duration,1.5*abs(b-a)/speed,math.sqrt(6*abs(b-a)/(.15 if i==6 else .8)))
    ticks=max(1,math.ceil(duration*RATE))
    for tick in range(1,ticks+1):
        u=tick/ticks;blend=u*u*(3-2*u)
        yield {s:[a+(b-a)*blend for a,b in zip(current[s],target[s])] for s in SIDES}

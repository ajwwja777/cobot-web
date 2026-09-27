"""No hardware: mock ROS publishing and use isolated YAML fixtures."""
import copy
import errno
import importlib.util
import json
import sys
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
import yaml

ROBOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROBOT))
import mid_home as mid
spec = importlib.util.spec_from_file_location("platform_home_cli", ROBOT / "home.py")
home = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home)


def test_middle_capture_only_and_preserves_other_poses(tmp_path, monkeypatch):
    config = {"poses": {"plug": {"front_left": [0]*7, "mid": [1]*7}, "other": {"x": [4]}}, "speed": {"joint_rad_per_sec": .3}}
    p=tmp_path/"poses.yaml";p.write_text(yaml.safe_dump(config))
    calls=[]
    def measured(topic):
        calls.append(topic);return tuple([.1]*7)
    monkeypatch.setattr(home, "measured", measured)
    args=NS(arm="mid", include_rear=False, pose="plug", config=str(p))
    assert home.do_capture(args, config)
    after=yaml.safe_load(p.read_text())
    assert calls==[mid.FEEDBACK]
    assert after["poses"]["plug"]["mid"]==[.1]*7
    assert after["poses"]["plug"]["front_left"]==[0]*7
    assert after["poses"]["other"]==config["poses"]["other"]
    assert after["speed"]==config["speed"]


def test_middle_only_named_pose_and_legacy_front_capture(tmp_path, monkeypatch):
    assert mid.resolve_target({"poses":{"camera":{"mid":[0]*7}}}, "camera")==tuple([0]*7)
    with pytest.raises(ValueError, match="没有中臂"):
        mid.resolve_target({"poses":{"plug":{"front_left":[0]*7}}}, "plug")
    p=tmp_path/"poses.yaml";p.write_text("poses: {}\n")
    calls=[]
    monkeypatch.setattr(home,"measured",lambda topic:calls.append(topic) or [0]*7)
    assert home.do_capture(NS(arm="front",include_rear=True,pose="legacy",config=str(p)), {})
    assert len(calls)==4 and mid.FEEDBACK not in calls
    assert set(yaml.safe_load(p.read_text())["poses"]["legacy"])=={"front_left","front_right","rear_left","rear_right"}


def test_failed_capture_does_not_partially_update_yaml(tmp_path, monkeypatch):
    p=tmp_path/"poses.yaml";p.write_text("poses: {}\n");before=p.read_bytes()
    monkeypatch.setattr(home,"measured",Mock(side_effect=RuntimeError("feedback missing")))
    with pytest.raises(RuntimeError):home.do_capture(NS(arm="mid",include_rear=False,pose="camera",config=str(p)), {})
    assert p.read_bytes()==before


@pytest.mark.parametrize("arm,expected",[
    ("rear", {"rear_left","rear_right"}),
    ("all", {"front_left","front_right"}),
])
def test_capture_selected_group_and_new_pose_is_immediately_resolvable(tmp_path,monkeypatch,arm,expected):
    p=tmp_path/"poses.yaml";p.write_text("poses: {}\n")
    monkeypatch.setattr(home,"measured",lambda topic:[.2]*7)
    assert home.do_capture(NS(arm=arm,include_rear=False,pose="onsite_v2",config=str(p)),{})
    config=yaml.safe_load(p.read_text())
    assert set(config["poses"]["onsite_v2"])==expected
    if arm=="rear":
        assert set(home.resolve_for_action(config,"onsite_v2","rear"))==expected


def test_capture_all_replaces_stale_rear_overrides_and_all_home_reuses_front(tmp_path,monkeypatch):
    p=tmp_path/"poses.yaml"
    old={"poses":{"shared":{"front_left":[0]*7,"front_right":[0]*7,
                              "rear_left":[8]*7,"rear_right":[9]*7}}}
    p.write_text(yaml.safe_dump(old))
    values={home.FRONT_FEEDBACK["left"]:[.1]*7,
            home.FRONT_FEEDBACK["right"]:[.2]*7}
    calls=[]
    monkeypatch.setattr(home,"measured",lambda topic:calls.append(topic) or values[topic])
    assert home.do_capture(NS(arm="all",include_rear=False,pose="shared",config=str(p)),old)
    config=yaml.safe_load(p.read_text())
    assert calls==[home.FRONT_FEEDBACK["left"],home.FRONT_FEEDBACK["right"]]
    assert set(config["poses"]["shared"])=={"front_left","front_right"}
    resolved=home.resolve_for_action(config,"shared","all")
    assert resolved["rear_left"]==resolved["front_left"]==tuple([.1]*7)
    assert resolved["rear_right"]==resolved["front_right"]==tuple([.2]*7)


def test_delete_pose_is_atomic_and_preserves_speed_and_other_poses(tmp_path):
    p=tmp_path/"poses.yaml"
    config={"poses":{"keep":{"front_left":[0]*7},"remove":{"mid":[1]*7}},"speed":{"joint_rad_per_sec":.3}}
    p.write_text(yaml.safe_dump(config))
    assert home.do_delete(NS(config=str(p),pose="remove",yes=True))
    after=yaml.safe_load(p.read_text())
    assert set(after["poses"])=={"keep"} and after["speed"]==config["speed"]
    with pytest.raises(ValueError,match="unknown pose"):
        home.do_delete(NS(config=str(p),pose="remove",yes=True))


def test_existing_middle_publisher_refused(monkeypatch):
    monkeypatch.setattr(mid.rospy,"get_name",lambda:"/home_test")
    graph=NS(getSystemState=lambda:([(mid.COMMAND,["/fix_mid_camera_pose"])],[(mid.COMMAND,["/driver"])],[]))
    with pytest.raises(RuntimeError,match="发布者"):mid.check_publishers(graph)
    graph.getSystemState=lambda:([], [], [])
    with pytest.raises(RuntimeError,match="未订阅"):mid.check_publishers(graph)
    graph.getSystemState=lambda:([(mid.COMMAND,["/home_test"])],[(mid.COMMAND,["/driver"])],[])
    mid.check_publishers(graph)


@pytest.mark.parametrize("payload",[
    {"phase":"rollout","policy_paused":False},
    {"phase":"rollout","policy_paused":True},
    {"phase":"recording_starting","policy_paused":True},
    {"phase":"hil","policy_paused":False},
    {"phase":"paused"},
    {"phase":"waiting_scene","policy_paused":False},
    {"phase":"waiting_scene","policy_paused":"true"},
    {"phase":"unknown","policy_paused":True},
])
def test_active_or_unknown_session_refused(payload, monkeypatch):
    response=NS(read=lambda:json.dumps(payload).encode())
    class CM:
        def __enter__(self):return response
        def __exit__(self,*args):pass
    monkeypatch.setattr(mid.urllib.request,"build_opener",lambda *args:NS(open=lambda *a,**k:CM()))
    with pytest.raises(RuntimeError,match="先暂停策略"):mid.check_session()


@pytest.mark.parametrize("phase",["stopped","idle","disarmed","ready","paused","terminal_pending","finalizing","replay_committing","waiting_scene","fault"])
def test_paused_session_allows_middle_home_without_unloading(phase, monkeypatch):
    response=NS(read=lambda:json.dumps({"phase":phase,"policy_paused":True,"session_id":"still-open"}).encode())
    class CM:
        def __enter__(self):return response
        def __exit__(self,*args):pass
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        return CM()
    monkeypatch.setattr(mid.urllib.request,"build_opener",lambda *args:NS(open=get))
    assert mid.check_session()==phase
    assert calls==["http://127.0.0.1:8026/api/session"]


def test_no_backend_allows_home_but_unreachable_backend_does_not(monkeypatch):
    def refused(*args,**kwargs):
        raise urllib.error.URLError(OSError(errno.ECONNREFUSED,"not running"))
    monkeypatch.setattr(mid.urllib.request,"build_opener",lambda *args:NS(open=refused))
    assert mid.check_session() is None
    def timeout(*args,**kwargs):
        raise urllib.error.URLError(TimeoutError("timeout"))
    monkeypatch.setattr(mid.urllib.request,"build_opener",lambda *args:NS(open=timeout))
    with pytest.raises(RuntimeError,match="无法确认 RLT 策略已暂停"):
        mid.check_session()


def test_feedback_health_and_timestamp(monkeypatch):
    f=mid.Feedback.__new__(mid.Feedback);f.lock=__import__("threading").Lock()
    monkeypatch.setattr(mid.rospy.Time,"now",lambda:NS(to_sec=lambda:100.))
    joints=NS(position=[0]*7,header=NS(stamp=NS(to_sec=lambda:100.)))
    status=NS(ctrl_mode=1,arm_status=0,err_code=0,teach_status=0)
    f.values={"joints":(joints,time.monotonic()),"status":(status,time.monotonic())}
    assert f.read()==tuple([0]*7)
    status.err_code=1
    with pytest.raises(RuntimeError,match="未就绪"):f.read()
    status.err_code=0;f.values["joints"]=(joints,time.monotonic()-2)
    with pytest.raises(RuntimeError,match="过期"):f.read()


@pytest.mark.parametrize("tracking_partial", [False, True])
def test_mock_move_only_middle_preserves_gripper(monkeypatch, tracking_partial, capsys):
    current=[0]*6+[.02];commands=[];closed=[];checks=[];sleeps=[]
    class Feed:
        def read(self):return tuple(current)
        def close(self):closed.append("feedback")
    class Pub:
        def get_num_connections(self):return 1
        def publish(self,msg):
            commands.append(msg);current[:]=msg.position
            if tracking_partial:current[4]=min(current[4], .035)
        def unregister(self):closed.append("publisher")
    monkeypatch.setattr(mid,"Feedback",Feed)
    monkeypatch.setattr(mid,"check_publishers",lambda master:checks.append("publisher"))
    monkeypatch.setattr(mid,"check_session",lambda:checks.append("session"))
    monkeypatch.setattr(mid.rosgraph,"Master",lambda name:None)
    monkeypatch.setattr(mid.rospy,"get_name",lambda:"/mock")
    monkeypatch.setattr(mid.rospy,"is_shutdown",lambda:False)
    monkeypatch.setattr(mid.rospy.Time,"now",lambda:__import__("rospy").Time(100))
    monkeypatch.setattr(mid.time,"sleep",lambda seconds:sleeps.append(seconds))
    def publisher(topic,*a,**kw):
        assert topic==mid.COMMAND;return Pub()
    monkeypatch.setattr(mid.rospy,"Publisher",publisher)
    monkeypatch.setattr("builtins.input",lambda prompt:"")
    target=[.03]*6+[.07]
    if tracking_partial:target[4]=.08
    assert mid._move(target,{})
    report=capsys.readouterr().out
    assert "目标发送完成" in report and "现场确认" in report
    if tracking_partial:
        assert current[4]==.035 and commands[-1].position[4]==.08
        assert "0.045000" in report
    assert commands and all(m.position[6]==.02 for m in commands)
    # Ownership/session checks are two movement preflights, never blocking
    # calls inside the 50 Hz trajectory loop.  Every emitted point therefore
    # has the same cadence as front/all home.
    assert checks==["publisher","session","publisher","session"]
    assert len(sleeps)==len(commands)
    assert all(seconds==pytest.approx(1/50.) for seconds in sleeps)
    q=[0]*6
    for m in commands:
        assert max(abs(a-b) for a,b in zip(q,m.position[:6]))<=.00600001
        q=m.position[:6]
    assert closed==["publisher","feedback"]


def test_legacy_movement_functions_are_unchanged():
    import ast
    before=ast.parse((ROBOT.parent/"runtime/diagnostics/mid-home-20260918/home-before.py").read_text())
    after=ast.parse((ROBOT/"home.py").read_text())
    names={"do_front","do_rear","do_all","verify_front_arrival","push_params"}
    left={n.name:ast.dump(n) for n in before.body if isinstance(n,ast.FunctionDef) and n.name in names}
    right={n.name:ast.dump(n) for n in after.body if isinstance(n,ast.FunctionDef) and n.name in names}
    assert left==right and len(left)==5

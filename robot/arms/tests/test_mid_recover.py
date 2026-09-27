"""Recovery frames are captured by fake CAN sockets; never touch hardware."""
import fcntl
import errno
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest

ROBOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROBOT))
import front_reset as reset
import mid_home as mid
import recover


def fake_recovery(module, monkeypatch, bus):
    frames=[];state={"enabled":True};angles=Mock(return_value=[0]*6)
    class FakeSocket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,address):assert address==(bus,)
    monkeypatch.setattr(module.socket,"socket",lambda *args:FakeSocket())
    def send(sock,cid,data):
        frames.append((cid,bytes(data)))
        if cid==0x471:state["enabled"]=data[1]==2
    def feedback(actual_bus,*args):
        assert actual_bus==bus
        return {"ctrl_mode":1,"arm_status":0,"err_code":0,"control_frames":0,
                "motors":[{"enabled":state["enabled"],"fault_bits":"0x0"} for _ in range(6)]}
    monkeypatch.setattr(module,"send",send)
    monkeypatch.setattr(module,"feedback",feedback)
    monkeypatch.setattr(module,"measured_angles",angles)
    monkeypatch.setattr(module.time,"sleep",lambda *args:None)
    monkeypatch.setattr(module,"operator_guard",lambda side, **kwargs:None)
    if hasattr(module,"prepare_can_tx"):
        monkeypatch.setattr(module,"prepare_can_tx",lambda *args,**kwargs:False)
    if hasattr(module,"mid_recovery_guard"):
        monkeypatch.setattr(module,"mid_recovery_guard",lambda:None)
    return frames,angles


def test_full_tx_queue_resets_only_target_once_before_recovery(monkeypatch):
    probes=[]
    def probe(bus):
        probes.append(bus)
        if len(probes)==1:
            raise OSError(errno.ENOBUFS,"No buffer space available")
    resetter=Mock()
    monkeypatch.setattr(reset.time,"sleep",lambda *_:None)
    assert reset.prepare_can_tx("can_right","one-shot",resetter=resetter,prober=probe) is True
    assert probes==["can_right","can_right"]
    resetter.assert_called_once_with("can_right","one-shot")


def test_non_enobufs_probe_error_never_resets():
    resetter=Mock()
    with pytest.raises(OSError) as caught:
        reset.prepare_can_tx("can_left",resetter=resetter,prober=lambda bus: (_ for _ in ()).throw(OSError(errno.ENODEV,"gone")))
    assert caught.value.errno==errno.ENODEV
    resetter.assert_not_called()


def test_second_full_queue_failure_does_not_loop(monkeypatch):
    resetter=Mock();monkeypatch.setattr(reset.time,"sleep",lambda *_:None)
    def probe(bus):raise OSError(errno.ENOBUFS,"still full")
    with pytest.raises(OSError,match="still full"):
        reset.prepare_can_tx("can_mid",resetter=resetter,prober=probe)
    resetter.assert_called_once_with("can_mid",None)


def test_middle_uses_scoped_bus_no_gripper_and_no_arrival_gate(tmp_path,monkeypatch):
    monkeypatch.setattr(mid,"ROOT",str(tmp_path))
    frames,angles=fake_recovery(reset,monkeypatch,"can_mid")
    result=reset.recover_mid()
    assert result["ctrl_mode"]==1 and all(m["enabled"] for m in result["motors"])
    assert frames[0]==(0x471,bytes([7,1,0,0,0,0,0,0]))
    assert [data[1] for cid,data in frames if cid==0x471]==[1,1,2]
    assert set(cid for cid,data in frames)<={0x471,0x150,0x151,0x155,0x156,0x157}
    assert 0x159 not in [cid for cid,data in frames]
    assert angles.call_count==1  # No measured-arrival criterion in recovery.


def test_front_sequence_identical_to_before_refactor(monkeypatch):
    spec=importlib.util.spec_from_file_location("front_reset_before",ROBOT.parent/"runtime/diagnostics/mid-recover-20260918/before-front_reset.py")
    before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
    a,_=fake_recovery(before,monkeypatch,"can_left");before.recover_front("left")
    b,_=fake_recovery(reset,monkeypatch,"can_left");reset.recover_front("left")
    assert a==b and len(a)>10


def test_control_traffic_refuses_before_any_write(tmp_path,monkeypatch):
    monkeypatch.setattr(mid,"ROOT",str(tmp_path))
    frames,_=fake_recovery(reset,monkeypatch,"can_mid")
    monkeypatch.setattr(reset,"feedback",lambda *a:{"control_frames":1})
    with pytest.raises(RuntimeError,match="其他控制指令"):reset.recover_mid()
    assert frames==[]


def test_disable_not_acknowledged_never_enables(tmp_path,monkeypatch):
    monkeypatch.setattr(mid,"ROOT",str(tmp_path))
    frames,angles=fake_recovery(reset,monkeypatch,"can_mid")
    monkeypatch.setattr(reset,"feedback",lambda *a:{"control_frames":0,"motors":[{"enabled":True}]*6})
    with pytest.raises(RuntimeError,match="未完全失能"):reset.recover_mid()
    assert all(not(cid==0x471 and data[1]==2) for cid,data in frames)
    angles.assert_not_called()


def test_home_and_recover_share_exclusive_lock(tmp_path,monkeypatch):
    monkeypatch.setattr(mid,"ROOT",str(tmp_path))
    p=tmp_path/"runtime/arms/home-mid.lock";p.parent.mkdir(parents=True)
    with p.open("a") as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match="正在运行"):reset.recover_mid()


def test_one_enter_dispatches_mid_without_ros_service_call(monkeypatch):
    monkeypatch.setattr(sys,"argv",["recover.py","mid"])
    monkeypatch.setattr(sys,"stdin",NS(isatty=lambda:True))
    inputs=[]
    monkeypatch.setattr("builtins.input",lambda prompt:inputs.append(prompt) or "")
    worker=Mock(return_value={"ctrl_mode":1})
    monkeypatch.setattr(reset,"recover_mid",worker)
    service=Mock(side_effect=AssertionError("must not invoke rear services"))
    monkeypatch.setattr(recover.rospy,"ServiceProxy",service)
    assert recover.main()==0 and len(inputs)==1
    worker.assert_called_once();service.assert_not_called()


def test_mid_guard_validates_owner_mode_and_cartesian_publishers(monkeypatch):
    import rosgraph
    graph=[[("/puppet/arm_status_mid",["/piper_mid_node"])],
           [("/master/joint_mid",["/piper_mid_node"]),("/pos_cmd",["/piper_mid_node"])], []]
    monkeypatch.setattr(reset.rospy.core,"is_initialized",lambda:True)
    monkeypatch.setattr(reset.rospy,"get_name",lambda:"/mid_recovery")
    monkeypatch.setattr(rosgraph,"Master",lambda name:NS(getSystemState=lambda:graph))
    monkeypatch.setattr(mid,"check_publishers",lambda master:None)
    monkeypatch.setattr(mid,"check_session",lambda:"stopped")
    params={"/piper_mid_node/can_port":"can_mid","/piper_mid_node/mode":1}
    monkeypatch.setattr(reset.rospy,"get_param",lambda key,default:params.get(key,default))
    reset.mid_recovery_guard()
    params["/piper_mid_node/can_port"]="can_left"
    with pytest.raises(RuntimeError,match="CAN 配置"):reset.mid_recovery_guard()
    params["/piper_mid_node/can_port"]="can_mid"
    graph[0].append(("/pos_cmd",["/another_controller"]))
    with pytest.raises(RuntimeError,match="末端位置控制"):reset.mid_recovery_guard()
    graph[0].pop();graph[2].append(("/task2/policy/arm",["/rlt"]))
    reset.mid_recovery_guard()  # Ended Session permits retained policy services.
    monkeypatch.setattr(mid,"check_session",lambda:None)
    with pytest.raises(RuntimeError,match="状态不可用"):reset.mid_recovery_guard()


@pytest.mark.parametrize("phase",["stopped","idle","disarmed"])
def test_ended_session_http_state_allows_model_retention(phase,monkeypatch):
    import json
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({"phase":phase,"policy_paused":True}).encode()
    monkeypatch.setattr(mid.urllib.request,"build_opener",lambda *args:NS(open=lambda *a,**k:Response()))
    assert mid.check_session()==phase


def test_inconsistent_stopped_but_policy_not_paused_refused(monkeypatch):
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'{"phase":"stopped","policy_paused":false}'
    monkeypatch.setattr(mid.urllib.request,"build_opener",lambda *args:NS(open=lambda *a,**k:Response()))
    with pytest.raises(RuntimeError,match="策略尚未暂停"):mid.check_session()

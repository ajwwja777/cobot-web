import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
ROBOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROBOT))
import sync_recovery as sync
import front_mode
import recover

def snapshot():
    joint=lambda:NS(position=[0.]*7,header=NS(stamp=NS(to_sec=lambda:100.)))
    return {key: (NS(data=False) if key.startswith("button_") else joint(), 10.) for key in sync.TOPICS}

def test_fresh_released_feedback_valid():
    sync.validate_snapshot(snapshot(),10.01,100.01)

@pytest.mark.parametrize("case",["missing","old_arrival","old_stamp","button","nan","short"])
def test_bad_snapshot_refuses(case):
    s=snapshot()
    if case=="missing":del s["rear_right"]
    if case=="old_arrival":s["rear_right"]=(s["rear_right"][0],9.)
    if case=="old_stamp":s["rear_right"][0].header.stamp.to_sec=lambda:90.
    if case=="button":s["button_right"][0].data=True
    if case=="nan":s["front_right"][0].position[0]=float("nan")
    if case=="short":s["front_right"][0].position=[]
    with pytest.raises(RuntimeError):sync.validate_snapshot(s,10.01,100.01)

@pytest.mark.parametrize("button,ok",[(False,True),(True,False)])
def test_reset_calls_only_coordinator_after_guard_and_cleans_subscriptions(monkeypatch,button,ok):
    monkeypatch.setattr(sync.rospy.core,"is_initialized",lambda:True)
    monkeypatch.setattr(sync.rospy,"wait_for_service",lambda *a,**k:None)
    monkeypatch.setattr(sync.rospy,"is_shutdown",lambda:False)
    monkeypatch.setattr(sync.time,"monotonic",lambda:10.01)
    monkeypatch.setattr(sync.time,"time",lambda:100.01)
    s=snapshot();s["button_right"][0].data=button
    handles=[]
    def subscribe(topic,kind,callback,**kwargs):
        key=next(k for k,v in sync.TOPICS.items() if v[0]==topic)
        callback(s[key][0]);handle=NS(unregister=Mock());handles.append(handle);return handle
    monkeypatch.setattr(sync.rospy,"Subscriber",subscribe)
    proxy=Mock(return_value=Mock(return_value=NS(success=True,message="cleared")))
    monkeypatch.setattr(sync.rospy,"ServiceProxy",proxy)
    if ok:
        sync.recover_sync();proxy.assert_called_once_with(sync.SERVICE,sync.Trigger)
    else:
        with pytest.raises(RuntimeError,match="释放"):sync.recover_sync()
        proxy.assert_not_called()
    assert len(handles)==6 and all(h.unregister.call_count==1 for h in handles)

@pytest.mark.parametrize("side",["left","right"])
def test_historical_fault_allowed_only_for_hardware_repair(monkeypatch,side):
    owner="/piper_"+side+"_node"
    graph=[[('/puppet/arm_status_'+side,[owner])],[],[('/task2/policy/arm',['/rlt'])]]
    monkeypatch.setattr(front_mode.rospy.core,"is_initialized",lambda:True)
    monkeypatch.setattr(front_mode.rospy,"get_name",lambda:"/test")
    monkeypatch.setattr(front_mode.rosgraph,"Master",lambda name:NS(getSystemState=lambda:graph))
    monkeypatch.setattr(front_mode.rospy,"get_param",lambda *a:"can_"+side)
    monkeypatch.setattr(front_mode.rospy,"wait_for_message",lambda topic,*a,**k:NS(data="old feedback fault" if topic.endswith('/fault') else False))
    with pytest.raises(RuntimeError,match="coordinator fault"):front_mode.operator_guard(side)
    front_mode.operator_guard(side,allow_coordinator_fault=True)

def test_sync_dispatch_has_no_hardware_prompt_or_front_recovery(monkeypatch):
    monkeypatch.setattr(sys,"argv",["recover.py","sync"])
    monkeypatch.setattr("builtins.input",Mock(side_effect=AssertionError("no disable prompt")))
    worker=Mock();monkeypatch.setattr(sync,"recover_sync",worker)
    assert recover.main()==0
    worker.assert_called_once_with()

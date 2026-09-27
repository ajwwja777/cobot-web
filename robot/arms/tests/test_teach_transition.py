import importlib.util
from pathlib import Path
from unittest.mock import patch
from test_handover_node import Clock,FakeRos,ServiceFactory,install_import_stubs,joint,Bool
from test_rear_teach_node import FakePiper,build_node,NODE as REAR,TEACHING,TEACH_START,TEACH_STOP,STANDBY

install_import_stubs()
spec=importlib.util.spec_from_file_location("platform_teach_handover",str(Path(__file__).resolve().parents[1]/"code/teach_handover.py"))
WRAP=importlib.util.module_from_spec(spec);spec.loader.exec_module(WRAP)

def fixture():
    clock=Clock();ros=FakeRos(clock);services=ServiceFactory(ros.events)
    node=WRAP.TeachHandoverNode(ros_api=ros,service_factory=services,wall_clock=lambda:clock.wall,monotonic_clock=lambda:clock.monotonic)
    update(node,clock,.0,.4)
    node._teach_callback("left")(Bool(False))
    return clock,ros,node

def update(node,clock,rear,front):
    for side in ("left","right"):
        node._feedback_callback("rear_"+side)(joint(clock.wall,[rear]*6+[.01]))
        node._feedback_callback("front_"+side)(joint(clock.wall,[front]*6+[.01]))

def test_fixed_entry_holds_then_captures_current_rear_without_jump():
    clock,ros,node=fixture()
    node._teach_callback("left")(Bool(True))
    assert node._engaged["left"] and ("service","/task2/policy/set_paused",True) in ros.events
    clock.advance(.1);update(node,clock,.02,.4);node._forward_once()
    assert node.front_pubs["left"].messages[-1].position[:6]==[.4]*6
    clock.advance(.105);update(node,clock,.05,.4);node._forward_once()
    assert node._entry["left"] is None
    assert node.front_pubs["left"].messages[-1].position[:6]==[.4]*6
    clock.advance(.01);update(node,clock,.06,.4);node._forward_once()
    assert all(abs(p-.41)<1e-9 for p in node.front_pubs["left"].messages[-1].position[:6])

def test_continuously_moving_rear_ready_after_fixed_interval():
    clock,ros,node=fixture();node._teach_callback("left")(Bool(True))
    for i in range(21):
        clock.advance(.01);update(node,clock,.01*(i+1),.4);node._forward_once()
    assert node._entry["left"] is None
    clock.advance(.01);update(node,clock,.22,.4);node._forward_once()
    assert any(p>.4 for p in node.front_pubs["left"].messages[-1].position[:6])

def test_policy_commands_blocked_during_entry():
    clock,ros,node=fixture();node._teach_callback("left")(Bool(True))
    n=len(node.front_pubs["left"].messages)
    node._policy_callback("left")(joint(clock.wall,[.8]*6+[.01]))
    assert len(node.front_pubs["left"].messages)==n
    node._forward_once()
    assert node.front_pubs["left"].messages[-1].position[:6]==[.4]*6

def test_exit_stops_following_drop_and_reentry_recaptures():
    clock,ros,node=fixture();node._teach_callback("left")(Bool(True))
    for _ in range(6):
        clock.advance(.1);update(node,clock,.05,.4);node._forward_once()
    node._teach_callback("left")(Bool(False))
    n=len(node.front_pubs["left"].messages)
    clock.advance(.01);update(node,clock,-.2,.4);node._forward_once()
    assert len(node.front_pubs["left"].messages)==n
    node._teach_callback("left")(Bool(True))
    for _ in range(6):
        clock.advance(.1);update(node,clock,-.2,.4);node._forward_once()
        assert node.front_pubs["left"].messages[-1].position[:6]==[.4]*6

def test_home_is_exact_original_inherited_method():
    assert WRAP.TeachHandoverNode.handle_home_front is WRAP.original.Task2TeachButtonNode.handle_home_front
    assert WRAP.TeachHandoverNode.handle_reset_fault is WRAP.original.Task2TeachButtonNode.handle_reset_fault

def test_raw_edges_takeover_and_disable_immediate():
    piper=FakePiper("can_rear_left");ros,node=build_node(piper,{"~auto_enable":True})
    with patch.object(REAR.time,"monotonic",return_value=100.0):
        piper.ctrl_mode=TEACHING;piper.teach_status=TEACH_START
        node._update_teach_state(piper.GetArmStatus())
    assert node.controller.gate is REAR.GateState.TEACHING
    assert node.teach_active_pub.messages[-1].data is True
    disables=len(piper.disable_calls)
    piper.ctrl_mode=STANDBY;piper.teach_status=TEACH_STOP
    with patch.object(REAR.time,"monotonic",return_value=100.01):
        node._update_teach_state(piper.GetArmStatus())
    assert node.teach_active_pub.messages[-1].data is False
    assert len(piper.disable_calls)==disables+1
    assert not piper.enabled
    with patch.object(REAR.time,"monotonic",return_value=100.02):
        node._update_teach_state(piper.GetArmStatus())
        node._verify_still_ready(piper.GetArmStatus())
    assert len(piper.disable_calls)==disables+1
    assert node._teach_exit_pending is None

def test_recording_listener_does_not_change_exit_order_or_hardware_calls():
    traces=[]
    for recording in (False,True):
        piper=FakePiper("can_rear_left");ros,node=build_node(piper,{"~auto_enable":True})
        piper.ctrl_mode=TEACHING;piper.teach_status=TEACH_START
        node._update_teach_state(piper.GetArmStatus())
        events=[]
        topic=node.teach_active_pub.topic
        seen=[]
        def listener(msg):
            events.append(("gate",msg.data))
            if recording:seen.append(msg.data)
        ros.hooks[topic]=listener
        real=piper.DisableArm
        def disable(*args):
            events.append(("disable",args))
            return real(*args)
        piper.DisableArm=disable
        piper.ctrl_mode=STANDBY;piper.teach_status=TEACH_STOP
        node._update_teach_state(piper.GetArmStatus())
        assert events.index(("gate",False))<events.index(("disable",(7,)))
        traces.append(events)
    assert traces[0]==traces[1]

def test_fault_not_cleared_by_raw_teach_edge():
    piper=FakePiper("can_rear_left");ros,node=build_node(piper,{"~auto_enable":True})
    node.controller.latch_fault()
    piper.ctrl_mode=TEACHING;piper.teach_status=TEACH_START
    node._update_teach_state(piper.GetArmStatus())
    assert node.controller.gate is REAR.GateState.FAULT
    assert node.teach_active_pub.messages[-1].data is False

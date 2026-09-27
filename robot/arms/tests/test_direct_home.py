import time
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from test_rear_teach_node import NODE, FakePiper, build_node, _hold_message, TEACHING, TEACH_START


def fixture():
    p=FakePiper("can_rear_left")
    ros,node=build_node(p,{"~auto_enable":True,"~enable_verify_timeout_sec":0.04,"~mode_verify_poll_sec":0.002})
    p.enabled=False
    return p,ros,node


def test_measured_preload_and_mode_precede_enable_with_short_observation():
    p,ros,node=fixture()
    node._measured_positions=lambda:[.01,.02,-.03,0,0,0,0]
    node._last_target=(999999,)*7
    events=[]
    for name in ("JointCtrl","EnableArm","MotionCtrl_1","MotionCtrl_2"):
        original=getattr(p,name)
        def wrapped(*args,name=name,original=original):
            events.append((name,args));return original(*args)
        setattr(p,name,wrapped)
    original_sleep=time.sleep
    def short_poll(seconds):
        assert seconds<=.02
        original_sleep(seconds)
    with patch.object(NODE.time,"sleep",side_effect=short_poll):
        node._prepare_home_without_dwell()
    joint=next(i for i,e in enumerate(events) if e[0]=="JointCtrl")
    enable=next(i for i,e in enumerate(events) if e[0]=="EnableArm")
    mode=next(i for i,e in enumerate(events) if e[0]=="MotionCtrl_2")
    assert joint<mode<enable
    assert all(abs(a-b)<=1 for a,b in zip(events[joint][1],(573,1146,-1719,0,0,0)))
    assert len([e for e in events if e[0]=="EnableArm"])==1
    assert all(999999 not in args for name,args in events)
    assert node._homed and p.enabled and p.ctrl_mode==1


def test_holding_arm_is_not_reset_or_reenabled():
    p,ros,node=fixture();p.enabled=True;p.ctrl_mode=1
    before=len(p.motion_calls);enables=len(p.enable_calls)
    with patch.object(NODE.time,"sleep",side_effect=AssertionError("fixed sleep")):
        node._prepare_home_without_dwell()
    assert ("MotionCtrl_1",(2,0,0)) not in p.motion_calls[before:]
    assert len(p.enable_calls)==enables


def test_enable_ignored_times_out_without_reset_or_reenable_retry():
    p,ros,node=fixture();p.ignore_enable=True
    before=len(p.enable_calls);start=time.monotonic()
    with pytest.raises(RuntimeError,match="feedback timeout"):
        node._prepare_home_without_dwell()
    assert time.monotonic()-start<.9
    assert len(p.enable_calls)-before==1


def test_hardware_protection_refuses_enable():
    p,ros,node=fixture();p.motor_flags[3]=("driver_error_status",)
    before=len(p.enable_calls)
    with pytest.raises(RuntimeError,match="hardware fault"):
        node._prepare_home_without_dwell()
    assert len(p.enable_calls)==before


def test_physical_teaching_refuses_every_hardware_write():
    p,ros,node=fixture();p.ctrl_mode=TEACHING;p.teach_status=TEACH_START
    before=(len(p.motion_calls),len(p.enable_calls),len(p.joint_calls))
    with pytest.raises(RuntimeError,match="physical teaching"):
        node._prepare_home_without_dwell()
    assert before==(len(p.motion_calls),len(p.enable_calls),len(p.joint_calls))


def test_late_protection_stops_without_rearming():
    p,ros,node=fixture();original=p.EnableArm;before=len(p.enable_calls)
    def enable(*args):
        original(*args);p.motor_flags[1]=("driver_overcurrent",)
    p.EnableArm=enable
    with pytest.raises(RuntimeError,match="hardware fault"):
        node._prepare_home_without_dwell()
    assert len(p.enable_calls)-before==1


def test_home_moves_from_measured_pose_without_fixed_settle_or_extra_target():
    p,ros,node=fixture();ros.params["/task2/homing/rear_left"]=[.001,.001,-.001,0,0,0,0]
    sleeps=[]
    with patch.object(NODE.time,"sleep",side_effect=lambda seconds:sleeps.append(seconds)):
        response=node.handle_home(None)
    assert response.success,response.message
    assert sleeps and max(sleeps)<=.05
    assert p.joint_calls[0]==(0,)*6
    assert all(max(abs(v) for v in command)<=57 for command in p.joint_calls)


def test_loss_of_enable_during_ramp_stops_without_rearming():
    p,ros,node=fixture();ros.params["/task2/homing/rear_left"]=[.1,.1,-.1,0,0,0,0]
    before=len(p.enable_calls)
    def lose_enable(seconds):
        if p.enabled:p.enabled=False
    with patch.object(NODE.time,"sleep",side_effect=lose_enable):
        response=node.handle_home(None)
    assert not response.success and "lost" in response.message
    assert len(p.enable_calls)-before==1
    assert node._last_target is None and not node._homed


def test_recover_idle_and_idle_position_commands_do_not_enable():
    p,ros,node=fixture();p.enabled=True
    before=len(p.enable_calls)
    assert node.handle_recover_idle(None).success
    assert not p.enabled
    node.joint_command_callback(_hold_message())
    assert len(p.enable_calls)==before


def test_teaching_takeover_during_home_has_priority_without_fault_latch():
    p,ros,node=fixture();ros.params["/task2/homing/rear_left"]=[.1,.1,-.1,0,0,0,0]
    def engage(seconds):
        if p.enabled:p.ctrl_mode=TEACHING;p.teach_status=TEACH_START
    with patch.object(NODE.time,"sleep",side_effect=engage):
        response=node.handle_home(None)
    assert not response.success and "physical teaching engaged" in response.message
    assert node.controller.gate is not NODE.GateState.FAULT
    assert not node._homed and node._last_target is None


@pytest.mark.parametrize("communication_mask",[0x3f,0x23,0x14,0])
def test_known_communication_transition_is_observed_only_while_fully_disabled(communication_mask):
    p,ros,node=fixture()
    original_status=p.GetArmStatus
    calls=[]
    def status():
        if p.enabled:
            p.arm_status=0;p.err_code=0
        elif p.ctrl_mode==1:
            calls.append(1)
            p.arm_status=5 if len(calls)<4 else 0
            p.err_code=communication_mask if len(calls)<4 else 0
        return original_status()
    p.GetArmStatus=status
    node._prepare_home_without_dwell()
    assert len(calls)>=4 and p.enabled
    assert len(p.enable_calls)==1


def test_same_communication_error_after_enable_is_not_ignored():
    p,ros,node=fixture();original=p.EnableArm
    def enable(*args):original(*args);p.arm_status=5;p.err_code=0x3f
    p.EnableArm=enable
    with pytest.raises(RuntimeError,match="enable_confirmation"):
        node._prepare_home_without_dwell()
    assert len(p.enable_calls)==1


def test_other_arm_error_is_not_misclassified_as_disabled_transition():
    p,ros,node=fixture();p.arm_status=4;p.err_code=1
    with pytest.raises(RuntimeError,match='"arm_status": 4'):
        node._prepare_home_without_dwell()
    assert not p.enable_calls


@pytest.mark.parametrize("mask",range(64))
def test_every_low_six_bit_communication_mask_is_recognized(mask):
    assert NODE.PiperRearTeachNode._disabled_can_communication_transition(True,5,mask,[False]*6)

@pytest.mark.parametrize("mask",[0x40,0x80,0x100,0x200,0x1023,0x8000,0xffff,-1])
def test_reserved_angle_and_unknown_masks_are_rejected(mask):
    assert not NODE.PiperRearTeachNode._disabled_can_communication_transition(True,5,mask,[False]*6)

@pytest.mark.parametrize("original_disabled,arm_status,bits",[(False,5,[False]*6),(True,4,[False]*6),(True,5,[True]+[False]*5),(True,5,None),(True,5,[False]*5)])
def test_communication_wait_requires_original_and_current_full_disable(original_disabled,arm_status,bits):
    assert not NODE.PiperRearTeachNode._disabled_can_communication_transition(original_disabled,arm_status,0x23,bits)

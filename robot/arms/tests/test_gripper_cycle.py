import importlib.util
import socket
import struct
from pathlib import Path
from unittest.mock import Mock, call
import pytest

spec = importlib.util.spec_from_file_location("gripper_cycle", Path(__file__).parents[2] / "gripper_cycle.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def ready_monitor():
    sock = Mock()
    m = g.Monitor(sock, clock=lambda: 10.0)
    m.latest[0x2a1] = (10, bytes([1,0,0,0,0,0,0,0]))
    for i in range(0x261,0x267):
        m.latest[i] = (10, bytes([0,0,0,0,0,0x40,0,0]))
    for i in [0x2a5,0x2a6,0x2a7]:m.latest[i]=(10, bytes(8))
    m.latest[0x2a8]=(10, bytes([0,0,0,0,0,0,0x40,0]))
    return m


@pytest.mark.parametrize("target", [0,30000,80000])
def test_frame_is_only_gripper_without_clear_or_zero(target):
    cid,n,data=struct.unpack("=IB3x8s",g.command_frame(target))
    assert cid==0x159 and n==8
    assert struct.unpack(">iHBB",data)==(target,1000,1,0)


@pytest.mark.parametrize("target", [-1,80001])
def test_invalid_opening_rejected(target):
    with pytest.raises(ValueError):g.command_frame(target)


class Clock:
    def __init__(self):self.now=0.0
    def __call__(self):return self.now


def cycle_fixture(open_delays=None):
    clock=Clock();events=[];sockets={};monitors={};open_delays=open_delays or {"left":.08,"right":.04}
    for side in ["left","right"]:
        sock=Mock();m=Mock();state={"target":0,"sent":-1,"confirmed":False}
        def send(raw,side=side,state=state):
            cid,n,data=struct.unpack("=IB3x8s",raw)
            assert cid==0x159 and n==8
            target=struct.unpack(">iHBB",data)[0]
            state.update(target=target,sent=clock.now,confirmed=False)
            events.append((side,target,clock.now))
        def poll():clock.now+=.01
        def fresh(cid,since=None,side=side,state=state):
            assert cid==0x2a8
            if since is not None and clock.now<=since:return None
            delay=open_delays[side] if state["target"]==g.OPEN_UM else .04
            reached=clock.now-state["sent"]>=delay
            target=state["target"] if reached else (0 if state["target"] else g.OPEN_UM)
            if reached and not state["confirmed"]:
                events.append((side,"arrived_"+str(state["target"]),clock.now));state["confirmed"]=True
            return struct.pack(">iHBB",target,0,0x40,0)
        sock.sendall.side_effect=send;m.poll.side_effect=poll;m.fresh.side_effect=fresh
        m.check.return_value=(0,)*6;m.clock=clock
        sockets[side]=sock;monitors[side]=m
    return sockets,monitors,clock,events


def test_both_open_first_and_half_second_starts_after_last_arrival():
    sockets,monitors,clock,events=cycle_fixture()
    g.run_cycle(sockets,monitors,clock)
    commands=[e for e in events if isinstance(e[1],int)]
    assert [(e[0],e[1]) for e in commands]==[("left",g.OPEN_UM),("right",g.OPEN_UM),("left",0),("right",0)]
    last_open=max(e[2] for e in events if e[1]=="arrived_"+str(g.OPEN_UM))
    assert commands[2][2]-last_open>=.5
    assert abs(commands[0][2]-commands[1][2])<.001
    assert abs(commands[2][2]-commands[3][2])<.001


@pytest.mark.parametrize("side",["left","right"])
def test_neither_sends_if_either_preflight_fails(side):
    sockets,monitors,clock,events=cycle_fixture();monitors[side].check.side_effect=RuntimeError("not ready")
    with pytest.raises(RuntimeError):g.run_cycle(sockets,monitors,clock)
    for sock in sockets.values():sock.sendall.assert_not_called()


def test_no_close_if_one_gripper_never_reaches_maximum():
    sockets,monitors,clock,events=cycle_fixture({"left":.04,"right":100})
    with pytest.raises(RuntimeError,match="未确认左右"):g.run_cycle(sockets,monitors,clock)
    for sock in sockets.values():
        sock.sendall.assert_called_once_with(g.command_frame(g.OPEN_UM))


def test_control_fault_during_half_second_prevents_both_closing():
    sockets,monitors,clock,events=cycle_fixture()
    def check(**kwargs):
        opened=[e for e in events if e[1]=="arrived_"+str(g.OPEN_UM)]
        if len(opened)==2 and clock.now-max(e[2] for e in opened)>.1:
            raise RuntimeError("other control")
        return (0,)*6
    monitors["right"].check.side_effect=check
    with pytest.raises(RuntimeError,match="other control"):g.run_cycle(sockets,monitors,clock)
    for sock in sockets.values():assert sock.sendall.call_count==1


def test_send_error_is_not_retried():
    sockets,monitors,clock,events=cycle_fixture();sockets["right"].sendall.side_effect=OSError("bus down")
    with pytest.raises(OSError):g.run_cycle(sockets,monitors,clock)
    for sock in sockets.values():assert sock.sendall.call_count==1


@pytest.mark.parametrize("cid", sorted(g.CONTROL_IDS))
def test_other_control_traffic_stops(cid):
    m=ready_monitor();m.sock.recv.return_value=struct.pack("=IB3x8s",cid,8,bytes(8))
    with pytest.raises(RuntimeError,match="其他控制"):m.poll()


@pytest.mark.parametrize("kind", ["disabled","motor_fault","mode","arm_fault","teach","error","stale","gripper_fault","moved"])
def test_unhealthy_or_moving_arm_rejected(kind):
    m=ready_monitor();m.anchor=(0,)*6
    if kind in ["disabled","motor_fault"]:
        data=bytearray(m.latest[0x261][1]);data[5]=0 if kind=="disabled" else 0x41
        m.latest[0x261]=(10,bytes(data))
    elif kind in ["mode","arm_fault","teach","error"]:
        data=bytearray(m.latest[0x2a1][1]);data[{"mode":0,"arm_fault":1,"teach":3,"error":7}[kind]]=2
        m.latest[0x2a1]=(10,bytes(data))
    elif kind=="stale":m.latest[0x2a8]=(9,bytes(8))
    elif kind=="gripper_fault":m.latest[0x2a8]=(10,bytes([0,0,0,0,0,0,0x41,0]))
    elif kind=="moved":m.latest[0x2a5]=(10,struct.pack(">ii",5000,0))
    with pytest.raises(RuntimeError):m.check()


def test_gripper_homed_flag_is_not_fault():
    m=ready_monitor();m.latest[0x2a8]=(10,bytes([0,0,0,0,0,0,0xc0,0]))
    assert m.check()==(0,)*6


def test_feedback_must_be_newer_than_command():
    m=ready_monitor()
    assert m.fresh(0x2a8,10) is None
    assert m.fresh(0x2a8,9.9) is not None


def test_noninteractive_launch_refuses_before_opening_socket(monkeypatch):
    monkeypatch.setattr(g.sys.stdin,"isatty",lambda:False)
    with pytest.raises(RuntimeError,match="交互"):g.main()



@pytest.mark.parametrize("code,flags",[(0x40,[]),(0xc0,[]),(0x70,["传感器异常","驱动器错误"]),
                                      (0x41,["电压过低"]),(0x44,["驱动器过流"])])
def test_decode_gripper_flags_distinguishes_enable_homing_from_errors(code,flags):
    report=g.gripper_report(struct.pack(">ihBB",-100,20,code,0))
    assert report["status_hex"]=="0x%02x"%code
    assert report["error_flags"]==flags
    assert report["opening_mm"]==-.1
    assert report["effort_nm"]==.02


def test_error_includes_device_status_and_specific_flags():
    m=ready_monitor();m.label="右前夹爪"
    m.latest[0x2a8]=(10,struct.pack(">ihBB",-100,20,0x70,0))
    with pytest.raises(RuntimeError,match="右前夹爪.*0x70.*传感器异常.*驱动器错误"):
        m.check()


@pytest.mark.parametrize("target",[0,200,70000])
def test_clear_frame_preserves_opening_enables_and_does_not_set_zero(target):
    cid,n,data=struct.unpack("=IB3x8s",g.command_frame(target,clear_error=True))
    assert cid==0x159 and n==8
    assert struct.unpack(">iHBB",data)==(target,1000,3,0)


def recovery_fixture(code=0x70,persistent=False,opening=200):
    clock=Clock();clock.now=10
    sockets={};monitors={};states={}
    for side in ["left","right"]:
        m=ready_monitor();m.clock=clock;m.label=side
        state={"code":code if side=="right" else 0x40,"opening":opening if side=="right" else 300}
        states[side]=state
        def poll(m=m,state=state):
            clock.now+=.01
            for cid,(stamp,data) in list(m.latest.items()):m.latest[cid]=(clock.now,data)
            m.latest[0x2a8]=(clock.now,struct.pack(">ihBB",state["opening"],20,state["code"],0))
        def send(raw,state=state):
            cid,n,data=struct.unpack("=IB3x8s",raw)
            target,effort,operation,zero=struct.unpack(">iHBB",data)
            assert cid==0x159 and operation in [1,3] and zero==0
            if operation==3:
                assert target==max(0,min(80000,state["opening"]))
                if not persistent:state["code"]=0x40
            else:
                assert state["code"]==0x40
                state["opening"]=target
        m.poll=Mock(side_effect=poll);m.sock.sendall.side_effect=send
        sockets[side]=m.sock;monitors[side]=m
    return sockets,monitors,clock,states


@pytest.mark.parametrize("code",[0x50,0x60,0x70])
def test_one_clear_only_on_flagged_gripper_then_require_normal_feedback(code):
    sockets,monitors,clock,states=recovery_fixture(code)
    g.clear_gripper_latches(sockets,monitors,clock)
    sockets["left"].sendall.assert_not_called()
    sockets["right"].sendall.assert_called_once_with(g.command_frame(200,clear_error=True))
    assert clock.now>=10.2
    for m in monitors.values():m.check()


def test_persistent_error_gets_only_one_clear_and_no_open_close():
    sockets,monitors,clock,states=recovery_fixture(persistent=True)
    with pytest.raises(RuntimeError,match="单次清状态后夹爪仍未恢复"):
        g.clear_gripper_latches(sockets,monitors,clock)
    sockets["left"].sendall.assert_not_called()
    sockets["right"].sendall.assert_called_once_with(g.command_frame(200,clear_error=True))


@pytest.mark.parametrize("code",[0x41,0x42,0x44,0x48,0x71])
def test_voltage_temperature_current_flags_refuse_clear(code):
    sockets,monitors,clock,states=recovery_fixture(code)
    with pytest.raises(RuntimeError):g.clear_gripper_latches(sockets,monitors,clock)
    for sock in sockets.values():sock.sendall.assert_not_called()


def test_error_reappearing_before_confirmation_prevents_resume():
    sockets,monitors,clock,states=recovery_fixture()
    original=monitors["right"].poll.side_effect
    def poll():
        if clock.now>10.1:states["right"]["code"]=0x70
        original()
    monitors["right"].poll.side_effect=poll
    with pytest.raises(RuntimeError,match="单次清状态后夹爪仍未恢复"):
        g.clear_gripper_latches(sockets,monitors,clock)
    assert sockets["right"].sendall.call_count==1


def test_disabled_or_unrepresentable_hold_refuses_clear():
    for code,opening in [(0x30,200),(0x70,85000)]:
        sockets,monitors,clock,states=recovery_fixture(code,opening=opening)
        with pytest.raises(RuntimeError):g.clear_gripper_latches(sockets,monitors,clock)
        for sock in sockets.values():sock.sendall.assert_not_called()


def test_normal_grippers_do_not_receive_clear_frames():
    sockets,monitors,clock,states=recovery_fixture(0x40)
    g.clear_gripper_latches(sockets,monitors,clock)
    for sock in sockets.values():sock.sendall.assert_not_called()


def test_full_reinit_clears_right_once_before_either_gripper_opens():
    sockets,monitors,clock,states=recovery_fixture()
    g.run_cycle(sockets,monitors,clock)
    assert sockets["left"].sendall.call_args_list==[call(g.command_frame(g.OPEN_UM)),call(g.command_frame(0))]
    assert sockets["right"].sendall.call_args_list==[call(g.command_frame(200,clear_error=True)),call(g.command_frame(g.OPEN_UM)),call(g.command_frame(0))]
    assert all(state["opening"]==0 for state in states.values())


def test_full_reinit_stops_before_opening_if_clear_does_not_work():
    sockets,monitors,clock,states=recovery_fixture(persistent=True)
    with pytest.raises(RuntimeError,match="单次清状态后夹爪仍未恢复"):
        g.run_cycle(sockets,monitors,clock)
    sockets["left"].sendall.assert_not_called()
    sockets["right"].sendall.assert_called_once_with(g.command_frame(200,clear_error=True))


@pytest.mark.parametrize("operation",[0x01,0x02])
def test_recovery_frame_holds_opening_without_zero(operation):
    cid,n,data=struct.unpack("=IB3x8s",g.recovery_frame(40600,operation))
    assert cid==0x159 and n==8
    assert struct.unpack(">iHBB",data)==(40600,1000,operation,0)


def one_gripper_recovery_fixture(code=0x22,persistent=False):
    clock=Clock();clock.now=10.0;opening=40600
    state={"code":code,"opening":opening};events=[]
    sock=Mock();monitor=Mock();monitor.clock=clock;monitor.label="右前夹爪"
    monitor.check_arm.return_value=(0,)*6
    def poll():clock.now+=.01
    def fresh(cid,since=None):
        assert cid==0x2a8
        if since is not None and clock.now<=since:return None
        return struct.pack(">iHBB",state["opening"],20,state["code"],0)
    def send(raw):
        cid,n,data=struct.unpack("=IB3x8s",raw)
        target,effort,operation,zero=struct.unpack(">iHBB",data)
        assert cid==0x159 and target==opening and effort==1000 and zero==0
        events.append(operation)
        if not persistent:
            state["code"]=0 if operation==0x02 else 0x40
    monitor.poll.side_effect=poll;monitor.fresh.side_effect=fresh;sock.sendall.side_effect=send
    return sock,monitor,clock,state,events


def test_disabled_temperature_error_recovers_once_at_measured_opening():
    sock,monitor,clock,state,events=one_gripper_recovery_fixture()
    result=g.recover_one_gripper(sock,monitor,clock)
    assert events==[0x02,0x01]
    assert result["enabled"] and result["error_flags"]==[]
    assert result["opening_mm"]==pytest.approx(40.6)
    assert monitor.anchor==(0,)*6


def test_persistent_overtemperature_stops_after_one_vendor_sequence():
    sock,monitor,clock,state,events=one_gripper_recovery_fixture(persistent=True)
    with pytest.raises(RuntimeError,match="电机过温仍存在.*不循环重试"):
        g.recover_one_gripper(sock,monitor,clock)
    assert events==[0x02,0x01]


def test_healthy_gripper_recovery_is_idempotent():
    sock,monitor,clock,state,events=one_gripper_recovery_fixture(code=0x40)
    result=g.recover_one_gripper(sock,monitor,clock)
    assert result["enabled"] and events==[]
    sock.sendall.assert_not_called()


def test_gripper_recovery_refuses_before_command_when_arm_is_unhealthy():
    sock,monitor,clock,state,events=one_gripper_recovery_fixture()
    monitor.check_arm.side_effect=RuntimeError("arm unhealthy")
    with pytest.raises(RuntimeError,match="arm unhealthy"):
        g.recover_one_gripper(sock,monitor,clock)
    assert events==[]

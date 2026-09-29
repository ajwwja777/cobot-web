import copy
import numpy as np
import pytest
from cobot_console.device_health import DeviceHealth, ARMS
from cobot_console.device_control import _classify_arm_feedback
from capture_core.ros_cache import LatestMessageCache

def fixture():
    arm=dict(fresh=True,phase="ready",ctrl_mode=0,teach_status=0,arm_status=0,error_code=0,
             protected_joints=[],enabled_joints=6,gripper={"error_bits":0},detail="6/6")
    systems=dict(can={"phase":"ready"},roscore={"phase":"ready"},arm_nodes={k:True for k in ARMS},
                 control_routes={side:{"ready":True} for side in ("left","right")},
                 arms_feedback={k:copy.deepcopy(arm) for k in ARMS})
    for key in ("rear-left","rear-right"):
        systems["arms_feedback"][key].update(rear_mode="idle_disabled",enabled_joints=0)
    cache=LatestMessageCache()
    for key,value in [("handover_fault",""),("handover_mode","policy")]:
        cache.put(key,value,10,10)
    return systems,cache

def test_enabled_standby_feedback_is_green_without_motion():
    frame={0x2a1:bytes(8),0x2a8:bytes([0,0,0,0,0,0,0x40,0])}
    for i in range(0x261,0x267):frame[i]=bytes([0,0,0,0,0,0x40,0,0])
    value=_classify_arm_feedback("can_left",frame)
    assert value["phase"]=="ready" and value["enabled_joints"]==6
    systems,cache=fixture()
    health=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert all(health[k]["phase"]=="ready" for k in ARMS)

@pytest.mark.parametrize("issue",["can","ros","node","front_teach","route","hardware","disabled"])
def test_faults_remain_yellow_and_have_bilingual_remedies(issue):
    systems,cache=fixture()
    if issue=="can":systems["can"]["phase"]="offline"
    elif issue=="ros":systems["roscore"]["phase"]="offline"
    elif issue=="node":systems["arm_nodes"]["front-left"]=False
    elif issue=="route":systems["control_routes"]["left"]["ready"]=False
    elif issue=="front_teach":systems["arms_feedback"]["front-left"]["teach_status"]=2
    elif issue=="hardware":systems["arms_feedback"]["front-left"]["error_code"]=4
    elif issue=="disabled":systems["arms_feedback"]["front-left"]["enabled_joints"]=5
    health=DeviceHealth().evaluate(systems,cache.snapshot(10))["front-left"]
    assert health["code"]==issue and health["phase"]=="warning"
    assert health["remedy_en"] and health["remedy_zh"]

def teach(systems,cache,side="left",stamp=10):
    systems["arms_feedback"]["rear-"+side].update(rear_mode="teaching",enabled_joints=6)
    for key,value in [("handover_mode","manual:"+side),("teach_"+side,True),
                      ("front_"+side,{"position":np.zeros(7)}),
                      ("rear_"+side,{"position":np.ones(7)}),
                      ("coordinator_"+side,{"position":np.zeros(7)})]:
        cache.put(key,value,stamp,stamp)

def test_blue_requires_fresh_actual_command_tracking_not_equal_absolute_rear_pose():
    systems,cache=fixture();teach(systems,cache)
    health=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert all(health[k]["phase"]=="teaching" for k in ("front-left","rear-left","gripper-left"))
    assert health["front-right"]["phase"]=="ready"
    cache.put("coordinator_left",{"position":np.ones(7)},10,10)
    health=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert health["front-left"]["code"]=="sync" and health["rear-left"]["phase"]=="warning"

def test_stale_feedback_cannot_show_blue_and_gripper_fault_overrides_parent():
    systems,cache=fixture();teach(systems,cache)
    health=DeviceHealth().evaluate(systems,cache.snapshot(11))
    assert health["front-left"]["phase"]=="warning"
    systems["arms_feedback"]["front-left"]["gripper"]["error_bits"]=1
    health=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert health["front-left"]["phase"]=="teaching"
    assert health["gripper-left"]["code"]=="gripper" and health["gripper-left"]["phase"]=="warning"

def test_rear_release_must_disable_but_explicit_later_home_is_allowed():
    systems,cache=fixture();observer=DeviceHealth();teach(systems,cache)
    observer.evaluate(systems,cache.snapshot(10),now=10)
    systems["arms_feedback"]["rear-left"]["rear_mode"]="can_holding"
    cache.put("teach_left",False,10,10)
    observer.evaluate(systems,cache.snapshot(10),now=11)
    assert observer.evaluate(systems,cache.snapshot(10),now=13)["rear-left"]["code"]=="rear_release"
    assert observer.evaluate(systems,cache.snapshot(10),now=14,home_started=13.5)["rear-left"]["phase"]=="ready"

@pytest.mark.parametrize("joint_count", [0, 5])
def test_partial_joint_feedback_never_confirms_teaching_sync(joint_count):
    systems, cache = fixture()
    teach(systems, cache)
    cache.put("front_left", {"position": np.zeros(joint_count)}, 10, 10)
    health = DeviceHealth().evaluate(systems, cache.snapshot(10))
    assert health["front-left"]["code"] == "sync"
    assert health["rear-left"]["phase"] == "warning"


@pytest.mark.parametrize("side", ["left", "right"])
def test_wire_teach_start_becomes_blue_and_exit_residue_does_not(side):
    systems,cache=fixture();teach(systems,cache,side)
    frame={0x2a1:bytes([2,0,0,1,0,0,0,0]),0x2a8:bytes(8)}
    for i in range(0x261,0x267):frame[i]=bytes([0,0,0,0,0,0x40,0,0])
    systems["arms_feedback"]["rear-"+side]=_classify_arm_feedback("can_rear_"+side,frame)
    h=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert all(h[n+"-"+side]["phase"]=="teaching" for n in ("front","rear","gripper"))
    frame[0x2a1]=bytes([2,0,0,2,0,0,0,0])
    systems["arms_feedback"]["rear-"+side]=_classify_arm_feedback("can_rear_"+side,frame)
    h=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert all(h[n+"-"+side]["phase"]!="teaching" for n in ("front","rear","gripper"))


def test_tx_stall_with_fresh_rx_is_reported_on_both_paired_arms():
    systems,cache=fixture();teach(systems,cache)
    systems["can_tx"]={"front-left":{"phase":"error","detail":"can_left TX queue 10"}}
    h=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert h["front-left"]["code"]=="can_tx" and "发送队列堵塞" in h["front-left"]["reason_zh"]
    assert h["gripper-left"]["code"]=="can_tx"
    assert h["rear-left"]["paired_code"]=="can_tx" and "发送队列堵塞" in h["rear-left"]["reason_zh"]
    assert h["front-right"]["phase"]=="ready"


def test_paired_fault_does_not_overwrite_front_raw_feedback():
    systems,cache=fixture()
    systems["arms_feedback"]["front-left"]["detail"]="FRONT mode 1"
    systems["arms_feedback"]["rear-left"].update(arm_status=5,detail="REAR mode 2")
    h=DeviceHealth().evaluate(systems,cache.snapshot(10))["front-left"]
    assert h["code"]=="pair" and h["detail"]=="FRONT mode 1"
    assert h["paired_code"]=="hardware" and "保护" in h["reason_zh"]


@pytest.mark.parametrize("issue",["teach_button","routing","stale","tracking","joints"])
def test_sync_diagnostic_names_the_failed_stage(issue):
    systems,cache=fixture();teach(systems,cache)
    if issue=="teach_button":cache.put("teach_left",False,10,10)
    if issue=="routing":cache.put("handover_mode","policy",10,10)
    if issue=="stale":cache.put("coordinator_left",{"position":np.zeros(7)},8,8)
    if issue=="tracking":cache.put("coordinator_left",{"position":np.ones(7)},10,10)
    if issue=="joints":cache.put("front_left",{"position":[float("nan")]*7},10,10)
    observer=DeviceHealth()
    observer.evaluate(systems,cache.snapshot(10),now=10)
    h=observer.evaluate(systems,cache.snapshot(10),now=11.1)["front-left"]
    assert h["sync_issue"]==issue and h["phase"]=="warning"
    assert h["reason_en"] and h["remedy_en"] and h["reason_zh"] and h["remedy_zh"]
    if issue=="tracking":assert h["max_joint_error"]==1.
    if issue=="stale":assert "coordinator_left" in h["reason_zh"]


def test_rear_teach_requires_all_motors_enabled():
    systems,cache=fixture();teach(systems,cache)
    systems["arms_feedback"]["rear-left"]["enabled_joints"]=5
    h=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert h["rear-left"]["code"]=="disabled"
    assert all(h[n+"-left"]["phase"]!="teaching" for n in ("front","rear","gripper"))


def test_unchanged_latched_coordinator_state_is_not_a_stale_heartbeat():
    systems,cache=fixture();teach(systems,cache)
    cache.put("handover_mode","manual:left",1,1)
    cache.put("handover_fault","",1,1)
    health=DeviceHealth().evaluate(systems,cache.snapshot(10))
    assert all(health[n+"-left"]["phase"]=="teaching" for n in ("front","rear","gripper"))


def moving_health(observer,systems,cache,at,error=0):
    teach(systems,cache,stamp=at)
    cache.put("coordinator_left",{"position":np.ones(7)*error},at,at)
    return observer.evaluate(systems,cache.snapshot(at),now=at)


def test_takeover_ros_can_skew_stays_blue_with_honest_transition_label():
    systems,cache=fixture();observer=DeviceHealth()
    teach(systems,cache)
    # Button/coordinator are live while the slower CAN poll still says idle.
    systems["arms_feedback"]["rear-left"].update(rear_mode="idle_disabled",enabled_joints=0)
    h=observer.evaluate(systems,cache.snapshot(10),now=10)
    assert all(h[n+"-left"]["phase"]=="teaching" for n in ("front","rear","gripper"))
    assert h["front-left"]["code"]=="teach_transition" and not h["front-left"]["sync_confirmed"]
    assert h["front-left"]["reason_zh"]=="示教接管中"
    h=moving_health(observer,systems,cache,10.6)
    assert h["front-left"]["code"]=="teaching" and h["front-left"]["sync_confirmed"]


def test_missing_takeover_confirmation_eventually_turns_yellow():
    systems,cache=fixture();observer=DeviceHealth()
    teach(systems,cache)
    systems["arms_feedback"]["rear-left"].update(rear_mode="idle_disabled",enabled_joints=0)
    assert observer.evaluate(systems,cache.snapshot(10),now=10)["front-left"]["phase"]=="teaching"
    h=observer.evaluate(systems,cache.snapshot(10),now=11.05)
    assert h["front-left"]["phase"]=="warning" and h["front-left"]["sync_issue"]=="teach_feedback"


def test_moving_brief_lag_never_flickers_but_continuous_lag_warns_and_recovers_stably():
    systems,cache=fixture();observer=DeviceHealth()
    moving_health(observer,systems,cache,10)
    for at,error in [(10.1,.16),(10.3,.20),(10.5,.14),(10.7,.18),(10.9,.13)]:
        h=moving_health(observer,systems,cache,at,error)
        assert all(h[n+"-left"]["phase"]=="teaching" for n in ("front","rear","gripper"))
        assert h["front-left"]["max_joint_error"]==pytest.approx(error)
    assert moving_health(observer,systems,cache,11,.2)["front-left"]["phase"]=="teaching"
    h=moving_health(observer,systems,cache,11.81,.2)
    assert h["front-left"]["code"]=="sync" and h["front-left"]["phase"]=="warning"
    # Hysteresis: touching just below the warning threshold is not recovery.
    assert moving_health(observer,systems,cache,12,.12)["front-left"]["phase"]=="warning"
    assert moving_health(observer,systems,cache,12.1,.09)["front-left"]["code"]=="sync_recovering"
    assert moving_health(observer,systems,cache,12.4,.08)["front-left"]["phase"]=="warning"
    assert moving_health(observer,systems,cache,12.65,.08)["front-left"]["phase"]=="teaching"


def test_short_stale_sample_preserves_blue_but_missing_stream_still_warns():
    systems,cache=fixture();observer=DeviceHealth()
    moving_health(observer,systems,cache,10)
    assert observer.evaluate(systems,cache.snapshot(10.3),now=10.3)["front-left"]["phase"]=="teaching"
    assert observer.evaluate(systems,cache.snapshot(10.8),now=10.8)["front-left"]["phase"]=="warning"


@pytest.mark.parametrize("fault",["front_teach","can_tx","hardware","disabled","node","route","ros","gripper","severe_tracking"])
def test_real_faults_bypass_smoothing_even_during_a_transient(fault):
    systems,cache=fixture();observer=DeviceHealth()
    moving_health(observer,systems,cache,10)
    moving_health(observer,systems,cache,10.1,.17)
    if fault=="front_teach":systems["arms_feedback"]["front-left"]["ctrl_mode"]=2
    elif fault=="can_tx":systems["can_tx"]={"front-left":{"phase":"error","detail":"TX stalled"}}
    elif fault=="hardware":systems["arms_feedback"]["front-left"]["error_code"]=1
    elif fault=="disabled":systems["arms_feedback"]["front-left"]["enabled_joints"]=5
    elif fault=="node":systems["arm_nodes"]["front-left"]=False
    elif fault=="route":systems["control_routes"]["left"]["ready"]=False
    elif fault=="ros":systems["roscore"]["phase"]="offline"
    elif fault=="gripper":systems["arms_feedback"]["front-left"]["gripper"]["error_bits"]=1
    elif fault=="severe_tracking":cache.put("coordinator_left",{"position":np.ones(7)*.51},10.1,10.1)
    h=observer.evaluate(systems,cache.snapshot(10.1),now=10.11)
    key="gripper-left" if fault=="gripper" else "front-left"
    assert h[key]["phase"]=="warning"
    assert h[key]["code"]==("sync" if fault=="severe_tracking" else fault)


def test_teach_exit_clears_debounce_for_next_takeover():
    systems,cache=fixture();observer=DeviceHealth()
    moving_health(observer,systems,cache,10)
    moving_health(observer,systems,cache,10.1,.2)
    assert moving_health(observer,systems,cache,11,.2)["front-left"]["phase"]=="warning"
    systems["arms_feedback"]["rear-left"].update(rear_mode="idle_disabled",enabled_joints=0)
    cache.put("teach_left",False,12,12);cache.put("handover_mode","policy",12,12)
    h=observer.evaluate(systems,cache.snapshot(12),now=12)
    assert h["front-left"]["phase"]=="ready" and "left" not in observer.teach_display
    assert moving_health(observer,systems,cache,13,.16)["front-left"]["code"]=="teach_transition"

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

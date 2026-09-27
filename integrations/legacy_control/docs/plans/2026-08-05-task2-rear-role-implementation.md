# Task2 Rear-Arm Dynamic Role Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a ROS1 driver for the independently connected rear Piper arms that can switch between motion-output (slave) and teaching-input (master) roles, plus a five-arm inference launch that preserves the existing three-arm files.

**Architecture:** Keep the existing `piper_start_ms_node.py` for front-left, front-right, and middle arms. Add a rear-only node that exclusively owns one `can_rear_*` interface, delegates validation/conversion/state transitions to a ROS-independent core, and exposes a `std_srvs/SetBool` role service. Instantiate the node twice from a new launch file.

**Tech Stack:** Python 3.8, ROS1 Noetic (`rospy`, `sensor_msgs`, `std_msgs`, `std_srvs`), `piper_sdk` 0.4.1, XML launch, standard-library `unittest` (pytest is not installed on the controller).

## Global Constraints

- Do not modify `piper_start_ms_node.py` or either existing three-arm launch file.
- Do not invoke `roslaunch`, enable an arm, or send a real motion command during implementation verification.
- The rear driver accepts only `can_rear_left` and `can_rear_right` by default.
- One rear CAN interface has exactly one driver owner.
- `0xFA` means teaching-input/master; `0xFC` means motion-output/slave.
- Invalid, non-finite, short, stale, or master-mode joint commands are rejected.
- All new Task2 files follow the existing Piper ROS and `multi_arm_launch_tools` directory layout.

---

### Task 1: ROS-independent rear-arm core

**Files:**
- Create: `/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/task2_rear_role_core.py`
- Test: `/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/tests/task2/test_rear_role_core.py`

**Interfaces:**
- Produces: `Role`, `validate_can_port(str)`, `normalize_role(str)`, `joint_positions_to_piper(Sequence[float])`, `feedback_to_joint_positions(joint_milli_degrees: Sequence[int], gripper_micrometers: int)`, and `RearRoleController`.
- `RearRoleController.switch_role(role: Role)` calls `MasterSlaveConfig(0xFA|0xFC, 0, 0, 0)` and records the requested role only if no exception is raised.
- `RearRoleController.encode_command(positions, now, stamp)` rejects commands unless role is slave and age is within the configured timeout.

- [x] **Step 1: Write failing validation and conversion tests**

```python
def test_only_rear_can_ports_are_allowed(self):
    self.assertEqual(validate_can_port("can_rear_left"), "can_rear_left")
    with self.assertRaises(ValueError):
        validate_can_port("can_left")

def test_joint_positions_are_encoded_like_original_driver():
    encoded = joint_positions_to_piper([0.1, 0.2, -0.3, 0.4, -0.5, 0.6, 0.04])
    assert encoded[:6] == tuple(round(x * 57324.840764) for x in [0.1, 0.2, -0.3, 0.4, -0.5, 0.6])
    assert encoded[6] == 40000
```

- [x] **Step 2: Run the tests and confirm missing-module failure**

Run: `python -m unittest -v tests.task2.test_rear_role_core`

Expected: FAIL because `task2_rear_role_core.py` does not exist.

- [x] **Step 3: Implement validation, conversion, and state machine**

Implement exact constants:

```python
MASTER_CONFIG = 0xFA
SLAVE_CONFIG = 0xFC
JOINT_RAD_TO_MILLI_DEG = 57324.840764
MAX_GRIPPER_METERS = 0.08
ALLOWED_CAN_PORTS = frozenset(("can_rear_left", "can_rear_right"))
```

Reject booleans, NaN/Inf, fewer than seven positions, negative timestamps, and commands older than `command_timeout_sec`.

- [x] **Step 4: Add role and stale-command tests**

Use a fake Piper object that records `MasterSlaveConfig` calls. Verify master blocks commands, slave accepts fresh commands, and failed SDK calls do not change the recorded role.

- [x] **Step 5: Run tests**

Run: `python -m unittest -v tests.task2.test_rear_role_core`

Expected: all PASS.

- [x] **Step 6: Commit Task 1 only**

```bash
git add tests/task2/test_rear_role_core.py
git commit -m "test: cover task2 rear arm role core"
```

The Piper SDK directory is not a Git repository, so record its checksum in the Teleop commit message or verification report rather than attempting a cross-repository commit.

### Task 2: ROS rear-arm role node

**Files:**
- Create: `/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/piper_rear_role_task2_node.py`
- Create: `/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/tests/task2/test_rear_role_node.py`

**Interfaces:**
- Parameters: `~can_port`, `~initial_role`, `~auto_enable`, `~gripper_exist`, `~publish_rate`, `~command_timeout_sec`.
- Subscribes: `~joint_cmd` (`sensor_msgs/JointState`).
- Publishes: `~joint_states`, `~master_joint`, `~role`, `~arm_status`.
- Service: `~set_master` (`std_srvs/SetBool`), where `true=master` and `false=slave`.

- [x] **Step 1: Write failing node behavior tests**

Inject a fake ROS API and fake Piper factory into `PiperRearRoleNode` and exercise the real node callbacks without a ROS master or CAN device. Verify that `SetBool(data=True)` changes the observable role to master, master mode rejects joint commands, and `SetBool(data=False)` restores slave command handling. The fake Piper mirrors every SDK method used by construction, feedback publishing, role switching, and command output.

- [x] **Step 2: Run the contract test and confirm failure**

Run: `python -m unittest -v tests.task2.test_rear_role_node`

Expected: FAIL because the node does not exist.

- [x] **Step 3: Implement the node**

Implementation requirements:

```text
startup -> validate can_port -> create C_PiperInterface -> ConnectPort
        -> request initial role -> create publishers/subscriber/service

slave transition -> MasterSlaveConfig(0xFC) -> verify standby ctrl_mode=0
                 -> keep motors/motion closed until a fresh command

first fresh slave command -> preload measured joint/gripper pose
                          -> require nonzero-Hz, recent post-CAN feedback
                          -> optionally enable and verify all six motors
                          -> MotionCtrl_2(1,1,100) and verify fresh ctrl_mode=1
                          -> revalidate command timestamp after activation
                          -> JointCtrl(j1..j6) -> GripperCtrl(gripper,1000,1,0)

transition/send failure -> publish fault -> reject all later commands
                         -> require a new verified role request to recover

master publish loop -> GetArmJointCtrl + GetArmGripperCtrl -> ~master_joint
all-role publish loop -> GetArmJointMsgs + GetArmGripperMsgs -> ~joint_states
```

Do not call `DisableArm()` or switch role in shutdown cleanup.

- [x] **Step 4: Run syntax and contract tests**

Run:

```bash
python3 -m py_compile /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/task2_rear_role_core.py
python3 -m py_compile /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/piper_rear_role_task2_node.py
python3 -m unittest -v tests.task2.test_rear_role_node tests.task2.test_rear_role_core
```

Expected: all PASS, with no CAN access.

- [x] **Step 5: Mark ROS node executable**

Run: `chmod 755 /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/piper_rear_role_task2_node.py`

### Task 3: Five-arm inference launch and static verification

**Files:**
- Create: `/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch`
- Create: `/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/tests/task2/test_5arm_launch.py`
- Modify: `/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/docs/2026-08-05-task2-dynamic-rear-arm-design.md`

**Interfaces:**
- Front/middle nodes use existing `piper_start_ms_node.py` with `mode=1`.
- Rear nodes use `piper_rear_role_task2_node.py` with `initial_role=slave`.
- Rear stable node names: `piper_rear_left_task2`, `piper_rear_right_task2`.

- [x] **Step 1: Write failing launch-structure tests**

Parse XML and assert exactly five arm nodes, unique CAN values, expected node types, rear private remaps, and no `can0` arm node.

- [x] **Step 2: Run test and confirm launch-file failure**

Run: `python -m unittest -v tests.task2.test_5arm_launch`

Expected: FAIL because launch file does not exist.

- [x] **Step 3: Create the five-arm launch**

Use arguments `front_mode=1`, `front_auto_enable=true`, `mid_mode=1`, `mid_auto_enable=true`, `rear_initial_role=slave`, and `rear_auto_enable=true`. Remap rear private topics to `/task2/rear_left/*` and `/task2/rear_right/*`.

- [x] **Step 4: Run all offline verification**

Run:

```bash
python3 -m unittest discover -v -s tests/task2 -p 'test_*.py'
python3 -m py_compile /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/task2_rear_role_core.py
python3 -m py_compile /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/piper_rear_role_task2_node.py
python3 - <<'PY'
import xml.etree.ElementTree as ET
ET.parse('multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch')
print('launch XML OK')
PY
```

Expected: all tests pass and `launch XML OK` prints.

- [x] **Step 5: Verify old files are unchanged**

Compare SHA-256 checksums captured before and after implementation for:

```text
piper_start_ms_node.py
start_ms_piper_3arm.launch
start_ms_piper_3arm_collect.launch
can_config_task2.sh
```

- [x] **Step 6: Update design documentation with exact offline test commands**

Document that no hardware validation has occurred and do not provide a live launch command as verified until the separate physical test phase.

- [x] **Step 7: Commit Teleop files only**

```bash
git add multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch \
        multi_arm_launch_tools/docs/2026-08-05-task2-dynamic-rear-arm-design.md \
        tests/task2
git commit -m "feat: add task2 five-arm driver launch"
```

# Task2 Physical-Teach Handover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent ROS1 Task2 deployment in which both rear Piper arms remain permanent slaves, physical teach buttons enable human takeover, and M/S safely route control between policy and human feedback.

**Architecture:** A fixed-slave rear driver owns each rear CAN interface and publishes stable physical-teach state from fresh `0x2A*` feedback. A separate event-driven coordinator reuses the existing paired-command validation and π0.5 pause protocol, but never calls `MasterSlaveConfig()` during M/S. New `teach` launch, keyboard, installer, preparation tool, tests, and runbook leave the existing dynamic-role and three-arm paths unchanged.

**Tech Stack:** Python 3.8, ROS1 Noetic (`rospy`), `piper_sdk 0.4.1`, `python-can`, `sensor_msgs/JointState`, `piper_msgs/PiperStatusMsg`, `std_srvs`, XML roslaunch, `unittest`, Bash.

## Global Constraints

- Runtime code must never call `MasterSlaveConfig(0xFA)`.
- `MasterSlaveConfig(0xFC)` exists only in the explicit one-time preparation tool and requires a rear-arm power cycle.
- All new runtime files and ROS names use `teach`; existing role-switch files retain their behavior.
- Policy and manual commands are mutually exclusive and left/right messages are forwarded only as validated pairs.
- Feedback maximum age is `0.10 s`; command maximum age is `0.25 s`; pair skew is `0.02 s`.
- Joint synchronization tolerance is `0.05 rad`; gripper synchronization tolerance is `0.015 m`.
- Physical teach enter/exit must be stable for `0.20 s` before acceptance.
- Offline work must not launch CAN, enable motors, switch roles, or move arms.
- All intermediary files on Cobot live under `/home/agilex/cobot_magic/task3/jiaan`; tracked source remains in the existing Teleop repository.

---

### Task 1: Fixed-Slave Rear Teach Driver

**Files:**
- Create: `multi_arm_launch_tools/task2_teach_driver/task2_rear_teach_core.py`
- Create: `multi_arm_launch_tools/task2_teach_driver/piper_rear_teach_task2_node.py`
- Create: `tests/task2/test_rear_teach_core.py`
- Create: `tests/task2/test_rear_teach_node.py`

**Interfaces:**
- Produces: `TeachStateTracker.update(ctrl_mode: int, teach_status: int, stamp_sec: float, now_sec: float) -> Optional[bool]`.
- Produces: `RearTeachCommandGate.encode(positions, now_sec, stamp_sec) -> Tuple[int, ...]`.
- Publishes private ROS topics `~joint_states`, `~arm_status`, `~teach_active`, `~motion_ready`, and `~fault`.
- Subscribes to private ROS topic `~joint_cmd`.

- [ ] **Step 1: Write failing core tests**

Add tests proving that teach state is unknown before a 200 ms stable window, becomes true only for `ctrl_mode=0x02` plus `teach_status=0x01`, becomes false after a stable exit, rejects stale status, and closes the command gate while teach is active or unknown.

```python
def test_teach_requires_stable_hardware_status(self):
    tracker = TeachStateTracker(stable_sec=0.20, feedback_timeout_sec=0.10)
    self.assertIsNone(tracker.update(0x02, 0x01, 1.00, 1.00))
    self.assertIsNone(tracker.update(0x02, 0x01, 1.10, 1.10))
    self.assertTrue(tracker.update(0x02, 0x01, 1.21, 1.21))

def test_command_gate_rejects_teach_and_unknown(self):
    gate = RearTeachCommandGate(command_timeout_sec=0.25)
    for teach_active in (None, True):
        gate.set_teach_active(teach_active)
        with self.assertRaises(TeachCommandBlocked):
            gate.encode([0.0] * 7, now_sec=2.0, stamp_sec=2.0)
```

- [ ] **Step 2: Run core tests and verify RED**

Run:

```bash
python -m unittest tests.task2.test_rear_teach_core
```

Expected: import failure because `task2_rear_teach_core.py` does not exist.

- [ ] **Step 3: Implement the ROS-independent core**

Implement exact constants and fail-closed transitions:

```python
TEACH_CTRL_MODE = 0x02
TEACH_START_RECORDING = 0x01

class TeachStateTracker:
    def update(self, ctrl_mode, teach_status, stamp_sec, now_sec):
        fresh = 0.0 <= now_sec - stamp_sec <= self.feedback_timeout_sec
        if not fresh:
            self._stable_value = None
            return None
        candidate = (
            int(ctrl_mode) == TEACH_CTRL_MODE
            and int(teach_status) == TEACH_START_RECORDING
        )
        # Reset the candidate start time on every edge; publish only after
        # candidate remains unchanged for stable_sec.
```

Reuse `joint_positions_to_piper`, `feedback_to_joint_positions`, parameter parsing, and CAN allow-list validation from `task2_rear_role_core.py`; do not duplicate conversion constants.

- [ ] **Step 4: Run core tests and verify GREEN**

Run the command from Step 2. Expected: all tests pass.

- [ ] **Step 5: Write failing node tests**

Create a `FakePiper` and `FakeRos` following `test_rear_role_node.py`. Assert:

```python
def test_startup_never_writes_master_slave_flash(self):
    node, _, piper = make_node()
    self.assertEqual(piper.master_slave_calls, [])

def test_teach_mode_blocks_position_and_enable_calls(self):
    node, ros, piper = make_node()
    piper.status.ctrl_mode = 0x02
    piper.status.teach_status = 0x01
    settle_feedback(node, seconds=0.21)
    node.joint_command_callback(fresh_command())
    self.assertEqual(piper.joint_calls, [])
    self.assertEqual(piper.enable_calls, [])
    self.assertTrue(ros.publishers["~teach_active"].messages[-1].data)
```

Also test stale feedback, malformed commands, exit-teach hold activation, `auto_enable=false`, `auto_enable=true`, and hardware-send failure latching `~fault`.

- [ ] **Step 6: Run node tests and verify RED**

Run:

```bash
python -m unittest tests.task2.test_rear_teach_node
```

Expected: missing production node failure.

- [ ] **Step 7: Implement the fixed-slave ROS node**

Use `C_PiperInterface`, but startup performs only `ConnectPort()` plus feedback verification. Do not call `MasterSlaveConfig`. Publish actual joints from `GetArmJointMsgs()` and gripper from `GetArmGripperMsgs()`. In `joint_command_callback`, acquire one hardware lock, validate fresh command, preload current feedback, optionally enable, enter `MotionCtrl_2(0x01, 0x01, 100)`, verify fresh `ctrl_mode=0x01`, set `motion_ready=true`, then issue the still-fresh command.

- [ ] **Step 8: Run driver tests and commit**

Run:

```bash
python -m unittest tests.task2.test_rear_teach_core tests.task2.test_rear_teach_node
```

Expected: pass.

Commit only Task 1 files:

```bash
git add multi_arm_launch_tools/task2_teach_driver tests/task2/test_rear_teach_core.py tests/task2/test_rear_teach_node.py
git commit -m "feat: add task2 fixed-slave teach driver"
```

---

### Task 2: Event-Driven Physical-Teach Handover Core

**Files:**
- Create: `multi_arm_launch_tools/task2_teach_handover/task2_teach_handover_core.py`
- Create: `tests/task2/test_teach_handover_core.py`

**Interfaces:**
- Consumes: `ValidatedJoint`, `PairBuffer`, `validate_joint`, and `arms_are_synchronized` from `task2_handover_core.py`.
- Produces: `TeachMode`, `TeachHandoverState`, `begin_manual()`, `observe_teach_pair()`, `begin_policy()`, `observe_teach_exit_pair()`, `complete_policy_arm()`, and `fault(reason)`.

- [ ] **Step 1: Write failing state-machine tests**

Cover the exact transition graph and illegal transitions:

```python
def test_manual_waits_for_both_physical_buttons(self):
    state = TeachHandoverState()
    state.begin_policy()
    state.complete_policy_arm()
    state.begin_manual()
    self.assertEqual(state.mode, TeachMode.WAITING_TEACH_ENTER)
    state.observe_teach_pair(left=True, right=False)
    self.assertEqual(state.mode, TeachMode.WAITING_TEACH_ENTER)
    state.observe_teach_pair(left=True, right=True)
    self.assertEqual(state.mode, TeachMode.MANUAL)
```

Test POLICY unexpected-teach fault, MANUAL unexpected-exit fault, S waiting for both exits, reset returning only to PAUSED, and stale deadline fault.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.task2.test_teach_handover_core
```

Expected: missing module failure.

- [ ] **Step 3: Implement the minimal state core**

Use the exact enum:

```python
class TeachMode(Enum):
    PAUSED = "paused"
    ARMING_POLICY = "arming_policy"
    POLICY = "policy"
    WAITING_TEACH_ENTER = "waiting_teach_enter"
    MANUAL = "manual"
    WAITING_TEACH_EXIT = "waiting_teach_exit"
    RESUMING = "resuming"
    FAULT = "fault"
```

Keep ROS calls outside the core. Core methods change state only after validating current mode and both-side physical status.

- [ ] **Step 4: Run tests, verify GREEN, and commit**

```bash
python -m unittest tests.task2.test_teach_handover_core
git add multi_arm_launch_tools/task2_teach_handover/task2_teach_handover_core.py tests/task2/test_teach_handover_core.py
git commit -m "feat: add task2 physical-teach handover state"
```

---

### Task 3: ROS Teach Handover Coordinator

**Files:**
- Create: `multi_arm_launch_tools/task2_teach_handover/task2_teach_handover_node.py`
- Create: `tests/task2/test_teach_handover_node.py`

**Interfaces:**
- Consumes rear `joint_states`, `teach_active`, `motion_ready`, `fault`; front feedback; paired policy commands; `/task2/policy/set_paused`.
- Produces front `/master/joint_left,right`, rear teach `joint_cmd`, mode/prompt/fault topics, and manual/policy/reset services.

- [ ] **Step 1: Write failing coordinator tests**

Use fake publishers, services, and a controllable monotonic clock. Assert ordering, not only final state:

```python
def test_m_closes_gate_before_pause_and_returns_waiting(self):
    node = make_policy_node_with_synchronized_feedback()
    response = node.handle_request_manual(TriggerRequest())
    self.assertTrue(response.success)
    self.assertEqual(node.mode, "waiting_teach_enter")
    self.assertEqual(node.events[:2], ["gate_closed", "policy_pause_true"])
    self.assertEqual(node.front_left_pub.messages, [])

def test_manual_opens_only_after_fresh_bilateral_teach(self):
    node = make_waiting_manual_node()
    node.left_teach_callback(Bool(data=True))
    self.assertNotEqual(node.mode, "manual")
    node.right_teach_callback(Bool(data=True))
    self.assertEqual(node.mode, "manual")
```

Test S before/after physical exit, hold ordering, fresh resume cutoff, pair skew, stale feedback, synchronization rejection, unexpected teach in POLICY, unexpected exit in MANUAL, driver fault, and timeout watchdog.

- [ ] **Step 2: Run node tests and verify RED**

```bash
python -m unittest tests.task2.test_teach_handover_node
```

Expected: missing module failure.

- [ ] **Step 3: Implement subscriptions, publishers, and event-driven transitions**

`request_manual` and `request_policy` return after safely entering their waiting states; they do not block a ROS service thread while the operator presses buttons. Teach callbacks call one locked `_advance_from_teach_state()` method. A 20 Hz timer enforces enter/exit deadlines and feedback age. Pair buffers are cleared at every control-source boundary.

Required prompts are exact strings:

```python
PRESS_BOTH_TO_ENTER = "PRESS BOTH REAR TEACH BUTTONS TO ENTER"
MANUAL_ACTIVE = "MANUAL ACTIVE"
PRESS_BOTH_TO_EXIT = "PRESS BOTH REAR TEACH BUTTONS TO EXIT"
WAITING_FRESH_POLICY = "WAITING FOR FRESH POLICY"
```

- [ ] **Step 4: Run coordinator tests and regression tests**

```bash
python -m unittest tests.task2.test_teach_handover_node
python -m unittest tests.task2.test_handover_core tests.task2.test_handover_node
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add multi_arm_launch_tools/task2_teach_handover/task2_teach_handover_node.py tests/task2/test_teach_handover_node.py
git commit -m "feat: add task2 physical-teach coordinator"
```

---

### Task 4: Keyboard, Launch Files, and Deterministic Installation

**Files:**
- Create: `multi_arm_launch_tools/task2_teach_handover/task2_teach_handover_keyboard.py`
- Create: `multi_arm_launch_tools/launch/start_ms_piper_5arm_teach_task2.launch`
- Create: `multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_teach_task2.launch`
- Create: `multi_arm_launch_tools/install_task2_teach.sh`
- Create: `tests/task2/test_teach_handover_keyboard.py`
- Create: `tests/task2/test_teach_launch.py`
- Create: `tests/task2/test_teach_driver_source_sync.py`

**Interfaces:**
- Keyboard calls only `/task2/teach_handover/request_manual`, `/request_policy`, and subscribes to mode/prompt/fault.
- Launch remaps rear private topics into `/task2/teach/rear_left,right/*` and starts the teach coordinator.

- [ ] **Step 1: Write failing keyboard, launch, and installer tests**

Assert M/S/Q mapping, exact five-arm CAN assignments, default `rear_auto_enable=true`, explicit override support, absence of dynamic-role node/service names, and `--check` byte/mode equality.

```python
def test_teach_launch_never_starts_dynamic_role_driver(self):
    root = parse_launch(TEACH_LAUNCH)
    types = [node.attrib["type"] for node in root.iter("node")]
    self.assertIn("piper_rear_teach_task2_node.py", types)
    self.assertNotIn("piper_rear_role_task2_node.py", types)
```

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.task2.test_teach_handover_keyboard tests.task2.test_teach_launch tests.task2.test_teach_driver_source_sync
```

- [ ] **Step 3: Implement keyboard and launch files**

Keyboard uses termios single-character input. M/S service calls return immediately with waiting-state messages, while prompt topic updates remain visible. Q exits only the keyboard process.

The driver launch starts front left/right, middle, and two fixed-slave rear nodes. The combined launch includes it and starts `task2_teach_handover_node.py` with the fixed thresholds from Global Constraints.

- [ ] **Step 4: Implement installer**

`install_task2_teach.sh --install|--check` copies core/node/keyboard files to the live Piper scripts directory with modes `0644/0755`, and reuses the existing dependency helper. It must not install or overwrite the dynamic-role files.

- [ ] **Step 5: Run tests, parse launch, and commit**

```bash
python -m unittest tests.task2.test_teach_handover_keyboard tests.task2.test_teach_launch tests.task2.test_teach_driver_source_sync
roslaunch --nodes multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_teach_task2.launch front_auto_enable:=false mid_auto_enable:=false rear_auto_enable:=false
git add multi_arm_launch_tools/task2_teach_handover/task2_teach_handover_keyboard.py multi_arm_launch_tools/launch/start_ms_piper_5arm_teach_task2.launch multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_teach_task2.launch multi_arm_launch_tools/install_task2_teach.sh tests/task2/test_teach_handover_keyboard.py tests/task2/test_teach_launch.py tests/task2/test_teach_driver_source_sync.py
git commit -m "feat: launch task2 physical-teach handover"
```

---

### Task 5: One-Time Slave Preparation and Staged Runbook

**Files:**
- Create: `multi_arm_launch_tools/prepare_task2_rear_slaves.py`
- Create: `tests/task2/test_prepare_task2_rear_slaves.py`
- Create: `multi_arm_launch_tools/docs/2026-08-05-task2-physical-teach-handover-runbook.md`

**Interfaces:**
- Preparation tool accepts only `--can can_rear_left` or `--can can_rear_right`; no `--all` option prevents accidental simultaneous maintenance.
- Tool sends exactly one `MasterSlaveConfig(0xFC, 0, 0, 0)`, prints mandatory power-cycle instructions, and never calls enable/motion/joint/gripper APIs.

- [ ] **Step 1: Write failing preparation-tool tests**

```python
def test_prepare_sends_only_slave_flash_command(self):
    piper = FakePiper()
    prepare_rear_slave(piper)
    self.assertEqual(piper.master_slave_calls, [(0xFC, 0, 0, 0)])
    self.assertEqual(piper.motion_calls, [])
    self.assertEqual(piper.enable_calls, [])
```

Test CAN allow-list rejection and exact warning text requiring at least five seconds of power-off.

- [ ] **Step 2: Run test and verify RED**

```bash
python -m unittest tests.task2.test_prepare_task2_rear_slaves
```

- [ ] **Step 3: Implement the maintenance tool**

Separate pure `prepare_rear_slave(piper)` from CLI parsing so tests do not touch hardware. CLI refuses to run if a matching ROS rear driver node is active, connects only to the explicitly selected CAN port, sends FC once, disconnects, and exits without claiming the change is active.

- [ ] **Step 4: Write the staged runbook**

Document one-time preparation, normal terminal layout, M/S physical-button sequence, exact inspection topics, eight acceptance stages, fail-closed recovery, and explicit prohibition on bypassing teach state or calling the old `set_master` services.

- [ ] **Step 5: Test and commit**

```bash
python -m unittest tests.task2.test_prepare_task2_rear_slaves
git diff --check -- multi_arm_launch_tools/prepare_task2_rear_slaves.py tests/task2/test_prepare_task2_rear_slaves.py multi_arm_launch_tools/docs/2026-08-05-task2-physical-teach-handover-runbook.md
git add multi_arm_launch_tools/prepare_task2_rear_slaves.py tests/task2/test_prepare_task2_rear_slaves.py multi_arm_launch_tools/docs/2026-08-05-task2-physical-teach-handover-runbook.md
git commit -m "docs: add task2 physical-teach runbook"
```

---

### Task 6: Full Verification, Live-Source Installation, and Stage-1 Handoff

**Files:**
- Modify only if tests require: new teach files from Tasks 1-5.
- Install copies into: `/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/`.

**Interfaces:**
- Produces a verified offline package and a Stage-1 command with all auto-enable parameters false.

- [ ] **Step 1: Run the complete Task2 suite**

```bash
python -m unittest discover -s tests/task2 -p 'test_*.py'
```

Expected: zero failures and zero errors, including all pre-existing role-switch tests.

- [ ] **Step 2: Compile every new Python file**

```bash
python -m py_compile \
  multi_arm_launch_tools/task2_teach_driver/*.py \
  multi_arm_launch_tools/task2_teach_handover/*.py \
  multi_arm_launch_tools/prepare_task2_rear_slaves.py
```

- [ ] **Step 3: Install and verify byte-for-byte source sync**

```bash
multi_arm_launch_tools/install_task2_teach.sh --install
multi_arm_launch_tools/install_task2_teach.sh --check
```

Expected: every new teach source reports `OK`; no old file is listed as a destination.

- [ ] **Step 4: Parse the combined launch without starting hardware**

```bash
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch --nodes \
  multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_teach_task2.launch \
  front_auto_enable:=false mid_auto_enable:=false rear_auto_enable:=false
```

Expected nodes: three existing front/mid Piper nodes, two `piper_rear_teach_task2_node.py`, and one `task2_teach_handover_node.py`.

- [ ] **Step 5: Audit the diff and commit verification-only corrections**

```bash
git diff --check
git status --short
git log --oneline -6
```

Do not stage unrelated existing changes. If verification required no correction, create no empty commit.

- [ ] **Step 6: Hand off Stage 1 only**

After the operator completes the one-time FC + physical power-cycle preparation, launch only:

```bash
roslaunch \
  /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_teach_task2.launch \
  front_auto_enable:=false \
  mid_auto_enable:=false \
  rear_auto_enable:=false
```

Verify `paused`, both `teach_active=false`, four front/rear joint topics near 200 Hz, and no physical motion. Do not proceed to a button press until Stage 1 evidence is recorded.

# Task2 M/S Handover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed ROS1 coordinator that switches Cobot between four-arm policy mirroring and rear-master human takeover, then adapt local π0.5 so every S recovery discards the old action chunk and performs a fresh inference.

**Architecture:** Keep the existing five hardware drivers as the only CAN owners. Add a model-independent coordinator between policy command topics and the four arm command topics, plus a foreground keyboard service client. Add a separate π0.5 Task2 entrypoint that imports the existing inference implementation, implements the standard pause/resume service, and never publishes around the coordinator.

**Tech Stack:** Python 3.8, ROS1 Noetic (`rospy`, `sensor_msgs`, `std_msgs`, `std_srvs`), standard-library `unittest`, XML roslaunch, Bash installers and deployment entrypoints.

## Global Constraints

- All Cobot generated runtime files stay under existing project roots; transfer-machine staging stays under `/data/LFT-W02_data/jiaan`.
- Do not start roslaunch, open CAN, enable an arm, switch a real role, or send a real movement command during implementation verification.
- Preserve existing `piper_start_ms_node.py`, three-arm launch files, `can_config_task2.sh`, original π0.5 `inference_pi05.py`, and existing non-Task2 deployment scripts byte-for-byte.
- The coordinator is the sole publisher to `/master/joint_left`, `/master/joint_right`, `/task2/rear_left/joint_cmd`, and `/task2/rear_right/joint_cmd` during Task2 operation.
- All unsafe failures latch `FAULT` and publish no further arm commands until an explicit software fault reset and a new mode request.
- Left/right commands are forwarded only as validated pairs; maximum pair skew is `0.02 s`, command age is `0.25 s`, joint sync tolerance is `0.05 rad`, gripper sync tolerance is `0.015 m`, and actual-state age is `0.10 s`.
- A Task2 policy client starts paused. `SetBool(true)` pauses and invalidates its current generation; `SetBool(false)` clears observation queues and begins a new generation that must use post-resume observations.
- Every behavior change follows RED → GREEN → REFACTOR and each task ends with a focused commit.

---

## File Map

**Modify**

- `multi_arm_launch_tools/task2_driver/piper_rear_role_task2_node.py`: publish latched motion readiness.
- `multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch`: expose rear motion-ready topics.
- `multi_arm_launch_tools/install_task2_driver.sh`: continue installing the changed rear node.
- `tests/task2/test_rear_role_node.py`: motion-ready driver contract.
- `tests/task2/test_5arm_launch.py`: motion-ready remap contract.

**Create**

- `multi_arm_launch_tools/task2_handover/task2_handover_core.py`: pure validation, pairing, synchronization, and mode state.
- `multi_arm_launch_tools/task2_handover/task2_handover_node.py`: ROS coordinator.
- `multi_arm_launch_tools/task2_handover/task2_handover_keyboard.py`: foreground M/S/Q client.
- `multi_arm_launch_tools/task2_policy_adapters/pi05/inference_pi05_task2.py`: π0.5 pause-aware client.
- `multi_arm_launch_tools/task2_policy_adapters/pi05/inference_pi05_task2.sh`: existing server lifecycle plus Task2 client.
- `multi_arm_launch_tools/task2_policy_adapters/pi05/run_checkpoint_task2.sh`: checkpoint selector.
- `multi_arm_launch_tools/task2_policy_adapters/pi05/interface_task2_live.sh`: policy process and keyboard orchestration.
- `multi_arm_launch_tools/install_task2_handover.sh`: reproducible runtime installer/checker.
- `multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch`: five drivers plus coordinator.
- `tests/task2/test_handover_core.py`
- `tests/task2/test_handover_node.py`
- `tests/task2/test_handover_keyboard.py`
- `tests/task2/test_pi05_task2_adapter.py`
- `tests/task2/test_handover_launch.py`
- `tests/task2/test_handover_source_sync.py`

---

### Task 1: Rear-arm motion-ready acknowledgement

**Files:**
- Modify: `multi_arm_launch_tools/task2_driver/piper_rear_role_task2_node.py`
- Modify: `/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/piper_rear_role_task2_node.py`
- Modify: `multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch`
- Modify: `tests/task2/test_rear_role_node.py`
- Modify: `tests/task2/test_5arm_launch.py`

**Interfaces:**
- Produces: private latched publisher `~motion_ready: std_msgs/Bool`.
- Contract: false before/through role transitions and after every fault; true only after the first fresh slave command preloads current feedback, verifies enable state, and confirms `ctrl_mode == 0x01`.

- [ ] **Step 1: Write failing driver tests**

Add Bool stubs and assertions equivalent to:

```python
def test_motion_ready_is_false_until_first_fresh_slave_command(self):
    node, ros, _factory = self.make_node()
    self.assertFalse(ros.publishers["~motion_ready"].messages[-1].data)
    node.joint_command_callback(make_command())
    self.assertTrue(ros.publishers["~motion_ready"].messages[-1].data)

def test_role_transition_and_send_fault_clear_motion_ready(self):
    node, ros, factory = self.make_node()
    node.joint_command_callback(make_command())
    self.assertTrue(ros.publishers["~motion_ready"].messages[-1].data)
    node.handle_set_master(SimpleNamespace(data=True))
    self.assertFalse(ros.publishers["~motion_ready"].messages[-1].data)
    factory.instances[0].role_failure = RuntimeError("send failed")
    node.handle_set_master(SimpleNamespace(data=False))
    self.assertFalse(ros.publishers["~motion_ready"].messages[-1].data)
```

Update the launch test to require:

```python
self.assertEqual(remaps["~motion_ready"], prefix + "/motion_ready")
```

- [ ] **Step 2: Run RED tests**

Run:

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_rear_role_node \
  tests.task2.test_5arm_launch
```

Expected: FAIL because the publisher and remaps do not exist.

- [ ] **Step 3: Implement readiness publication**

Import `Bool`, construct a latched publisher before `_apply_role()`, and centralize state updates:

```python
def _set_motion_ready(self, ready: bool) -> None:
    self._motion_ready = bool(ready)
    self.motion_ready_pub.publish(Bool(data=self._motion_ready))
```

Call `_set_motion_ready(False)` before every role request and in every exception path. Call `_set_motion_ready(True)` only after fresh feedback, enable verification, and fresh `ctrl_mode == 0x01` confirmation. Remap each private topic to `/task2/rear_left/motion_ready` and `/task2/rear_right/motion_ready`.

- [ ] **Step 4: Sync the tracked and live driver, then run GREEN tests**

```bash
./multi_arm_launch_tools/install_task2_driver.sh --install
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_rear_role_node \
  tests.task2.test_5arm_launch \
  tests.task2.test_task2_driver_source_sync
```

Expected: all selected tests PASS without a ROS master or CAN access.

- [ ] **Step 5: Commit Task 1**

```bash
git add multi_arm_launch_tools/task2_driver/piper_rear_role_task2_node.py \
        multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch \
        tests/task2/test_rear_role_node.py \
        tests/task2/test_5arm_launch.py
git commit -m "feat: expose task2 rear motion readiness"
```

---

### Task 2: Pure handover safety core

**Files:**
- Create: `multi_arm_launch_tools/task2_handover/task2_handover_core.py`
- Create: `tests/task2/test_handover_core.py`

**Interfaces:**
- Produces: `Mode`, `ValidatedJoint`, `CommandPair`, `PairBuffer`, `HandoverState`, `validate_joint()`, and `arms_are_synchronized()`.
- Consumes later: ROS node converts JointState fields into `validate_joint()` arguments and delegates every mode transition to `HandoverState`.

- [ ] **Step 1: Write failing validation and pairing tests**

Define the intended interface in tests:

```python
left = core.validate_joint(
    names=[f"joint{i}" for i in range(7)],
    positions=[0.0] * 7,
    stamp_sec=10.0,
    arrival_monotonic=50.0,
    now_wall=10.1,
    now_monotonic=50.1,
    max_age_sec=0.25,
)
pairer = core.PairBuffer(max_skew_sec=0.02, max_age_sec=0.25)
self.assertIsNone(pairer.offer("left", left, now_monotonic=50.1))
pair = pairer.offer("right", left, now_monotonic=50.1)
self.assertEqual(pair.left.positions, (0.0,) * 7)
```

Also require rejection of incorrect names, lengths, booleans, NaN/Inf, future timestamps, stale wall time, stale callback age, duplicate side messages, skew over 20 ms, and reusing an already-consumed message.

- [ ] **Step 2: Write failing state and synchronization tests**

```python
state = core.HandoverState()
self.assertIs(state.mode, core.Mode.PAUSED)
state.begin_manual()
self.assertIs(state.mode, core.Mode.TO_MANUAL)
state.complete_manual(policy_pause_confirmed=True)
self.assertIs(state.mode, core.Mode.MANUAL)
state.begin_policy()
self.assertIs(state.mode, core.Mode.TO_POLICY)
state.begin_resuming()
self.assertIs(state.mode, core.Mode.RESUMING)
state.complete_policy()
self.assertIs(state.mode, core.Mode.POLICY)
state.fault("right rear role failed")
self.assertIs(state.mode, core.Mode.FAULT)
```

Test `arms_are_synchronized()` with six joint differences at `0.05 rad`, gripper difference at `0.015 m`, and strict rejection above either threshold or when any feedback age exceeds `0.10 s`.

- [ ] **Step 3: Run RED tests**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_handover_core
```

Expected: FAIL because `task2_handover_core.py` is absent.

- [ ] **Step 4: Implement the pure core**

Use immutable tuples for commands and an explicit enum:

```python
class Mode(Enum):
    PAUSED = "paused"
    ARMING = "arming"
    POLICY = "policy"
    TO_MANUAL = "to_manual"
    MANUAL = "manual"
    TO_POLICY = "to_policy"
    RESUMING = "resuming"
    FAULT = "fault"

@dataclass(frozen=True)
class ValidatedJoint:
    positions: Tuple[float, ...]
    stamp_sec: float
    arrival_monotonic: float

@dataclass(frozen=True)
class CommandPair:
    left: ValidatedJoint
    right: ValidatedJoint
```

`HandoverState` must reject illegal transitions with `TransitionError`; `fault()` is legal from every state; `reset_fault()` is legal only from FAULT and returns to PAUSED. The pure module must not import rospy or ROS message classes.

- [ ] **Step 5: Run GREEN tests and commit**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_handover_core
git add multi_arm_launch_tools/task2_handover/task2_handover_core.py \
        tests/task2/test_handover_core.py
git commit -m "feat: add task2 handover safety core"
```

---

### Task 3: ROS coordinator and fail-closed service sequencing

**Files:**
- Create: `multi_arm_launch_tools/task2_handover/task2_handover_node.py`
- Create: `tests/task2/test_handover_node.py`

**Interfaces:**
- Consumes: Task 2 core; rear role and motion-ready interfaces from Task 1.
- Produces: the exact ROS topics/services in sections 5–9 of the design document.

- [ ] **Step 1: Build fake ROS, publisher, service proxy, clock, and message fixtures**

The test fixture must inject `ros_api`, `service_factory`, `wall_clock`, and `monotonic_clock` into `Task2HandoverNode`, so tests never contact ROS or CAN. Record every external call and every published topic in a single ordered event list.

- [ ] **Step 2: Write failing POLICY routing tests**

Require that PAUSED publishes nothing; `request_policy` performs rear-slave requests, validates four-arm sync, sends rear-only hold, waits for both motion-ready acknowledgements, resumes policy, enters RESUMING, and only the first post-resume command pair opens four outputs:

```python
self.assertEqual(events[-4:], [
    ("publish", "/master/joint_left"),
    ("publish", "/master/joint_right"),
    ("publish", "/task2/rear_left/joint_cmd"),
    ("publish", "/task2/rear_right/joint_cmd"),
])
self.assertEqual(node.mode.value, "policy")
```

Assert that a single-side policy message, pre-resume timestamp, stale pair, invalid names, service failure, missing feedback, sync mismatch, or motion-ready timeout never publishes to any arm and latches FAULT.

- [ ] **Step 3: Write failing MANUAL routing tests**

Require the event order `gate closed -> policy pause -> sync check -> rear-left master -> rear-right master -> MANUAL`. Verify that policy pause timeout still permits MANUAL but records `policy_pause_confirmed=False`; verify that a later S must re-confirm pause before any slave request. In MANUAL, only paired rear master messages reach the two front topics and rear command topics remain silent.

- [ ] **Step 4: Run RED node tests**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_handover_node
```

Expected: FAIL because the ROS adapter is absent.

- [ ] **Step 5: Implement the coordinator**

Construct all publishers before subscribers/services, use one `threading.RLock` for mode transitions and routing, and keep service callbacks synchronous. Required methods:

```python
def handle_request_manual(self, request) -> TriggerResponse: ...
def handle_request_policy(self, request) -> TriggerResponse: ...
def handle_reset_fault(self, request) -> TriggerResponse: ...
def policy_left_callback(self, message: JointState) -> None: ...
def policy_right_callback(self, message: JointState) -> None: ...
def rear_left_master_callback(self, message: JointState) -> None: ...
def rear_right_master_callback(self, message: JointState) -> None: ...
```

Publish mode and fault as latched strings. On any exception after a gate closes, call a single `_latch_fault(reason)` that clears both pair buffers and never publishes a hold or movement command.

- [ ] **Step 6: Run GREEN node/core tests and commit**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_handover_core \
  tests.task2.test_handover_node
git add multi_arm_launch_tools/task2_handover/task2_handover_node.py \
        tests/task2/test_handover_node.py
git commit -m "feat: coordinate task2 policy and manual control"
```

---

### Task 4: Foreground single-key controller

**Files:**
- Create: `multi_arm_launch_tools/task2_handover/task2_handover_keyboard.py`
- Create: `tests/task2/test_handover_keyboard.py`

**Interfaces:**
- Calls: `/task2/handover/request_manual` and `/task2/handover/request_policy` Trigger services.
- Does not publish arm commands or call rear role services directly.

- [ ] **Step 1: Write failing key dispatch tests**

```python
self.assertEqual(module.command_for_key("m"), "manual")
self.assertEqual(module.command_for_key("M"), "manual")
self.assertEqual(module.command_for_key("s"), "policy")
self.assertEqual(module.command_for_key("q"), "quit")
self.assertIsNone(module.command_for_key("x"))
```

Inject a fake `read_key` iterator and fake service calls; verify no Enter is required, unknown keys do nothing, Q invokes no ROS service, and a failed response is printed but does not retry automatically.

- [ ] **Step 2: Run RED test, implement, and run GREEN**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_handover_keyboard
```

Expected RED: module missing. Implement `termios`/`tty.setcbreak` inside a context manager that always restores terminal settings in `finally`, then rerun for PASS.

- [ ] **Step 3: Commit Task 4**

```bash
git add multi_arm_launch_tools/task2_handover/task2_handover_keyboard.py \
        tests/task2/test_handover_keyboard.py
git commit -m "feat: add task2 single-key handover client"
```

---

### Task 5: π0.5 pause-aware Task2 adapter

**Files:**
- Create: `multi_arm_launch_tools/task2_policy_adapters/pi05/inference_pi05_task2.py`
- Create: `tests/task2/test_pi05_task2_adapter.py`

**Interfaces:**
- Imports the deployed sibling module as `import inference_pi05 as base`.
- Provides `/task2/policy/set_paused: std_srvs/SetBool`.
- Publishes paired JointState only to `/task2/policy/joint_left` and `/task2/policy/joint_right`.

- [ ] **Step 1: Write failing adapter lifecycle tests**

Install stub modules for numpy, rospy, `inference_pi05`, and websocket policy before importing the adapter. Require:

```python
adapter = Task2RosInterface(args, ros_api=fake_ros)
self.assertTrue(adapter.paused)
self.assertEqual(adapter.generation, 0)
resume = adapter.handle_set_paused(SimpleNamespace(data=False))
self.assertTrue(resume.success)
self.assertFalse(adapter.paused)
self.assertEqual(adapter.generation, 1)
pause = adapter.handle_set_paused(SimpleNamespace(data=True))
self.assertTrue(pause.success)
self.assertTrue(adapter.paused)
```

Verify pause clears remaining chunk state; resume clears all five observation queues and `last_command`; left/right messages share one nonzero stamp; no publisher targets `/master/joint_left/right`; inference returning after a generation change is discarded; and a pause during action execution drops all remaining actions.

- [ ] **Step 2: Run RED test**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_pi05_task2_adapter
```

Expected: FAIL because the Task2 adapter is absent.

- [ ] **Step 3: Implement adapter with generation invalidation**

Subclass the existing RosInterface and expose thread-safe state:

```python
class Task2RosInterface(base.RosInterface):
    def __init__(self, args, ros_api=rospy):
        super().__init__(args)
        self._task2_lock = threading.RLock()
        self._paused = True
        self._generation = 0

    def handle_set_paused(self, request):
        with self._task2_lock, self.lock:
            self._generation += 1
            self._paused = bool(request.data)
            self.front_images.clear()
            self.left_images.clear()
            self.right_images.clear()
            self.left_joints.clear()
            self.right_joints.clear()
            self.last_command = None
        return SetBoolResponse(success=True, message="paused" if request.data else "fresh resume")
```

The main loop captures `generation` before infer and before each action; any mismatch or paused state discards the result. Create one `stamp = rospy.Time.now()` per action and assign it to both JointState headers.

- [ ] **Step 4: Run GREEN adapter tests and commit**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_pi05_task2_adapter
git add multi_arm_launch_tools/task2_policy_adapters/pi05/inference_pi05_task2.py \
        tests/task2/test_pi05_task2_adapter.py
git commit -m "feat: add pause-aware pi05 task2 adapter"
```

---

### Task 6: Launch, installers, and direct π0.5 Task2 entrypoint

**Files:**
- Create: `multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch`
- Create: `multi_arm_launch_tools/install_task2_handover.sh`
- Create: `multi_arm_launch_tools/task2_policy_adapters/pi05/inference_pi05_task2.sh`
- Create: `multi_arm_launch_tools/task2_policy_adapters/pi05/run_checkpoint_task2.sh`
- Create: `multi_arm_launch_tools/task2_policy_adapters/pi05/interface_task2_live.sh`
- Create: `tests/task2/test_handover_launch.py`
- Create: `tests/task2/test_handover_source_sync.py`

**Interfaces:**
- Installs three coordinator scripts into the Piper package scripts directory.
- Installs the π0.5 adapter and three Task2 shell entrypoints without replacing original deployment files.
- Final entrypoint: `./interface_task2_live.sh <checkpoint-step>`.

- [ ] **Step 1: Write failing launch and installer tests**

Require the new launch to include `start_ms_piper_5arm_task2.launch`, start exactly one `task2_handover_node.py`, pass all thresholds explicitly, and never start the interactive keyboard. Require `--check` to compare every tracked/runtime file and fail on a modified destination. Use temporary target roots to prove `--install` is idempotent and preserves sentinel original files.

- [ ] **Step 2: Write failing shell contract tests**

Run entrypoints with no args, invalid step, and missing coordinator service under a temporary PATH containing fake `rosservice`, `rosrun`, and process commands. Require exit code 2 for bad CLI, fail-closed exit before the policy starts if coordinator services are missing, and these fixed Task2 topics:

```bash
PUPPET_ARM_LEFT_CMD_TOPIC=/task2/policy/joint_left
PUPPET_ARM_RIGHT_CMD_TOPIC=/task2/policy/joint_right
```

Require `interface_task2_live.sh` to start the paused policy client, wait for `/task2/policy/set_paused`, call `/task2/handover/request_policy`, then keep `task2_handover_keyboard.py` in the foreground. Its trap terminates only the policy child; it never switches roles or disables arms during cleanup.

- [ ] **Step 3: Run RED tests**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_handover_launch \
  tests.task2.test_handover_source_sync
```

Expected: FAIL because launch, installer, and scripts are absent.

- [ ] **Step 4: Implement launch and deterministic installer**

The installer usage is:

```text
install_task2_handover.sh --check
install_task2_handover.sh --install
```

Use `install -m 0644` for pure modules and `install -m 0755` for ROS/shell entrypoints. Destination defaults are:

```text
PIPER_PACKAGE_DIR=/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper
PI05_DEPLOYMENT_DIR=/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05
```

No recursive copy and no overwrite outside the exact new Task2 filenames.

- [ ] **Step 5: Implement shell entrypoints and run GREEN tests**

`run_checkpoint_task2.sh` validates `<step>`, exports the in-the-pot config/asset/prompt, and execs `common/inference_pi05_task2.sh`. `inference_pi05_task2.sh` preserves the current server lifecycle but invokes `robot/inference_pi05_task2.py`. `interface_task2_live.sh` owns the policy child and foreground keyboard as specified above.

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_handover_launch \
  tests.task2.test_handover_source_sync
```

- [ ] **Step 6: Install, check, parse launch, and commit**

```bash
./multi_arm_launch_tools/install_task2_handover.sh --install
./multi_arm_launch_tools/install_task2_handover.sh --check
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch --nodes \
  multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch
git add multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch \
        multi_arm_launch_tools/install_task2_handover.sh \
        multi_arm_launch_tools/task2_policy_adapters/pi05 \
        tests/task2/test_handover_launch.py \
        tests/task2/test_handover_source_sync.py
git commit -m "feat: package task2 pi05 handover deployment"
```

`roslaunch --nodes` is parse-only; it must list five arm drivers and one coordinator without opening CAN.

---

### Task 7: Full offline verification and operator runbook

**Files:**
- Modify: `multi_arm_launch_tools/docs/2026-08-05-task2-handover-design.md`
- Create: `multi_arm_launch_tools/docs/2026-08-05-task2-handover-runbook.md`

**Interfaces:**
- Produces the exact terminal layout and staged hardware checklist; it does not authorize live execution during this implementation turn.

- [ ] **Step 1: Run the complete Task2 unit suite**

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest discover -v \
  -s tests/task2 -p 'test_*.py'
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run compile, sync, dependency, and launch parsing checks**

```bash
/home/agilex/miniconda3/envs/aloha/bin/python -m py_compile \
  multi_arm_launch_tools/task2_handover/task2_handover_core.py \
  multi_arm_launch_tools/task2_handover/task2_handover_node.py \
  multi_arm_launch_tools/task2_handover/task2_handover_keyboard.py \
  multi_arm_launch_tools/task2_policy_adapters/pi05/inference_pi05_task2.py
./multi_arm_launch_tools/install_task2_driver.sh --check
./multi_arm_launch_tools/install_task2_handover.sh --check
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch --nodes \
  multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch
```

Expected: all commands exit 0; launch parsing lists no keyboard node.

- [ ] **Step 3: Verify protected legacy hashes and scoped Git diff**

Compare fresh SHA-256 values for:

```text
Piper ROS piper_start_ms_node.py
start_ms_piper_3arm.launch
start_ms_piper_3arm_collect.launch
can_config_task2.sh
π0.5 common/robot/inference_pi05.py
π0.5 interface_live.sh
π0.5 run_checkpoint.sh
```

The first four must equal the recorded Task 1 baselines; record π0.5 baselines before installing Task2 files and verify they remain equal. Inspect `git diff --check` and exclude all unrelated dirty files.

- [ ] **Step 4: Write the staged runbook**

Document separate terminals for CAN setup, five-arm handover launch, cameras, middle camera, and `interface_task2_live.sh 2000`. Mark stages 1–8 from the design as individually gated and explicitly state: “Do not proceed to the next stage after any unexpected movement, role mismatch, stale feedback, or FAULT.”

- [ ] **Step 5: Request focused safety review, fix Critical/Important findings with new RED tests, and rerun Steps 1–3**

The review scope is command-source exclusivity, role-service partial failure, stale/zero timestamps, pause during infer/chunk execution, rear hold readiness, cleanup behavior, and preservation of old deployment paths.

- [ ] **Step 6: Commit documentation only**

```bash
git add multi_arm_launch_tools/docs/2026-08-05-task2-handover-design.md \
        multi_arm_launch_tools/docs/2026-08-05-task2-handover-runbook.md
git commit -m "docs: add task2 handover staged runbook"
```

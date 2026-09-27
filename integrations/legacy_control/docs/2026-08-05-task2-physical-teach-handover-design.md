# Task2 固定从臂 + 物理示教按钮接管设计

## 1. 背景与目标

Cobot Magic 的前后四条 Piper 已分别接入独立 USB-CAN。现有动态角色方案尝试在
运行中使用 `MasterSlaveConfig(0xFA/0xFC)` 完成后臂主从切换，但实机固件
`S-V1.7-3` 在 `master -> slave` 后要求机械臂重启，无法满足反复按 `M/S` 的
无重启接管流程。

本方案改用官方推荐的“从臂联动示教模式”：后双臂永久保持运动输出臂身份，
推理时接受 policy 命令；人工接管时由操作员按后臂物理示教按钮进入拖动示教，
ROS 读取后臂实际关节反馈并控制前双臂。退出示教后，协调器重新同步四臂并恢复
基于最新观测的模型推理。

目标操作流程固定为：

```text
M -> 按左右后臂物理示教按钮 -> 人工遥操
S -> 再按左右后臂物理示教按钮 -> 自动恢复推理
```

本方案不修改模型权重，不替换已有采集、三臂推理和动态角色方案；新增文件使用
独立的 `teach` 名称。

## 2. 不可变安全约束

1. 后双臂只允许使用从臂身份；运行时禁止调用
   `MasterSlaveConfig(0xFA)`。
2. `MasterSlaveConfig(0xFC)` 只允许作为一次性准备步骤执行，之后必须按 SDK 要求
   给后双臂断电重启。正常启动与 M/S 流程不再写 Flash。
3. policy、人工反馈和任何测试源不得同时发布前臂命令。
4. 左右命令必须成对、同一代次、同一控制源转发；缺任一侧时四臂均不发布。
5. 示教状态、反馈新鲜度、四臂同步或模型暂停任一项不能确认时 fail closed。
6. FAULT 不自动切模式、不使能、不回零；只关闭协调器软件命令门。
7. 中臂仍由现有 `puppet/joint_mid` 路径控制，不参与左右接管。

## 3. 一次性硬件准备

在新方案第一次实机启动前，扶住五条臂并停止全部机械臂节点。分别向左右后臂
发送一次 `MasterSlaveConfig(0xFC, 0, 0, 0)`，随后真正断电至少 5 秒并重新
上电。准备工具只负责写入从臂身份，不启动运动。

重新上电后，运行时节点只通过新鲜 `0x2A*` 反馈确认：

```text
ctrl_mode != LINKAGE_TEACHING_INPUT_MODE(0x06)
GetArmStatus().Hz > 0
GetArmJointMsgs().Hz > 0
```

若检测到主臂控制帧 `0x155/0x156/0x157` 或缺少从臂反馈，启动失败，不尝试
自动修复角色。

## 4. 架构与数据流

### 4.1 POLICY

```text
policy left/right
        |
        v
task2_teach_handover_node
   |                    |
   +--> front left/right
   +--> rear left/right fixed-slave command
```

模型左右动作由协调器同时送往前后四臂，使接管前后臂姿态一致。

### 4.2 MANUAL

```text
rear left/right GetArmJointMsgs
        |
        v
task2_teach_handover_node
        |
        +--> front left/right
```

后臂处于物理示教模式时不接受位置命令。协调器只读取后臂实际关节和夹爪反馈，
不使用主臂专用的 `GetArmJointCtrl()`。

## 5. 文件边界

新增并由 Teleop 仓库追踪：

```text
multi_arm_launch_tools/task2_teach_driver/
  task2_rear_teach_core.py
  piper_rear_teach_task2_node.py

multi_arm_launch_tools/task2_teach_handover/
  task2_teach_handover_core.py
  task2_teach_handover_node.py
  task2_teach_handover_keyboard.py

multi_arm_launch_tools/launch/
  start_ms_piper_5arm_handover_teach_task2.launch

multi_arm_launch_tools/
  prepare_task2_rear_slaves.py
  install_task2_teach_driver.sh

multi_arm_launch_tools/docs/
  2026-08-05-task2-physical-teach-handover-runbook.md
```

`prepare_task2_rear_slaves.py` 是显式的一次性维护工具，不被 launch 或部署脚本
自动调用。运行时驱动安装到现有 ROS1 `piper/scripts` 目录；源文件和安装目标继续
使用 `--check` 保证一致。

现有以下文件保持原行为：

```text
piper_rear_role_task2_node.py
task2_handover_node.py
start_ms_piper_5arm_handover_task2.launch
原三臂采集/推理 launch
```

## 6. 后臂固定从臂驱动

每条后臂由一个 `piper_rear_teach_task2_node.py` 独占对应 CAN 接口。节点：

- 启动时只读取并验证从臂反馈，不调用 `MasterSlaveConfig()`；
- 发布 `joint_states`、`arm_status`、`teach_active` 和 `motion_ready`；
- 在非示教状态接受协调器的 `joint_cmd`；
- 进入示教状态后立即关闭命令接收门，不向后臂发送位置、模式或使能指令；
- 退出示教后保持 `motion_ready=false`，直到收到新鲜 hold，完成当前姿态预装、
  位置模式确认；仅当 ROS 参数 `auto_enable=true` 时才使能并验证六个电机；
- 任意 CAN/反馈/命令异常时发布 fault 并拒绝后续命令。

示教状态判定必须来自新鲜硬件状态：

```text
ctrl_mode == 0x02 (TEACHING_MODE)
且 teach_status == 0x01 (START_RECORDING)
```

退出判定要求新鲜状态不再满足上述组合；只收到单帧变化不够，需连续确认一个
短稳定窗口。具体默认值为 200 ms，并作为 ROS 参数暴露。

## 7. 协调器状态机

新状态机独立于动态角色协调器：

```text
PAUSED
  -> ARMING_POLICY -> POLICY
POLICY
  -> WAITING_TEACH_ENTER -> MANUAL
MANUAL
  -> WAITING_TEACH_EXIT -> ARMING_POLICY -> RESUMING -> POLICY
任意状态
  -> FAULT
```

### 7.1 M：进入人工接管

1. 原子关闭 policy 输出门并进入 `WAITING_TEACH_ENTER`；
2. 调用 `/task2/policy/set_paused(true)`，客户端清空剩余 action chunk；
3. 验证前后对应臂关节差和夹爪差在阈值内；
4. 发布提示，等待操作员按下两条后臂物理示教按钮；
5. 两侧 `teach_active=true` 且反馈新鲜后，再次验证四臂同步；
6. 清空旧后臂反馈配对缓存；
7. 进入 MANUAL，只把之后产生的成对后臂 `joint_states` 转发给前双臂。

M 不调用任何后臂角色或模式服务。任意一侧在超时内未进入示教，保持所有命令门
关闭并进入 FAULT。

### 7.2 S：恢复模型

1. 立即关闭人工转发门并进入 `WAITING_TEACH_EXIT`；
2. 保持 policy 暂停，提示操作员再次按下两侧物理按钮；
3. 等待两侧示教状态退出且稳定；
4. 验证前后姿态仍同步；
5. 将当前前臂实测姿态作为一对 hold，只发给后双臂；
6. 等待两侧 `motion_ready=true`；
7. 再次验证四臂同步；
8. 调用 policy fresh resume，清空观测和动作缓存；
9. 只接受恢复时间之后的新鲜左右 policy 动作对；
10. 第一对新动作同时发给前后四臂并进入 POLICY。

如果操作员先退出物理示教、随后再按 S，步骤 2 可立即通过；如果先按 S，则协调器
安全等待物理退出。两种顺序都不允许人工或 policy 在等待阶段控制前臂。

## 8. ROS 接口

固定从臂驱动发布：

```text
/task2/teach/rear_left/joint_states       sensor_msgs/JointState
/task2/teach/rear_right/joint_states      sensor_msgs/JointState
/task2/teach/rear_left/arm_status         piper_msgs/PiperStatusMsg
/task2/teach/rear_right/arm_status        piper_msgs/PiperStatusMsg
/task2/teach/rear_left/teach_active       std_msgs/Bool (latched)
/task2/teach/rear_right/teach_active      std_msgs/Bool (latched)
/task2/teach/rear_left/motion_ready       std_msgs/Bool (latched)
/task2/teach/rear_right/motion_ready      std_msgs/Bool (latched)
/task2/teach/rear_left/fault              std_msgs/String (latched)
/task2/teach/rear_right/fault             std_msgs/String (latched)
```

固定从臂驱动订阅：

```text
/task2/teach/rear_left/joint_cmd          sensor_msgs/JointState
/task2/teach/rear_right/joint_cmd         sensor_msgs/JointState
```

协调器继续复用标准 policy 输入和前臂输出：

```text
/task2/policy/joint_left,right
/master/joint_left,right
/task2/policy/set_paused
```

新增协调器状态：

```text
/task2/teach_handover/mode
/task2/teach_handover/prompt
/task2/teach_handover/fault
/task2/teach_handover/request_manual
/task2/teach_handover/request_policy
/task2/teach_handover/reset_fault
```

## 9. 反馈、配对与同步阈值

沿用现有经过测试的约束：

- 七个 joint 名称和位置必须完整且有限；
- 左右消息时间戳差不超过 20 ms；
- 命令和反馈年龄不超过 250 ms；
- 进入 MANUAL/POLICY 前反馈年龄不超过 100 ms；
- 六关节最大差不超过 0.05 rad；
- 夹爪差不超过 0.015 m；
- 每条消息只消费一次；
- 单侧缺失时不发布任何前后臂命令。

实机阶段可在更严格值下验证；未经记录不得放宽阈值。

## 10. 异常处理

以下情况立即关闭全部协调器命令门并进入 FAULT：

- POLICY 中任一后臂意外进入物理示教；
- MANUAL 中任一后臂退出物理示教；
- 左右示教进入/退出不一致或超时；
- 任一前后臂反馈过期、非有限或关节差超限；
- 后臂在示教状态仍报告接受/执行位置命令；
- hold 或 `motion_ready` 超时；
- policy pause/fresh resume 无法确认；
- 节点检测到主臂帧、错误角色或 CAN 故障。

`reset_fault` 只清软件状态并回到 PAUSED。它不会退出物理示教、切换 Piper 身份、
使能、回零或恢复模型；操作员必须先确认物理状态。

## 11. 键盘和页面行为

键盘继续单字符读取，不需要 Enter：

```text
M -> 请求进入 WAITING_TEACH_ENTER
S -> 请求进入 WAITING_TEACH_EXIT/恢复 POLICY
Q -> 仅退出本次键盘客户端
```

终端和未来 Cobot Station 页面均显示 `prompt`：

```text
PRESS BOTH REAR TEACH BUTTONS TO ENTER
MANUAL ACTIVE
PRESS BOTH REAR TEACH BUTTONS TO EXIT
WAITING FOR FRESH POLICY
FAULT: <reason>
```

页面和键盘只调用协调器服务，不能直接发布机械臂命令。

## 12. 测试与分阶段验收

离线测试先覆盖：

- 固定从臂驱动启动时不调用 `MasterSlaveConfig()`；
- 示教硬件状态的进入、稳定、退出和过期判定；
- 示教时拒绝后臂命令；退出后必须经过 hold 才 `motion_ready`；
- M 先关 policy 门再等待按钮，未双侧确认不转发；
- S 先关 manual 门，退出示教前不发送 hold；
- 左右配对、反馈新鲜度、同步阈值和 fail-closed；
- POLICY 中意外进入示教立即故障；
- launch 与驱动安装源一致性；
- 原动态角色和三臂测试保持通过。

实机验收不能跨级：

1. 全部 `auto_enable=false`，只读验证前后四臂约 200 Hz 和 PAUSED；
2. 不启动模型，单独按左后臂按钮，验证 `teach_active`，前臂不动；
3. 左侧退出后重复右侧；
4. 双侧进入/退出示教，只检查状态机，不转发前臂；
5. 低速、短行程 MANUAL，左右前臂分别跟随后臂；
6. 无模型的 hold、`motion_ready` 和四臂重新同步；
7. policy shadow 验证 M/S、清 chunk 和 fresh resume；
8. π0.5 live 完整接管。

每一阶段均要求现场扶住五条臂、急停可及并记录话题、日志和视频。任何异常立即
停止，不通过增加超时、绕过状态或放宽阈值继续。

# Task2 后双臂动态主从驱动设计

## 目标

在保留现有三臂采集、推理流程的前提下，为独立接入
`can_rear_left` 和 `can_rear_right` 的后双臂增加运行时主从角色切换能力。
本阶段只实现驱动与五臂 launch，不接入 M/S 键盘控制，不启动机械臂验证。

## 文件位置

- 新驱动：`Piper_ros_private-ros-noetic/src/piper/scripts/piper_rear_role_task2_node.py`
- 新 launch：`Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch`
- 离线测试：`Piper-AVP-Teleop/tests/task2/test_rear_role_node.py`

不覆盖或修改：

- `piper_start_ms_node.py`
- `start_ms_piper_3arm.launch`
- `start_ms_piper_3arm_collect.launch`
- 已完成的 `can_config_task2.sh`

## 驱动职责

新节点每个进程只打开一个后臂 CAN 接口，并由 launch 启动两个实例。

- `role=slave`：调用 `MasterSlaveConfig(0xFC, 0, 0, 0)`，订阅本后臂命令话题，
  使用原驱动相同的 `MotionCtrl_2`、`JointCtrl` 和 `GripperCtrl` 控制后臂。
- `role=master`：调用 `MasterSlaveConfig(0xFA, 0, 0, 0)`，停止接收运动命令，
  使用 `GetArmJointCtrl()` 和 `GetArmGripperCtrl()` 发布示教输入。
- 始终使用 `GetArmJointMsgs()` 发布后臂实际反馈。
- 通过 ROS service 请求角色切换，并发布当前角色与切换结果。
- 同一个后臂 CAN 不得再由其他 Piper 节点打开和控制。

## 五臂 launch

- 前左、前右、中臂继续使用原 `piper_start_ms_node.py`，均以 `mode=1` 启动。
- 后左、后右使用新 `piper_rear_role_task2_node.py`，部署启动角色默认为 `slave`。
- 前臂命令仍为 `/master/joint_left`、`/master/joint_right`。
- 后臂使用独立命令和反馈话题，避免和前臂冲突：
  - `/task2/rear_left/joint_cmd`
  - `/task2/rear_right/joint_cmd`
  - `/task2/rear_left/joint_states`
  - `/task2/rear_right/joint_states`
  - `/task2/rear_left/master_joint`
  - `/task2/rear_right/master_joint`

本阶段模型尚不会自动复制动作到后臂；该功能由下一阶段的
`task2_handover_node.py` 统一完成。

## 安全约束

- 编写和测试期间不调用 roslaunch，不执行使能，不发送真实运动命令。
- 角色切换失败时保持故障状态，不自动继续发送命令。
- 输入 JointState 长度不足、数值非有限、数据超时均拒绝控制。
- 节点退出时不擅自改变机械臂角色，避免退出过程产生意外运动。
- 首次上机测试必须扶住五个臂，并先只验证 CAN、话题和角色反馈。

## 验收标准

1. Python 语法检查通过。
2. 离线单元测试覆盖参数解析、单位换算、非法命令拒绝、主从状态机。
3. launch XML 可解析，五个节点的 CAN 名称和话题无冲突。
4. 旧三臂 launch 和原驱动文件内容不变。
5. 实机启动必须留到用户确认后的单独步骤。

## 离线实现状态（2026-08-05）

已添加：

- `Piper_ros_private-ros-noetic/src/piper/scripts/task2_rear_role_core.py`
- `Piper_ros_private-ros-noetic/src/piper/scripts/piper_rear_role_task2_node.py`
- `Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch`
- `Piper-AVP-Teleop/multi_arm_launch_tools/task2_driver/task2_rear_role_core.py`
- `Piper-AVP-Teleop/multi_arm_launch_tools/task2_driver/piper_rear_role_task2_node.py`
- `Piper-AVP-Teleop/multi_arm_launch_tools/install_task2_driver.sh`
- `Piper-AVP-Teleop/tests/task2/test_rear_role_core.py`
- `Piper-AVP-Teleop/tests/task2/test_rear_role_node.py`
- `Piper-AVP-Teleop/tests/task2/test_5arm_launch.py`
- `Piper-AVP-Teleop/tests/task2/test_task2_driver_source_sync.py`
- `Piper-AVP-Teleop/tests/task2/test_task2_ros_dependencies.py`

工控机没有安装 pytest，因此测试使用 Python 标准库 `unittest`，不新增环境依赖。

离线验证命令：

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
/home/agilex/miniconda3/envs/aloha/bin/python -m unittest -v \
  tests.task2.test_rear_role_core \
  tests.task2.test_rear_role_node \
  tests.task2.test_5arm_launch

source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch --nodes \
  multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch
```

`roslaunch --nodes` 只解析并列出节点，不启动驱动，不打开 CAN，不使能机械臂。

当前验证范围仅为离线代码、ROS 导入和 launch 解析。尚未进行后臂角色切换、
五臂使能或运动的实机验证。SDK 的 `MasterSlaveConfig()` 没有成功返回值，
所以节点不会依赖其返回值：主臂必须由 `GetArmStatus().ctrl_mode == 0x06`
确认；切回从臂时先由待机反馈 `ctrl_mode == 0x00` 确认，且不会立刻使能或进入
运动模式。只有收到一条通过名称、数值和时间戳检查的新命令后，节点才会先把
当前实测姿态预装为目标，再确认六个电机的 `driver_enable_status`，最后进入
`ctrl_mode == 0x01` 的 CAN/MoveJ 控制并发送新目标。用于确认角色、预装姿态和
确认使能的 SDK 反馈必须有非零频率、有效时间戳且不超过 0.25 秒；角色/运动
模式确认还必须来自本次请求之后的新反馈。切换期间已经进入回调队列的命令、
以及在等待使能期间变旧的命令都会被丢弃；即使 ROS 消息的 header 时间戳为
零，也会用回调到达时的单调时钟严格限制总等待时间。任何切换或发送异常都会
进入 `fault`，继续封锁命令，直到一次新的、经过硬件反馈确认的角色切换成功。

Teleop 仓库中的 `multi_arm_launch_tools/task2_driver/` 是可追踪源码，Piper ROS
包的 `scripts/` 是运行安装位置。检查两处是否一致：

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
./multi_arm_launch_tools/install_task2_driver.sh --check
```

该脚本同时检查/安装两个驱动文件和 `std_srvs` 的 package/CMake 依赖，保证从
Teleop Git 仓库可以复现部署。只有源码或依赖不同步时才执行 `--install`；正常
启动不需要重复安装。

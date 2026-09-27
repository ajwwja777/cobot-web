# Task2 人在环 M/S 接管设计

## 1. 目标与范围

本阶段在已经完成物理 CAN 隔离、后双臂动态角色驱动和五臂 launch 的基础上，
增加一个统一的控制权协调层。操作员在模型推理期间按 `M`，立即停止模型控制并
使用后双臂接管前双臂；按 `S`，安全地把后双臂切回从臂，清空模型旧动作块，
基于最新观测重新推理后恢复四臂同步控制。

第一版只适配 Cobot 本地部署的 π0.5 非 RTC 客户端。协调器和 ROS 接口必须与
模型无关，后续 LingBot V2、Tau0、π0.5+RTC 只需实现相同的暂停/恢复协议。
中间臂继续由现有节点控制相机姿态，不参与左右手控制权切换。

本阶段不修改模型权重、不重新训练、不修改已有三臂 launch 和推理部署脚本的
默认行为，也不在离线实现阶段启动 CAN、使能或运动机械臂。

## 2. 现有接口

现有模型读取：

```text
/puppet/joint_left                 sensor_msgs/JointState
/puppet/joint_right                sensor_msgs/JointState
```

现有模型直接发布：

```text
/master/joint_left                 sensor_msgs/JointState
/master/joint_right                sensor_msgs/JointState
```

现有后臂动态驱动提供：

```text
/task2/rear_left/joint_cmd         sensor_msgs/JointState
/task2/rear_right/joint_cmd        sensor_msgs/JointState
/task2/rear_left/joint_states      sensor_msgs/JointState
/task2/rear_right/joint_states     sensor_msgs/JointState
/task2/rear_left/master_joint      sensor_msgs/JointState
/task2/rear_right/master_joint     sensor_msgs/JointState
/task2/rear_left/role              std_msgs/String
/task2/rear_right/role             std_msgs/String
/task2/rear_left/set_master        std_srvs/SetBool
/task2/rear_right/set_master       std_srvs/SetBool
```

## 3. 架构选择

采用“统一路由器 + 标准模型暂停协议”。模型不再直接发布到前臂，而是发布到
协调器的 policy 输入。协调器是前后四臂命令的唯一软件发布源。

```text
π0.5 / 其他模型
    │ /task2/policy/joint_left,right
    ▼
task2_handover_node
    ├── POLICY  ──> /master/joint_left,right
    │           └─> /task2/rear_left,right/joint_cmd
    │
    └── MANUAL  <── /task2/rear_left,right/master_joint
                └─> /master/joint_left,right
```

不采用“只切 topic”的方案，因为模型会继续消耗旧 action chunk，恢复时不能
保证第一次动作来自最新观测。不采用“结束并重启模型进程”的方案，因为本地
模型重新加载时间长，不满足快速接管恢复要求。

## 4. 文件边界

新增并由 Teleop Git 仓库追踪：

```text
multi_arm_launch_tools/task2_handover/task2_handover_core.py
    ROS 无关的状态机、命令配对、时效和同步检查。

multi_arm_launch_tools/task2_handover/task2_handover_node.py
    ROS1 适配器，订阅/发布话题、调用后臂和模型服务。

multi_arm_launch_tools/task2_handover/task2_handover_keyboard.py
    前台单键控制器，读取 M/S/Q，不要求按 Enter。

multi_arm_launch_tools/task2_policy_adapters/pi05/inference_pi05_task2.py
    复用现有 inference_pi05.py 的 π0.5 Task2 客户端。

multi_arm_launch_tools/install_task2_handover.sh
    把节点和 π0.5 适配器安装到运行目录，并支持 --check。

multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch
    include 已有五臂 launch，增加 handover 节点；键盘程序不由 roslaunch
    启动，避免交互式 stdin 丢失。
```

运行安装位置：

```text
/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/
/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05/common/robot/
```

原 `inference_pi05.py` 保持不变，Task2 部署显式运行新增的
`inference_pi05_task2.py`。

## 5. ROS 接口

### 5.1 协调器订阅

```text
/task2/policy/joint_left           JointState
/task2/policy/joint_right          JointState
/task2/rear_left/master_joint      JointState
/task2/rear_right/master_joint     JointState
/puppet/joint_left                 JointState
/puppet/joint_right                JointState
/task2/rear_left/joint_states      JointState
/task2/rear_right/joint_states     JointState
/task2/rear_left/role              String
/task2/rear_right/role             String
/task2/rear_left/motion_ready      Bool
/task2/rear_right/motion_ready     Bool
```

### 5.2 协调器发布

```text
/master/joint_left                 JointState
/master/joint_right                JointState
/task2/rear_left/joint_cmd         JointState
/task2/rear_right/joint_cmd        JointState
/task2/handover/mode               String，latched
/task2/handover/fault              String，latched
```

`mode` 只允许：

```text
PAUSED -> ARMING -> POLICY -> TO_MANUAL -> MANUAL
MANUAL -> TO_POLICY -> RESUMING -> POLICY
任意状态 -> FAULT
```

### 5.3 协调器服务

```text
/task2/handover/request_manual     std_srvs/Trigger
/task2/handover/request_policy     std_srvs/Trigger
/task2/handover/reset_fault        std_srvs/Trigger
```

协调器调用：

```text
/task2/rear_left/set_master        SetBool(true=master, false=slave)
/task2/rear_right/set_master       SetBool(true=master, false=slave)
/task2/policy/set_paused           SetBool(true=pause, false=fresh resume)
```

`reset_fault` 只清除协调器的软件故障锁，不自动切角色、不使能、不发布命令；
清除后回到 `PAUSED`，操作员必须重新请求 POLICY 或 MANUAL。

## 6. 成对命令约束

左右臂命令必须成对转发，不能因某一侧话题先到而单臂运动。

- JointState 必须恰好包含 `joint0` 至 `joint6` 七个名称和七个有限位置。
- policy 适配器对左右消息使用完全相同的 ROS header 时间戳。
- 协调器只接受时间戳差不超过 20 ms 的左右消息对。
- 消息时间戳和回调到达时间都不得超过 250 ms。
- 每个左右消息最多消费一次；缺一侧时不发布任何命令。
- POLICY 状态下，一对命令以同一临界区依次发布到两个前臂和两个后臂。
- MANUAL 状态下，一对后臂主臂反馈以同一临界区发布到两个前臂。

这里的“同一临界区”保证软件路由不会混入另一种控制源；ROS/CAN 本身不是硬
实时总线，因此不能声称四条臂在同一微秒收到命令。

## 7. 四臂同步与后臂预备

进入 POLICY 或 MANUAL 前必须比较前后对应臂的最新实际位置：

```text
六个关节最大绝对差 <= 0.05 rad
夹爪绝对差           <= 0.015 m
反馈年龄              <= 0.10 s
```

进入 POLICY 前后臂已经是从臂，但仍处于 standby。为避免第一条策略命令先让
前臂运动，协调器执行以下预备动作：

1. 验证前后实际位置已同步；
2. 用当前前臂实际位置生成一对 hold 命令，只发给后双臂；
3. 等待两侧 `/motion_ready == true`，超时 5 秒进入 FAULT；
4. 再通知 policy 客户端 fresh resume；
5. 收到恢复时间之后生成的第一对新 policy 命令，才同时发给前后四臂。

因此需要给已经实现的后臂动态节点增加 latched `~motion_ready` Bool：角色切换
开始、切换失败或命令发送失败时发布 false；首条新鲜命令完成实测姿态预装、
电机使能确认和 CAN/MoveJ 反馈确认后发布 true。

## 8. M：进入人工接管

按 `M` 或调用 `request_manual`：

1. 在协调器内部先把命令门切为关闭，状态改为 `TO_MANUAL`；
2. 调用 `/task2/policy/set_paused(true)`，要求模型停止发布并清空当前 chunk；
3. 即使模型服务超时，命令门仍保持关闭，并继续允许人工救援；同时记录
   `policy_pause_unconfirmed`，后续禁止恢复 POLICY；
4. 验证前后双臂实际位置同步；
5. 依次请求左右后臂切换 master；两个服务都必须成功；
6. 再次验证前后同步并等待两侧新鲜 master_joint；
7. 进入 `MANUAL`，只把成对 master_joint 转发到前双臂。

左右任一角色切换失败，不自动猜测或回滚硬件角色，直接进入 `FAULT` 并停止
所有命令。操作员检查物理状态后显式处理，避免自动回滚产生第二次意外切换。

## 9. S：恢复模型推理

按 `S` 或调用 `request_policy`：

1. 立即关闭人工转发门，状态改为 `TO_POLICY`；
2. 确认 policy 已成功暂停；若之前暂停未确认，重新请求 pause，失败则 FAULT；
3. 验证前后双臂仍同步；
4. 依次请求左右后臂切换 slave，并确认 latched role 均为 slave；
5. 执行第 7 节的后臂 hold/`motion_ready` 预备；
6. 调用 `/task2/policy/set_paused(false)`；π0.5 客户端在返回成功前清空相机、
   关节队列、`last_command` 和未执行 chunk；
7. 状态改为 `RESUMING`，丢弃恢复请求之前产生或到达的 policy 消息；
8. π0.5 用恢复后的新相机/关节消息完成一次全新推理；
9. 第一对新动作到达后同时发布到前后四臂，状态改为 `POLICY`。

## 10. π0.5 Task2 适配器

适配器复用现有 π0.5 的观测、限速、夹爪处理和 websocket policy 接口，但增加：

- 启动时默认 paused，不构造任何绕过协调器的前臂发布器；
- 命令固定发布到 `/task2/policy/joint_left/right`；
- 提供 `/task2/policy/set_paused`；
- pause 回调设置线程安全事件并清空待执行 chunk；
- 如果 pause 发生在 websocket infer 期间，infer 可自然返回，但结果必须丢弃；
- resume 在锁内清空五路观测队列和 `last_command`，然后解除暂停；
- 主循环只有在未暂停时才获取恢复后的同步观测并调用 infer；
- action chunk 执行过程中每一步检查暂停事件，切换后立即丢弃剩余动作；
- 左右 JointState 共享一个非零时间戳。

本阶段只适配非 RTC 客户端；RTC 控制器需要独立的 `pause/close/reinitialize` 生命周期
设计，不能假装通过同一段非 RTC 循环已经支持。

## 11. 键盘与未来页面

`task2_handover_keyboard.py` 在前台使用 termios 单字符读取：

```text
M/m -> request_manual
S/s -> request_policy
Q/q -> 只退出键盘程序，不切角色、不发布命令
```

键盘程序只调用协调器服务，不直接调用机械臂服务。未来 Cobot Station 页面也只
调用这三个协调器服务，因此 UI 不会复制或绕过安全状态机。

## 12. 故障与恢复原则

以下情况进入 FAULT：

- JointState 结构、数值、时间戳或左右配对无效；
- 前后臂反馈缺失、过期或位置差超限；
- 后臂角色服务失败或角色反馈不一致；
- 后臂 hold 后 `motion_ready` 超时；
- 恢复 POLICY 前无法确认 policy pause/fresh resume；
- 在非预期状态收到控制请求。

FAULT 时协调器不发布任何前臂或后臂命令。后臂底层节点自身的 fault 锁继续作为
第二层保护。程序退出时不自动切角色、不 DisableArm、不发送回零命令。

## 13. 测试与验收

离线自动测试覆盖：

- 状态机合法/非法转换；
- M 时先关命令门再调用外部服务；
- S 时 hold、motion_ready、fresh resume 的严格顺序；
- 左右命令配对、单侧缺失、重复、过期、零时间戳和非有限值；
- 前后臂同步阈值；
- 任一角色/模型服务失败后 fail closed；
- π0.5 在 pause、infer 中 pause、chunk 中 pause 和 resume 时清空状态；
- 键盘映射只调用协调器服务；
- launch 解析和旧文件哈希保护。

实机测试必须分阶段执行，每一步通过后才能进入下一步：

1. 五臂节点只读反馈，协调器保持 PAUSED；
2. 单独测试一条后臂 slave/master/slave，扶住五臂且不启动模型；
3. 两条后臂角色切换，不转发到前臂；
4. 后双臂从臂 hold 和 motion_ready，不启动策略动作；
5. 低速四臂镜像命令；
6. MANUAL 下后臂控制前臂；
7. π0.5 shadow 模式验证 pause/fresh resume；
8. π0.5 live 模式执行 M/S 完整接管。

任何阶段异常都停止，不跨级继续。

## 14. 2026-08-05 离线实现状态

当前已完成协调器、安全核心、单键客户端、π0.5 pause-aware 适配器、组合 launch、
确定性安装器和独立 Task2 部署入口。原三臂 launch、原 π0.5 客户端和原部署入口
没有被替换；Task2 文件使用独立名称安装。

聚焦安全审查发现并修复了两个重要问题：

1. 快速 `M -> S` 时，旧推理线程可能在新 generation 已恢复后发布旧动作。现在
   generation 校验、动作限幅和一条动作的成对发布处于同一个 Task2 锁边界；旧
   generation 无法发布。
2. 键盘退出后，策略 shell 需要同时回收前台客户端和它启动的策略服务。现在
   Task2 shell 显式记录 `client_pid` 与 `server_pid`，cleanup 只终止该 Task2
   策略进程树，不切换角色、不下发 DisableArm 或回零命令。

组合 launch 额外向下透传 `front_auto_enable`、`mid_auto_enable` 和
`rear_auto_enable`，因此实机验收第 1 阶段可以显式全部设为 `false`。正式部署
默认值仍为 `true`。

截至本文更新时，91 项 Task2 离线测试、Python 编译、安装源/目标一致性和
`roslaunch --nodes` 解析均通过。尚未执行五臂上电角色切换、hold、人工接管或
live π0.5 实机动作；这些只能按配套 runbook 分阶段验收，不能由离线结果替代。

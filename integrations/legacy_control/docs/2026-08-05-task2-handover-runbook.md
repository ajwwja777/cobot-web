# Task2 五臂 M/S 人在环接管：安装与分阶段验收手册

## 1. 本文能证明什么

这套代码解决的是：推理时前后双臂都作为从臂同步执行；按 `M` 后先停止模型
路由，把后双臂切成主臂并由人拖动控制前双臂；按 `S` 后清空旧动作、把后双臂
切回从臂，再从最新观测恢复四臂同步推理。

当前只完成了离线自动测试和 launch 解析，没有完成五臂实机验收。因此：

> Do not proceed to the next stage after any unexpected movement, role mismatch,
> stale feedback, or FAULT.

中文：只要出现意外运动、角色不一致、反馈过期或 `FAULT`，立即停止，不得进入
下一阶段。实机阶段必须扶住五个臂、保持急停可及，并由现场人员确认航空插头、
CAN_H/CAN_L、终端压接和工作区。

## 2. 文件与控制边界

源码仓库：

```text
/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/
├── multi_arm_launch_tools/launch/start_ms_piper_5arm_task2.launch
├── multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch
├── multi_arm_launch_tools/task2_driver/
├── multi_arm_launch_tools/task2_handover/
├── multi_arm_launch_tools/task2_policy_adapters/pi05/
└── multi_arm_launch_tools/install_task2_handover.sh
```

运行安装位置：

```text
/home/agilex/cobot_magic/Piper_ros_private-ros-noetic/src/piper/scripts/
├── piper_rear_role_task2_node.py
├── task2_rear_role_core.py
├── task2_handover_core.py
├── task2_handover_node.py
└── task2_handover_keyboard.py

/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05/
├── common/robot/inference_pi05.py          # 原文件，不改
├── common/robot/inference_pi05_task2.py    # Task2 适配器
├── common/inference_pi05_task2.sh
├── run_checkpoint_task2.sh
└── interface_task2_live.sh
```

协调器是四臂软件命令的唯一发布源：

```text
π0.5 -> /task2/policy/joint_left,right
               |
               v
task2_handover_node
  -> /master/joint_left,right
  -> /task2/rear_left,right/joint_cmd
```

运行 Task2 时，不得同时运行任何会直接发布 `/master/joint_left` 或
`/master/joint_right` 的旧推理客户端。

## 3. 安装与纯离线检查

这些命令不启动 CAN 或机械臂：

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
./multi_arm_launch_tools/install_task2_driver.sh --check
./multi_arm_launch_tools/install_task2_handover.sh --install
./multi_arm_launch_tools/install_task2_handover.sh --check

/home/agilex/miniconda3/envs/aloha/bin/python -m unittest discover -v \
  -s tests/task2 -p 'test_*.py'

source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch --nodes \
  /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch \
  front_auto_enable:=false mid_auto_enable:=false rear_auto_enable:=false
```

最后一条只解析 launch，应列出：前左、前右、中臂、后左、后右和
`/task2_handover`，不能列出 keyboard。

## 4. 每次上电前检查

1. 五个臂均有人扶持，急停可立即按下。
2. 左前、右前、中、左后、右后各自使用独立 CAN；底盘仍为 `can0`。
3. 后臂多股铜线已完整拧紧并压入端子，没有散丝、裸铜短接或松动。
4. 所有航空插头已插牢，排插状态明确。
5. 没有遗留的 roslaunch、旧 inference、tmux 或 tmux 内后台节点。
6. 下列两个前臂命令 topic 上没有未知 publisher：

```bash
rostopic info /master/joint_left
rostopic info /master/joint_right
```

7. 一旦下电，必须继续扶住五个臂，直到确认不会坠落。

## 5. 正式运行时的终端布局

以下是最终布局，不代表可以跳过第 6 节的分阶段验收。

### 终端 1：配置六个 CAN

```bash
conda activate aloha
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
bash multi_arm_launch_tools/can_config_task2.sh
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
ip -br link show type can
```

必须看到 `can0`、`can_left`、`can_right`、`can_mid`、`can_rear_left`、
`can_rear_right` 均为 `UP`。缺少任何一个都停止。

### 终端 2：五臂驱动和协调器

首次实机第 1 阶段必须禁止自动使能：

```bash
conda activate aloha
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch \
  /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch \
  front_auto_enable:=false mid_auto_enable:=false rear_auto_enable:=false
```

只有第 1–7 阶段逐项通过后，正式 live 才使用默认值：

```bash
roslaunch \
  /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_task2.launch
```

### 终端 3：三路相机

```bash
conda activate aloha
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch \
  /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/multi_camera_shuai.launch
```

### 终端 4：中路相机预设位姿

```bash
conda activate aloha
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
python teleop/fix_mid_camera_pose.py
```

### 终端 5：π0.5 Task2 策略和单键控制

仅在实机第 1–7 阶段通过后运行：

```bash
cd /home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05
./interface_task2_live.sh 2000
```

脚本会先确认协调器服务存在，再启动默认暂停的 π0.5，等待 pause 服务，调用
`request_policy` 完成后臂 slave/hold/fresh resume，最后把键盘保持在前台：

```text
M/m  -> 切到人工接管
S/s  -> 清旧动作并恢复策略
Q/q  -> 只退出键盘和本次 Task2 策略进程，不切角色
```

不需要按 Enter。若出现 `FAULT`，Q 退出，检查物理状态和日志；不要反复按 S。

## 6. 八个实机验收门槛

每一级必须单独记录结果。下面的直接角色服务仅用于工程验收，不是日常操作；
日常 M/S 只能调用协调器。

### 阶段 1：只读反馈，协调器保持 PAUSED

使用终端 2 的三个 `auto_enable:=false` 参数。确认：

```bash
rostopic echo -n 1 /task2/handover/mode
rostopic hz /puppet/joint_left
rostopic hz /puppet/joint_right
rostopic hz /task2/rear_left/joint_states
rostopic hz /task2/rear_right/joint_states
```

mode 必须为 `paused`，四路反馈持续更新，机械臂没有命令运动。

### 阶段 2：单条后臂 slave/master/slave

模型不启动、协调器保持 PAUSED、现场扶住五臂。一次只测试一侧：

```bash
rosservice call /task2/rear_left/set_master 'data: true'
rostopic echo -n 1 /task2/rear_left/role
rosservice call /task2/rear_left/set_master 'data: false'
rostopic echo -n 1 /task2/rear_left/role
```

右侧换成 `rear_right`。角色反馈必须分别为 master、slave；任一侧失败停止。

### 阶段 3：双后臂角色切换，不向前臂转发

仍不启动模型。分别切 master，确认两侧重力补偿和角色反馈，再切回 slave。
同时用 `rostopic hz /master/joint_left` 与 right 确认协调器没有发布前臂动作。

### 阶段 4：后臂 hold 与 motion_ready

此阶段需要专用 shadow/测试策略服务配合协调器，只允许把当前前臂实际姿态作为
hold 发给后臂，不允许发送模型 action。确认两侧：

```bash
rostopic echo -n 1 /task2/rear_left/motion_ready
rostopic echo -n 1 /task2/rear_right/motion_ready
```

都从 false 变为 true，且前臂没有运动后才通过。当前文档不提供绕过协调器的
临时发布命令；若没有测试策略服务，本阶段保持“未验收”，不得直接跳到 live。

### 阶段 5：低速四臂镜像命令

使用受审查的低速测试发布器，限幅必须低于正式策略；验证一对命令同时到前后
四臂，后臂跟随误差不超 `0.05 rad / 0.015 m`。当前交付不包含临时运动发布器，
不得用 `rostopic pub` 手写关节数组代替。

### 阶段 6：MANUAL 下后臂控制前臂

策略必须已成功 pause，前后臂同步后，通过协调器 `request_manual` 或键盘 M
切换。先做毫米/小角度动作；确认只有成对后臂 master feedback 控制前臂。

### 阶段 7：π0.5 shadow 的 pause/fresh resume

策略只计算、不建立动作 publisher。验证推理中按 M、chunk 中按 M、再按 S 后，
旧 generation 全部丢弃，第一对结果来自 S 之后的新观测。必须观察 mode 顺序：
`manual -> to_policy -> resuming`，shadow 下不能出现机械臂动作。

### 阶段 8：π0.5 live 完整 M/S

前七级全部通过后，才运行 `./interface_task2_live.sh 2000`。先让策略执行短距离，
按 M 人工修正，再按 S 恢复。记录 mode、fault、左右 role、motion_ready 和现场视频。

## 7. 故障处理

查看状态：

```bash
rostopic echo -n 1 /task2/handover/mode
rostopic echo -n 1 /task2/handover/fault
rostopic echo -n 1 /task2/rear_left/role
rostopic echo -n 1 /task2/rear_right/role
```

`reset_fault` 只清软件锁，回到 PAUSED；它不切角色、不使能、不回零：

```bash
rosservice call /task2/handover/reset_fault
```

只有亲眼确认五臂物理状态、两侧角色和工作区安全后才能调用。清除后仍需重新
请求 MANUAL 或 POLICY。任何一侧角色服务部分成功时，系统故意不自动回滚。

## 8. 旧文件保护哈希

2026-08-05 离线交付时的 SHA-256：

```text
64a3a55424eec91117c5ff48f5714c1a80886e432357dd5b1b43ab5c09de5968  piper_start_ms_node.py
788af1f70eedc2af99e69cd9c2bb507d44c4965bae8450dba6c23a99d479a6c2  start_ms_piper_3arm.launch
0add48c4d652f6c520a5cb29a4a8fed777460222e5b0a10b2706cdbd0e788e20  start_ms_piper_3arm_collect.launch
40daa73aebc48ddc0894f85abcb4245e06a47d7af8dc88b4b6765222c01204dd  can_config_task2.sh
f1ad74552db101b50f4a56d60b6153da98d9e7ed19a3028c89570e461e8ec702  common/robot/inference_pi05.py
d9a0a357bff168a17a1f8db8bfe7ad27ac298a912d1368f09941386b699e9555  interface_live.sh
b0c9aed3698421909607b5df8368c449adee87481e294921556f85fcb5d08480  run_checkpoint.sh
```

安装器只写 Task2 独立文件名；哈希变化表示有人另外修改了旧路径，需要先审计，
不能把变化归因于 Task2 安装器。

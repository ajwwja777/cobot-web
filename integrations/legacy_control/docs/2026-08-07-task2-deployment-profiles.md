# Cobot Magic 部署 profile：旧部署、示教接管推理、采数据

> 要给**新训练出来的模型**写部署脚本，看
> [`2026-08-07-task2-integration-contract.md`](2026-08-07-task2-integration-contract.md)
> ——那里有接口契约、可照抄的模板 `task2_policy_deploy_template.sh`，
> 以及自检脚本 `task2_policy_contract_check.sh`。
> 那份文档也是新对话快速理解本项目的入口。
>
> 只是要让某个新模型支持接管、不关心项目全貌的话，直接发
> [`2026-08-07-task2-new-model-integration-brief.md`](2026-08-07-task2-new-model-integration-brief.md)。

三种用法共用同一套硬件和同一份接线表，区别只在**起哪个 launch**、**后臂空闲时是软的还是抱闸**、以及**推理指令走不走闸门**。

---

## 0. 先解决一个会卡住旧部署的问题

每条臂是**独立的 USB-CAN 模块**，互不共用总线：

| 接口 | USB bus-info | 用途 |
|---|---|---|
| `can_left` | `1-13.1.2:1.0` | 左前臂 |
| `can_right` | `1-13.1.3:1.0` | 右前臂 |
| `can_mid` | `1-13.1.4:1.0` | 中臂（相机） |
| `can_rear_left` | `1-13.1.1:1.0` | 左后臂 |
| `can_rear_right` | `1-13.2:1.0` | 右后臂 |
| `can0` | `1-4:1.0` | 底盘 |

`can_config_shuai.sh` 写死了 `EXPECTED_CAN_COUNT=4`，并且遇到不认识的 USB 口就 `exit 1`。**只要后臂的两个 CAN 模块插在机器上，它在配置任何接口之前就会退出**——和后臂是否通电无关。旧脚本和 task2 脚本因此在同一台机器上互斥。

替代品是 `multi_arm_launch_tools/can_config_cobot.sh`：一份接线表，按 profile 声明**必需**的接口，不认识的适配器只报告不致命。

```bash
bash multi_arm_launch_tools/can_config_cobot.sh probe     # 只看接线，不改任何东西，不需要密码
bash multi_arm_launch_tools/can_config_cobot.sh legacy3   # 旧的 3 臂部署
bash multi_arm_launch_tools/can_config_cobot.sh task2     # 5 臂（接管推理 / 采数据）
```

两个 profile 都会把**所有认得的**适配器配好并激活，只是"缺了就报错"的清单不同。所以从 task2 切回 legacy3 不需要拔任何东西。

`can_config_shuai.sh` 和 `can_config_task2.sh` 保留未改，作为接线记录。日常用上面这一个。

---

## 1. 旧部署（3 臂，无后臂参与）

后臂的电源线和 CAN 线**可以一直接着**，不需要拔。`start_ms_piper_3arm.launch` 只连 `can_left` / `can_right` / `can_mid`，没有任何节点会寻址后臂，它们就停在上一次被留下的状态。

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
bash multi_arm_launch_tools/can_config_cobot.sh legacy3

source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch multi_arm_launch_tools/launch/start_ms_piper_3arm.launch mode:=1 auto_enable:=true

# 相机、中臂相机定位、interface.sh 全部不变
```

**唯一要注意的**：如果上一次跑的是接管推理 profile，后臂被留在**失能**状态（软的）。它们会靠在自己的低位上，不影响前臂，但起 launch 前先看一眼它们没有压到别的东西。

---

## 2. 示教接管推理（5 臂）

推理期间随时按后臂示教按钮接管，再按一下交还。详见
`docs/2026-08-06-task2-physical-teach-handover-runbook.md` §10。

```bash
bash multi_arm_launch_tools/can_config_cobot.sh task2

source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch multi_arm_launch_tools/launch/start_ms_piper_5arm_button_teach_task2.launch \
  front_mode:=1 front_auto_enable:=true mid_auto_enable:=true rear_auto_enable:=true

# 相机、中臂相机定位不变

/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05/interface_task2_teach_rtc_live.sh 2000
```

后臂空闲时**失能**（`rear_idle_disabled:=true`，默认），所以可以徒手搬到顺手的位置——离合参考在按下按钮那一刻才抓取，搬动不会带着前臂走。

---

## 3. 采数据（5 臂，后臂使能）

> 用 cobot-station 网页采集（推荐）看
> [`2026-08-07-task2-cobot-station-collection.md`](2026-08-07-task2-cobot-station-collection.md)。
> 本节是不经过网页、直接用 collect_data_3arm 那条路。

采数据需要后臂使能、和前臂一起动。**这正是示教模式本身提供的**：按下按钮后固件会给该臂上电并做重力补偿，协调器把它经离合转发给前臂。按钮是一按一放的开关，所以一次按下可以持续整个 episode。

```bash
bash multi_arm_launch_tools/can_config_cobot.sh task2

source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch multi_arm_launch_tools/launch/start_ms_piper_5arm_teleop_collect.launch \
  front_mode:=1 front_auto_enable:=true mid_auto_enable:=true

# 相机、中臂相机定位不变
# 然后按平时的方式起录制节点，录 /master/joint_* 作 action，/puppet/joint_* 作 state
```

和推理 profile 只差一个参数：`rear_idle_disabled:=false`。**episode 之间后臂抱闸停在原地，不会软下来垂落**——连续采集时这个差别很重要。要挪动后臂就按一下按钮（此时前臂会跟随，这在采数据时正是想要的）。

**不需要也不要用主从联动模式**：固件 S-V1.7-3 上 `MasterSlaveConfig(0xFA)` 会让后臂停止一切 CAN 输出，且必须断电重启才能恢复，见 runbook §2。示教按钮这条路不需要切角色。

一个已知差异：录到的 action 是**离合映射后的前臂指令**，不是后臂的绝对关节角。对模仿学习没有影响（action 本来就是下发给前臂的指令），但如果有脚本假设"主臂角度 == 从臂指令"，需要改成读 `/master/joint_*`。

---

## 4. 让任意模型支持接管

有两条路，深浅之别很实在。

### 4.1 深度集成（推荐，需要改模型的推理循环）

参考实现：`task3/jiaan/deployments/in_the_pot/pi05/common/robot/inference_pi05_rtc_task2.py`

策略进程自己提供 `/task2/policy/set_paused`，并在暂停时做三件事：

1. **停止发布**
2. **丢弃动作块**——RTC 里是 `controller.close()` 然后重建；不能用 `reset()`，它不停后台推理线程，下一次 `initialize()` 会起第二个 worker，两个都去做推理，输的那个抛"推理已在途"，`_fail()` 把它变成 `rospy.signal_shutdown()`，主循环静默退出（症状：交还后手臂动一个 chunk 就整个进程正常返回 0）
3. **清空 `last_command`**——步长限幅器是从上一条**指令**开始爬的，接管后那条指令描述的是手臂原来的位置。不清它，恢复瞬间手臂会朝旧位置爬

恢复时用**全新观测**重新 `initialize()`，保证第一个动作块是按相机现在看到的场景规划的。

只需要这三条，其余（观测、限幅、夹爪处理）全部复用原有实现。

### 4.2 通用闸门（零改动，只适合演示）

```bash
multi_arm_launch_tools/task2_wrap_policy.sh <任意 interface 脚本> [参数...]
```

它启动 `task2_policy_gate_node.py`，把策略的指令话题指向闸门输入，由闸门代为提供 `/task2/policy/set_paused`：

```
策略 --/task2/policy_raw/joint_*--> 闸门 --/task2/policy/joint_*--> 协调器 --> 前臂
```

**闸门做不到的事，必须清楚**：它在策略进程外面，**清不掉策略内部的动作块，也重置不了策略自己的步长限幅器**。分块策略在交还后会接着执行接管前算好的动作。闸门能做的只是把由此产生的**跳变**限速——恢复后它从手臂的**实测位置**开始，按 `ramp_step_rad`（默认 0.01 rad/次）向策略指令逼近，追上后转为直通。

**动作是旧的，只是到得平滑。** 演示够用，正经跑请走 4.1。

如果策略的指令话题环境变量名不是 pi0.5 那两个（`PUPPET_ARM_LEFT_CMD_TOPIC` / `PUPPET_ARM_RIGHT_CMD_TOPIC`），需要在 `task2_wrap_policy.sh` 里加对应的覆盖——这是这条路唯一的按模型工作量。

---

## 5. 三种 profile 的差异一览

| | 旧部署 | 接管推理 | 采数据 |
|---|---|---|---|
| CAN profile | `legacy3` | `task2` | `task2` |
| launch | `start_ms_piper_3arm` | `start_ms_piper_5arm_button_teach_task2` | `start_ms_piper_5arm_teleop_collect` |
| 后臂空闲 | 不参与 | 失能（可徒手搬） | 抱闸（停在原地） |
| 后臂进 CAN 控制 | — | **从不** | **从不** |
| 推理指令路径 | 直连前臂 | 协调器（可被按钮暂停） | 无推理 |
| 中臂 | 使能 | 使能 | 使能 |

**"后臂从不进 CAN 控制"是这套设计的核心不变式**，不是实现细节：固件 S-V1.7-3 上每次进入 `ctrl_mode=0x01` 后固定 0.5 秒会抛 `arm_status=0x05` + `err_code=0x003F`，关节通信中断约 100 毫秒，手臂失力下坠再自愈。绕开它的唯一办法就是永远不进。详见 runbook §10。

---

## 6. 交付文件

| 文件 | 作用 |
|---|---|
| `multi_arm_launch_tools/can_config_cobot.sh` | 统一 CAN 配置，按 profile 声明必需接口 |
| `multi_arm_launch_tools/task2_wrap_policy.sh` | 用通用闸门包装任意 interface 脚本 |
| `multi_arm_launch_tools/task2_teach_handover/task2_policy_gate_node.py` | 通用暂停闸门 + 恢复限速 |
| `multi_arm_launch_tools/task2_teach_handover/task2_teach_button_node.py` | 按钮驱动协调器（每侧独立接管） |
| `multi_arm_launch_tools/launch/start_ms_piper_5arm_button_teach_task2.launch` | 接管推理 |
| `multi_arm_launch_tools/launch/start_ms_piper_5arm_teleop_collect.launch` | 采数据 |
| `.../pi05/interface_task2_teach_rtc_live.sh` | pi0.5 RTC 接管推理入口 |
| `.../pi05/common/robot/inference_pi05_rtc_task2.py` | pi0.5 RTC 深度集成参考实现 |
| `tests/task2/test_policy_gate_node.py` | 闸门测试 |
| `tests/task2/test_teach_button_node.py` | 协调器测试 |

旧的 M/S 协调器（`task2_teach_handover_node.py`）、它的 launch、键盘节点和 `interface_task2_teach_live.sh` **全部保留未删**，测试也仍然通过，作为固件修复后的回退选项。注意那条路径依赖 `request_manual` / `request_policy` 两个服务，按钮协调器不提供它们——两套不能混用。

---

## 7. 跑测试

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
python -m unittest discover -s tests/task2 -p "test_*.py"
./multi_arm_launch_tools/install_task2_teach_handover.sh --check
```

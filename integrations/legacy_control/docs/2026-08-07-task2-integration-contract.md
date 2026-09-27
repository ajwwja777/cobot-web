# Task2 接管接口契约 · 项目交接

这份文档有三个用途：**新对话快速理解这个项目**、**记录做了什么和踩了什么坑**、**给以后每个训练产物一份可照抄的部署接口**。

看完第 1 节就能接上话；要写新模型的部署脚本直接跳到第 4 节。

> **要让新训练的模型支持接管？** 把
> [`2026-08-07-task2-new-model-integration-brief.md`](2026-08-07-task2-new-model-integration-brief.md)
> 这一份直接发给负责产出部署脚本的人或对话即可 —— 它是自包含的，
> 不需要额外解释。

---

## 1. 三十秒速读

**要解决什么**：Cobot Magic 平台（4 条 AgileX Piper 臂 + 1 条相机臂），π0.5 推理跑着的时候，人要能随时接管前面两条任务臂，改完再交还，全程不中断部署。

**最终形态**：操作员按一下后臂上的**物理示教按钮**就接管那一侧，再按一下交还。没有键盘、没有模式切换、没有顺序要求。两侧独立。

**最重要的一条约束**：

> **后臂永远不进 CAN 控制模式（`ctrl_mode=0x01`）。**

固件 S-V1.7-3 上，每次进入 `ctrl_mode=0x01` 后**固定 0.5 秒**会抛 `arm_status=0x05` + `err_code=0x003F`，六个关节通信同时中断约 100 毫秒，伺服失力、手臂下坠、然后自愈。与指令频率、待机时长、使能状态全都无关，改不掉。整套设计就是围绕"绕开它"建立的。

**为什么绕得开**：映射是**离合式增量**——按下按钮那一刻记住 `(前臂位置 F₀, 后臂位置 R₀)`，之后 `前臂目标 = F₀ + (后臂当前 − R₀)`。前后臂不需要位置对齐，所以后臂根本不需要被驱动，也就不需要进 CAN 控制。

**当前状态**：真机验证通过（π0.5 RTC + 真模型，4 个 episode 反复接管，211 秒 50 Hz 采样零掉落）。

---

## 2. 系统结构

```
        策略进程
             │  /task2/policy/joint_{left,right}
             ▼
    ┌────────────────────────┐  /task2/policy/set_paused
    │  task2_teach_button_   │ ─────────────────────────► 暂停 / 恢复策略
    │       node             │
    │  （前臂唯一指令源）      │ ◄── /task2/teach/rear_*/teach_active
    │                        │ ◄── /task2/teach/rear_*/joint_states
    └────────┬───────────────┘ ◄── /puppet/joint_*
             │ /master/joint_{left,right}
             ▼
        前左臂        前右臂
```

**三条不变式**，任何改动都不能破坏：

1. **协调器是前臂唯一的软件指令源。** 策略指令先进协调器；接管期间被丢弃。所以"暂停策略"失败不危险——策略根本够不到手臂。
2. **后臂永不进 CAN 控制。** 见第 1 节。
3. **映射是增量不是绝对。** 后臂进示教时松抱闸会沉降零点几弧度，绝对映射会把这个沉降直接甩给前臂。

**一次接管的完整时序**

```
空闲      后臂失能（软的），可徒手搬到顺手位置；搬动不影响前臂
          前臂跑推理

按下 X 侧示教按钮
   ├─ 固件给 X 后臂逐关节上电（约 110 ms），进重力补偿
   ├─ 驱动检测 teach_status，发 teach_active=true
   ├─ 协调器：抓离合参考 → 暂停策略 → X 前臂跟随 X 后臂
   └─ 另一侧前臂定住，其后臂仍然软着

拖动 X 后臂，X 前臂跟随

再按一下
   ├─ 驱动在同一个回调里立刻发 DisableArm → X 后臂变软
   ├─ X 前臂停在最后位置
   └─ 没有按钮还按着 → 策略以全新观测重新开始
```

---

## 3. 做了什么、踩了什么坑

按发生顺序，只记有结论的。

### 3.1 方案一（主臂模式 + 话题切换）被证伪

官方推荐的第一条路。写了 8 种配方的固件探针
（`multi_arm_launch_tools/task2_probe_master_slave_cycle.py`）逐一验证：

- 角色切换本身能来回切，状态机没有卡死
- 但 `MasterSlaveConfig(0xFA)` 之后后臂**停止一切 CAN 输出**，永远不产生主臂帧
- 恢复主臂行为需要**断电重启**，两个方向都需要

实时接管不可能断电，这条路死了。前一个 agent 的结论"内部角色状态机卡住"是误判——不是卡住，是这个固件版本根本不支持热切换。

### 3.2 掉落：五个假设，全错

方案二做通之后，每次交还控制（当时还叫按 S）后臂都会失能掉落。我依次归因并逐一被实测否掉：

| 假设 | 怎么验的 | 结果 |
|---|---|---|
| 退出示教的指令导致失能 | `MotionCtrl_1(0x02)` 换成 `(0,0,0x00)` | 电机确实不失能了，但**还是掉** |
| 待机停留不够长 | 无条件停留 1.0 秒 | 停留期间**完全干净**，故障在之后 |
| 指令频率打爆总线 | ride-through 20 Hz 降到 4 Hz | 故障时间点**分毫不差** |
| 关节离开示教需重新初始化 | 待机期间无条件补 `EnableArm(7)` | 故障照旧，还是 0.495 秒 |
| 顶住使能位就不会掉 | 50 Hz 使能广播，实测使能位全程 111111 | **操作员报告掉落幅度毫无变化** |

最后一条是转折点：**使能位是状态字，不是力矩保证**。关节通信断了，驱动器收不到指令，位是 1 而力矩没了。所有"盯着使能位"的缓解手段全部无效。

真正的原因是 3.1 节那条固件定时故障。**绕开它的唯一办法是永远不进那个模式**——而离合映射让这件事成为可能。

### 3.3 待机保持 → 空闲失能

第一版绕开方案：后臂停在待机（`ctrl_mode=0x00`），抱闸锁住。掉落确实消失了（211 秒零异常）。

但用户指出一个我没想到的问题：**抱闸锁死之后，操作员没法徒手把后臂搬到舒服的位置**。而离合参考是在按下按钮那一刻抓的，任何重新摆位都必须发生在按钮按下之前——那就必须有一个"能用手推、但不转发"的状态。只有失能能提供。

所以空闲态改成**失能**。手臂会在重力下垂落，这是**输入设备的静止姿态**，不是"运行中的手臂掉了"，风险完全不同。

### 3.4 M/S 键被删掉

原设计要求严格顺序：按 M → 按示教按钮 → 拖 → 再按按钮 → 按 S。用户提出示教按钮本身就是明确意图，M 是多余的。

改成按钮直驱后：少一个要记的顺序、支持单臂接管、协调器状态机从"两臂同进同出"简化成每侧独立。

### 3.5 `AsyncRTCController.reset()` 不能用来重新初始化

接真模型时踩的坑，**症状极具迷惑性**：交还后手臂动一个 chunk（约 1.2 秒）就停，整个进程**正常返回 0**，一行报错都没有。

原因：`reset()` 只清动作队列和标志位，**不停后台推理线程**。下一次 `initialize()` 又起一个，两个 worker 抢同一个唤醒事件，输的那个在 `begin_inference()` 撞上"推理已在途"抛异常 → `_fail()` → `sink.safe_stop()` → `rospy.signal_shutdown()`。主循环的退出条件是 `not rospy.is_shutdown()`，于是干净地走完 `finally` 返回 0。

正解：暂停时 `controller.close()`（会 join 线程），恢复时**造一个全新的控制器**。

### 3.6 暂停服务失败不该是致命错误

用户在两次推理之间的空档按了示教按钮，协调器去暂停一个不存在的策略，失败后 latch 了 fault。之后按钮无效、推理也不转发，两个症状一起出现。

判断依据：协调器是 `/master/joint_*` 的唯一发布者，接管时**本来就丢弃所有策略指令**。所以"暂停策略"影响的只是策略自己攒不攒动作块，碰不到手臂——它是礼貌，不是安全屏障。而拒绝接管的代价是真实的：操作员握着后臂，前臂不跟随。

改成：暂停失败只记日志，接管照常。反方向也一样，恢复失败就让策略继续暂停着（安全方向）。

### 3.7 CAN 配置脚本互斥

`can_config_shuai.sh` 写死 `EXPECTED_CAN_COUNT=4` 且遇到不认识的 USB 口 `exit 1`。**只要后臂的 CAN 模块插着，旧脚本在配置任何接口之前就退出**，和后臂通不通电无关。

改成 `can_config_cobot.sh <profile>`：一份接线表，按 profile 声明**必需**的接口，不认识的适配器只报告不致命。

### 3.8 方法论上的教训

三次我的采样说"修好了"，操作员的眼睛说没有，**每次都是操作员对**：

- 0.3 秒轮询看不见 80 毫秒的失能
- 首尾位置对比看不见"掉下去又弹回来"
- 使能位全程为 1 不代表关节有力矩

还有一次更糟：我把**裸 SDK 脚本**的测量结果直接当成完整驱动链路的结论，报告"1 秒待机停留解决了掉落"——实际它只是把猛烈掉落换成了缓慢下沉，我的粗采样看不见。

**规则**：硬件结论必须在完整链路上复现；台架脚本的结果不能外推。操作员的直接观察优先于我的遥测。

---

### 3.9 一键归位：四个只有真机能暴露的 bug

每条 episode 都要从同一个起点开始，人也不该从很低的姿势把手臂拉起来。
实现见 `multi_arm_launch_tools/task2_homing/`。

**前臂归位是免费的**：它们一直在 CAN 控制里，归位只是换个目标，不涉及模式
切换，不失能、不掉落。

**后臂归位要进 CAN 控制**，也就是要碰 §1 那条约束。它安全，是因为**移动永远
从重力静止位开始**——操作员把失能的手臂放回低位，那 0.5 秒故障在那里发生
时，100 毫秒没有力矩什么后果都没有。**但顺序不能反**：先进 CAN 控制、静止
等 1.2 秒把故障吃掉，**再**开始移动。先下发目标的话，故障会发生在手臂已经
抬起来的途中，那就是真的从高处摔。

上线过程中真机暴露了四个 bug，离线测试一个都发现不了，**它们全都是"上一次
运行留下的状态"和"固件的真实时序"**：

| bug | 症状 |
|---|---|
| `~home` 服务在 launch 里漏了 remap | CLI 报"服务不可用" |
| 归位路径漏了解锁存回退 | 退出示教后归位必失败（锁存是常态，不是异常） |
| 静止期之后没有主动恢复 | 服务报告成功，手臂却软塌塌躺着——故障把电机关了，而静止期不发任何指令，没有东西把它带回来 |
| 启动时无条件失能 | 上一轮留在 CAN 控制里保持的手臂，重启 launch 会被直接丢下（从高处掉） |

第三条最有欺骗性：之前 50 Hz 轨迹里看到的"故障自愈"，其实是 `_send_target`
每帧重新断言模式带回来的。归位的静止期刻意什么都不发，所以没有东西带它回来。

### 3.10 CAN：接口"就绪"不等于总线能用

`can_config_shuai.sh` 写死 `EXPECTED_CAN_COUNT=4` 且见到不认识的 USB 口就
`exit 1`，所以后臂适配器一插上它就退出——和接线对不对、后臂通不通电都无关。
替代品是 `can_config_cobot.sh <legacy3|task2|probe>`。

更要紧的是**"配好了"和"能用"是两回事**。实测遇到过：

```
名字对、UP、比特率对、状态 ERROR-ACTIVE、收包 200 Hz  ——  但发送全部失败
write: No buffer space available
```

起 launch 会看到 `SEND_MESSAGE_FAILED (100017)` 刷屏，然后前臂使能超时、节点
退出，而其余节点还活着——半死不活比整个挂掉更难查。

机制是 gs_usb 的**发送回显槽位错乱**（内核日志 `Unexpected unused echo id`）：
驱动和适配器对"哪些槽位在途"的看法对不上，槽位泄漏光之后队列停发。账目只在
驱动内存里，所以 `ip link set <if> down && up` 重建它就好，**不用拔插硬件**。

`restart-ms` 对这个**没用**——它的触发条件是 bus-off，而这里控制器全程
ERROR-ACTIVE。

两种故障症状完全一样，**只有内核日志能分开**：

```bash
dmesg | grep -iE "gs_usb|USB disconnect" | tail -20
```

| 日志 | 是什么 | 处置 |
|---|---|---|
| `USB disconnect` + `renamed from canN` | 真的插松了 | 重新插稳 |
| `Unexpected unused echo id` | 驱动槽位错乱 | `down/up` |

所以 `can_config_cobot.sh` 现在同时查**收**和**发**，失败时直接打印处置命令。
开机第一件事跑 `probe`（只读、不要密码）比等 launch 炸了再查省事。

一个观察：这个槽位错乱在两个不同拓扑位置的适配器上都发生过（一个在二级 hub
后，一个直插主板根端口），间隔 25 分钟。**触发原因未确定**；曾怀疑 USB 拥塞，
但两个 hub 都是多 TT、且直插的那个也中招，该假设不成立。内核是
`5.15.0-102-generic`。

## 4. 接口契约（写新部署脚本看这里）

一个模型部署满足下面 6 条，就能直接用示教接管，**不需要改机器人侧任何东西**。

### 4.1 必须做到

| # | 要求 | 检查方式 |
|---|---|---|
| 1 | 关节指令发到 `/task2/policy/joint_left` 和 `/task2/policy/joint_right`，**不得**直接发 `/master/joint_*` | `rostopic info /master/joint_left` 的 Publishers 里只能有协调器 |
| 2 | 消息是 `sensor_msgs/JointState`，`name = [joint0..joint6]`，`position` 7 个值（6 关节 + 夹爪），带有效 `header.stamp` | 协调器会拒收并 fault |
| 3 | 提供服务 `/task2/policy/set_paused`（`std_srvs/SetBool`） | `rosservice info /task2/policy/set_paused` |
| 4 | **启动后处于暂停状态**，由操作员显式恢复 | 起来后 `/task2/policy/joint_*` 应该没有数据 |
| 5 | `set_paused(true)` 后立即停止发布，并**丢弃已排队的动作** | 见 4.3 |
| 6 | `set_paused(false)` 后用**全新观测**重新规划，并把步长限幅锚定在**实测关节位置**（`/puppet/joint_*`），不是自己上一条指令 | 见 4.3 |

### 4.2 不要做的

- **不要**在被恢复之前把手臂开到预设初始位姿（`USE_INIT_POSE=true`）。手臂在哪由操作员决定
- **不要**同时跑两个提供 `/task2/policy/set_paused` 的进程（例如深度集成的策略 + 通用闸门），协调器会连到后注册的那个
- **不要**假设后臂和前臂位置一致。离合映射下它们本来就不一致

### 4.3 恢复时必须做的三件事

这是整个契约里最容易漏、后果最直接的部分。参考实现见
`.../pi05/common/robot/inference_pi05_rtc_task2.py`。

1. **停止发布** —— 不再往指令话题发东西
2. **丢弃动作块** —— 暂停期间攒的动作是为"操作员介入前的世界"算的，恢复时执行它们等于按几秒前的场景动手
   - RTC 用 `controller.close()` 然后重建。**不要用 `reset()`**，见 3.5
3. **清空 `last_command`** —— 步长限幅器从上一条**指令**开始爬，接管后那条指令描述的是手臂原来的位置。不清它，恢复瞬间手臂会朝旧位置爬

### 4.4 两条集成路线

**深度集成**（推荐）：策略进程自己提供 `set_paused`，在里面做 4.3 那三件事。改动量约 100 行，参考
`inference_pi05_rtc_task2.py`。

**通用闸门**（零改动，只适合演示）：

```bash
multi_arm_launch_tools/task2_wrap_policy.sh <任意 interface 脚本> [参数...]
```

闸门（`task2_policy_gate_node.py`）代替策略提供 `set_paused`，把策略的指令话题接到自己的输入：

```
策略 --/task2/policy_raw/joint_*--> 闸门 --/task2/policy/joint_*--> 协调器
```

**它做不到 4.3 的第 2、3 条**——它在策略进程外面，清不掉策略内部的动作块，也重置不了策略的限幅器。它能做的只是把由此产生的**跳变**限速：恢复后从手臂实测位置按 `ramp_step_rad`（默认 0.01 rad/次）向策略指令逼近，追上后转直通。**动作是旧的，只是到得平滑。**

### 4.5 部署脚本模板

复制 `multi_arm_launch_tools/task2_policy_deploy_template.sh`，改开头三个变量即可：

```bash
POLICY_NAME="my_new_model"
POLICY_RUNNER="/abs/path/to/run_checkpoint_xxx.sh"   # 实际起策略的脚本
POLICY_RUNNER_ARGS=("$@")
```

模板已经处理好：协调器在线检查、fault 自动清除、等待 `set_paused` 出现、操作确认、显式解除暂停、退出时清理子进程。

### 4.6 自检

```bash
multi_arm_launch_tools/task2_policy_contract_check.sh
```

对**正在运行**的系统逐条检查 4.1。默认只做不动手臂的检查（话题/服务是否存在、发布者身份、暂停时是否静默）。加 `--live` 才会真的解除暂停验证转发，**那一步手臂会动**。

---

## 5. 三种部署 profile

归位（每条 episode / 每次部署都从同一起点开始）：

```bash
python multi_arm_launch_tools/task2_homing/task2_home_cli.py capture   # 第一次：定起点
python multi_arm_launch_tools/task2_homing/task2_home_cli.py all       # 之后：回起点
```

前臂是使能的、用手推不动，所以**起点要靠后臂拖出来**：按示教按钮 → 拖后臂带
着前臂走 → 退出示教 → `capture`。存的是前臂**实测**位姿。

**起点和数据集是绑定的。** 部署时必须用采这批数据时的那个起点，否则策略一上来
看到的初始状态就在训练分布之外。建议起点名字带上数据集名（`--pose` 支持多套），
别都叫 `collect_start`。

部署时归位插在"起 launch 之后、解除策略暂停之前"。协调器会拒绝在策略正在
发指令时归位（两者都写 `/master/joint_*`，会打架）。


详细命令见 `2026-08-07-task2-deployment-profiles.md`。

| | 可示教推理 | 旧 3 臂推理 | 数据采集 |
|---|---|---|---|
| CAN | `can_config_cobot.sh task2` | `legacy3` | 见下 |
| launch | `start_ms_piper_5arm_button_teach_task2.launch` | `start_ms_piper_3arm.launch` | 见下 |
| 后臂空闲 | 失能（可徒手搬） | 不参与 | 抱闸 |
| 协调器 | 有 | 无 | 有 |
| 相机 / 中臂 | `multi_camera_shuai.launch` + `fix_mid_camera_pose.py` | 同左 | 同左 |

**数据采集有一个未决问题**：原有流程 `start_ms_piper_3arm_collect.launch` 用 `mode=0`，
依赖主臂和从臂**在同一条 CAN 总线上**、联动由固件完成。但实测中 `can_rear_left` 和
`can_left` 报告的状态不同，说明后臂现在在**独立总线**上——如果接线确实改过，`mode=0`
这条路是断的。

验证（不需要起任何新东西）：

```bash
bash multi_arm_launch_tools/can_config_cobot.sh legacy3
roslaunch multi_arm_launch_tools/launch/start_ms_piper_3arm_collect.launch
rostopic hz /master/joint_left     # 有数据吗
# 用手拖左后臂，看左前臂动不动
```

- 前臂跟着动 → 接线没改，用原流程
- 不动 → 用 `start_ms_piper_5arm_teleop_collect.launch`（按钮驱动，`rear_idle_disabled:=false`）

---

## 6. 代码地图

基准路径 `/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/`

代码有三代，**只有第三代是活的**，前两代保留未删（测试仍全过）。

### 第三代 · 生产路径

| 文件 | 作用 |
|---|---|
| `task2_teach_driver/piper_rear_teach_task2_node.py` | 后臂驱动。只读关节、判示教、退出示教时立刻 `DisableArm`。**从不下发位置指令** |
| `task2_teach_driver/task2_rear_teach_core.py` | 去抖、单位换算、CAN 口白名单（纯逻辑，无 ROS） |
| `task2_teach_handover/task2_teach_button_node.py` | 协调器。前臂唯一指令源，按钮驱动，每侧独立 |
| `task2_teach_handover/task2_policy_gate_node.py` | 通用暂停闸门 + 恢复限速 |
| `task2_teach_handover/task2_teach_handover_core.py` | 状态定义，转发校验原语 |
| `launch/start_ms_piper_5arm_button_teach_task2.launch` | 接管推理 |
| `launch/start_ms_piper_5arm_teleop_collect.launch` | 数据采集（`rear_idle_disabled:=false`） |
| `can_config_cobot.sh` | 统一 CAN 配置，profile 化 |
| `task2_wrap_policy.sh` | 包装任意 interface 脚本 |
| `task2_policy_deploy_template.sh` | **新模型部署脚本模板** |
| `task2_policy_contract_check.sh` | **契约自检** |
| `task2_homing/task2_homing_core.py` | 归位的纯逻辑：位姿校验、限速插补（无 ROS，可单测） |
| `task2_homing/task2_home_cli.py` | 归位命令行：`front` / `rear` / `all` / `capture` / `show` |
| `task2_homing/home_poses.yaml` | 起点配置。出厂无位姿，必须先 `capture`——占位的全零位姿是个合法但很远的目标 |
| `install_task2_teach_handover.sh` | 装 8 个文件到 ROS 包（`--install` / `--check`） |

### 被依赖的旧代码（不能删）

| 文件 | 为什么留 |
|---|---|
| `task2_handover/task2_handover_core.py` | 提供 `validate_joint` / `ValidatedJoint` / `JOINT_NAMES`，第三代在用 |
| `tests/task2/test_handover_node.py` | 提供 `FakeRos` / `Clock` / `install_import_stubs` 测试替身，被 4 个第三代测试 import |

### 已废弃（保留但不要用）

- 第一代（方案一）：`task2_driver/`、`task2_handover/task2_handover_node.py`、`task2_probe_master_slave_cycle.py`、`launch/start_ms_piper_5arm{,_handover}_task2.launch`
- 第二代（M/S 键）：`task2_teach_handover/task2_teach_handover_node.py`、`..._keyboard.py`、`launch/start_ms_piper_5arm_handover_teach_task2.launch`、`.../pi05/interface_task2_teach_live.sh`

第二代和第三代**不能混用**：第二代依赖 `request_manual` / `request_policy` 两个服务，按钮协调器不提供。

### 模型侧

`/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05/`

| 文件 | 作用 |
|---|---|
| `interface_task2_teach_rtc_live.sh` | 入口，暂停启动 + 清 fault + 等确认 |
| `run_checkpoint_rtc_task2.sh` | 改话题、禁 init pose、换客户端脚本 |
| `common/robot/inference_pi05_rtc_task2.py` | **深度集成参考实现** |
| `common/inference_pi05_rtc.sh` | 通用 RTC 启动器（加了 `PI05_RTC_CLIENT_SCRIPT` 覆盖点） |

---

## 7. 验证状态

**真机验证过**

- 接管推理全流程（π0.5 RTC + 真模型，4 个 episode 反复接管）
- 零掉落：211 秒 50 Hz 采样，`ctrl_mode=0x01` 样本数 **0**，异常样本 **0**
- 一键归位：前臂、后臂、前后臂并行，多轮
- cobot-station 网页采集：多条 episode，584 帧 / 19.5 秒 @ 30 Hz，无 NaN、无全零帧，
  两条臂 action 幅度 60~130°
- fault 恢复、空档期按按钮不再卡死
- CAN 健康检查在真实故障上生效（拦下了发送失败的总线）

**没在真机验证过**

- 通用闸门接真模型（只做了单机 roscore 冒烟）
- 连续多条 episode 的长时间稳定性
- `convert_cobot_hdf5_to_lerobot.py` 对这批数据的转换

**离线**：315 项测试通过，安装校验 9/9。

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
python -m unittest discover -s tests/task2 -p "test_*.py"
./multi_arm_launch_tools/install_task2_teach_handover.sh --check
```

## 8. 待办

- **给 AgileX 报固件问题**：S-V1.7-3 上进入 `ctrl_mode=0x01` 后固定 0.5 秒抛
  `arm_status=0x05` + `err_code=0x003F` 再自愈，与指令频率、停留时长、使能状态无关。
  复现条件在 `2026-08-06-...-runbook.md` §10.8
- **磁盘**：录制是原始 uint8，约 82 MB/s（每分钟 4.9 GB）。2026-08-07 实测只剩
  137 GB，按这个码率约 27 分钟。要么清旧数据，要么给 `collector.py` 开压缩
- **左后臂夹爪**：三条 episode 里读数恒为 0（完全闭合）。硬件在报（200 Hz、
  status_code 正常），需要人工确认是没捏过还是卡住了
- **跟随误差**：拖后臂太快时前臂跟不上，实测有一条 episode 差到 0.93 rad(53°)。
  `action` 会记录机器人执行不了的指令，采集时慢一点或事后筛掉
- **gs_usb 槽位错乱**：会复发（已在两个适配器上各见过一次）。频繁的话再考虑
  驱动/内核，目前检测 + `down/up` 成本很低
- **运动参数漂移**：`can_rear_left` 是 200/250，另外三条臂是 300/500
- 这些文件**全部是 git 未跟踪状态**，还没提交过

---

## 9. 其他文档

| 用途 | 文档 |
|---|---|
| 让新模型支持接管（直接发给产出部署脚本的人/对话） | `2026-08-07-task2-new-model-integration-brief.md` |
| 照命令把机器跑起来（三种 profile） | `2026-08-07-task2-deployment-profiles.md` |
| 用 cobot-station 网页采集 | `2026-08-07-task2-cobot-station-collection.md` |
| 硬件实测、固件行为、排查记录 | `2026-08-06-task2-physical-teach-handover-runbook.md` |

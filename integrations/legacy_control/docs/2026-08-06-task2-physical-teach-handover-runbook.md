# Task2 物理示教接管：硬件实测、设计依据与分阶段验收

日期：2026-08-06　固件：`S-V1.7-3`（硬件 `H-V1.2-1`，构建 250708）　SDK：`piper_sdk 0.4.1`

本文记录 2026-08-06 一整天实机调试的全部结论。**第 3 节「Piper 固件实测行为」是全文最有价值的部分**——
那些行为没有出现在任何官方文档里，是这套方案能不能跑起来的决定因素，
也是后来者最容易重新踩一遍的坑。

---

## 1. 验收状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 只读反馈，协调器 PAUSED | ✅ 通过 |
| 2 | 左后臂物理示教按钮 | ✅ 通过 |
| 3 | 右后臂物理示教按钮 | ✅ 通过 |
| 4 | 双侧同时进出示教 | ✅ 通过 |
| 5 | 后臂 hold 与 motion_ready（首次带电） | ✅ 通过 |
| 6 | MANUAL 下后臂驱动前臂 + 完整 M→S 闭环 | ✅ 通过（单次） |
| — | 多轮 M/S 循环可重复性 | ⚠️ 受后臂工作区碰撞影响，需先调整任务姿态（见第 7 节） |
| 7 | π0.5 shadow 的 pause/fresh resume | ⬜ 未开始，需先移植适配器 |
| 8 | π0.5 live 完整 M/S | ⬜ 未开始 |

离线测试：**212 项全部通过**。

阶段 6 的实测数据（一次完整闭环）：

```
进入 MANUAL 瞬间   前臂位移 0.0001 rad      离合零点正确，无跳变
拖动中             前臂位移 0.0775 / 0.0957  双前臂 1:1 跟随
跟随误差           0.0097 / 0.0137 rad      约 0.6–0.8 度
前臂命令频率       149.8 / 150.8 Hz         高于手册要求的 100 Hz
按 S 后收敛        0.0003 / 0.0009 rad
```

---

## 2. 为什么不是方案一

2026-08-06 在两条后臂上实测，逐条记录：

| 观测项 | 结果 |
|---|---|
| `MasterSlaveConfig(0xFA/0xFC)` 无重启反复循环 | **可以**。每次 `0xFA` 立刻停止从臂反馈，每次 `0xFC` 完整恢复 1600 帧/秒 |
| 切主臂后出现 `0x155-0x159` 主臂控制帧 | **从未出现**，等待 10 秒无效 |
| 切换前先 `EnableArm(7)` | 切换动作反而把六个电机**全部失能** |
| 切换后再 `EnableArm(7)` ×5 | 总线零帧，指令无响应 |
| 切换后 `MotionCtrl_2` 激活运动模式 | 无响应 |
| `ReqMasterArmMoveToHome(0)` | 无响应（该指令需固件 ≥ V1.7-4） |
| 主臂模式下**人手拖动机械臂 5 秒** | **零帧** |

结论：`0xFA` 的运行时效果只是"写 Flash + 停止全部 CAN 输出"。真正的主臂行为要断电重启才生效，
**两个方向都需要重启**，不止 SDK `piper_set_slave.py` 注释提到的 slave 方向。
人在环每次接管都要重启后臂，任务过程中做不到（断电即失力下坠）。

顺带解释了一个此前无法定位的现象：四条臂在总线上完全静默、只应答 `0x4AF` 固件查询——
那是被上一次实验留在了主臂模式，只有整机断电重启能救回来。

若日后升级到固件 ≥ V1.7-4，用现成探针一条命令即可复验：

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools
/home/agilex/miniconda3/envs/aloha/bin/python task2_probe_master_slave_cycle.py \
    --can can_rear_left --variant all
```

其中 `h-home-0x191` 配方就是为固件升级后准备的。

---

## 3. Piper 固件实测行为（S-V1.7-3）

**这一节是整个实现的基础。每一条都是实机测出来的，都不在官方文档里，
而且每一条都曾经导致过一次失败。**

### 3.1 示教模式

| 行为 | 实测 |
|---|---|
| 示教按钮是**切换式** | 按一下进、再按一下退，不需要按住。`teach_status` 在操作员双手离开按钮、正在拖臂的 15 秒里持续为 `0x01` |
| 示教中反馈实时跟随 | `0x2A2-0x2A4` 持续输出真实关节角，峰值实测 1.18 rad（67°）连续跟踪 |
| 退出示教后状态**粘滞** | `ctrl_mode` 停在 `0x02`，`teach_status` 停在 `0x02`，不会自己回 `0x00` |
| 清除粘滞状态 | 只有 `MotionCtrl_1(0x02,0,0)` 能把 `teach_status` 清成 `0x00` |
| **`MotionCtrl_1(0x02,0,0)` 会把六个电机全部失能** | 隔离实验确认：`111111 → 000000`，0.8 秒内 |
| **但它根本不需要发** | 见 3.1.1。发它换来的只是每次 M/S 都让机械臂自由落体 |
| 进出示教**不失能** | 隔离实验中进入示教、拖动、退出，全程 `使能=111111` |
| 进入示教后臂会下沉 | 实测 0.245 rad（14°）到 **0.84 rad（48°）**。是示教模式重力补偿的静差，不是断电 |
| 跨臂零串扰 | 拖动一条后臂时，其余四条臂位移 0.000–0.002 rad |

### 3.1.1 `teach_status` 的粘滞状态不需要清除（重要修正）

早期实现以为必须先用 `MotionCtrl_1(0x02,0,0)` 把 `teach_status` 清零才能进位置控制，
为此付出了**每次 S 都失能掉落**的代价。两个隔离实验推翻了这个前提：

| 实验 | 结果 |
|---|---|
| 使能到 `111111` 后单发 `MotionCtrl_1(0x02,0,0)` | 0.8 秒内 `000000`。**确认它会失能** |
| 真实示教残留（`ctrl_mode=0x02, teach_status=0x02`）下跳过它 | `MotionCtrl_2(0x00)` → `MotionCtrl_2(0x01)`，**0.3 秒进入 CAN 控制，全程不失能** |

`teach_status = 0x02` 是一个**状态**（已结束记录），不是错误，不阻塞任何东西。
示教判定只认 `0x01`，留着 `0x02` 完全无害。

顺带：`MotionCtrl_1(0x00, 0x00, 0x02)`（grag_teach_ctrl「结束示教记录」）**不清除**残留，
它反而**把 `teach_status` 设成 0x02**。它不是清理指令。

**教训**：这个"必须清残留"的前提从第一天就被当成了既定事实，后续所有努力
（提前使能、缩短失能窗口、持续重试）都是在优化一个本该删掉的操作。
从现象倒推得出的因果，一定要单独做隔离实验验证。

### 3.2 控制模式与使能

| 行为 | 实测 |
|---|---|
| **不存在"指令中断超时"看门狗** | 没有任何指令的情况下，臂可以无限期停在 `ctrl_mode=0x01` |
| 使能会激发一段**自清的通信异常** | `arm_status=0x05`（JOINT_COMMUNICATION_ERR）+ `err_code` 六个关节全置位，约 100 ms 自清 |
| 该瞬态期间臂会掉回 `0x00` 并失能 | 错误自清后**臂不会自己回到 `0x01`，也不会自己重新使能** |
| 原厂驱动的应对 | `piper_start_ms_node.py` 在**每一条指令前后各发一次** `MotionCtrl_2(0x01,0x01,100)`。前臂从不搁浅就是因为这个 |
| 原厂驱动**不做**持续重使能 | 它的使能循环只在启动时跑一次。前臂不需要——它从不进示教模式，也就不走"失能→重使能"这条路 |
| `motion_ready ≠ 到达目标` | 进入 `0x01` 并接受目标后，0.24 rad 的运动要一两秒才走完 |
| `bus_current` 字段不上报 | 所有电机、所有时刻恒为 0，**不能用来判断负载** |
| **掉出 CAN 控制模式会失能整臂六个电机** | 与单关节保护跳闸完全不同：后者只关掉出问题的那一个 |
| 两种失能的区分 | 六个 + 无标志 + `ctrl_mode=0x00` = 模式掉线，**软件可恢复**；单个 + 有锁存标志 = 保护跳闸，**必须断电** |

### 3.3 进入位置控制的完整序列

缺任何一步都进不去 `ctrl_mode=0x01`（实测：直接发 `MotionCtrl_2(0x01,...)` 时模式原地不动、电机被失能）：

```text
MotionCtrl_1(0x02, 0, 0)            清 teach_status（会失能电机）
  → 立刻开始 EnableArm(7)，并在后续所有等待中持续重试
MotionCtrl_2(0x00, 0x00, 0, 0x00)   位置速度模式（待机）
EnableArm(7) + 确认六个电机
等待 arm_status==0x00 且 err_code==0 持续 0.3 秒   吸收使能瞬态
JointCtrl(hold)                     预装目标，防止执行旧目标造成跳变
MotionCtrl_2(0x01, 0x01, spd, 0x00) 进 CAN 控制；等待期间持续重发目标
确认 ctrl_mode==0x01 且六电机仍使能  → motion_ready
此后 100 Hz 持续重发目标，且每次都重新断言 MotionCtrl_2
```

### 3.4 SDK 接口速查

**写**：`MotionCtrl_1`(0x150) 复位 · `MotionCtrl_2`(0x151) 切模式 ·
`JointCtrl`(0x155/6/7) 六关节目标 · `GripperCtrl`(0x159) 夹爪 · `EnableArm`(0x471) 使能

**读**（读 SDK 解析缓存，不发帧、不阻塞）：`GetArmStatus()` 含 `ctrl_mode`/`teach_status`/`arm_status`/`err_code` ·
`GetArmJointMsgs()` 实测关节角 · `GetArmGripperMsgs()` 夹爪 ·
`GetArmLowSpdInfoMsgs()` **每个电机的使能位、温度、保护标志**

每个返回值带 `Hz` 和 `time_stamp`；所有安全判据都建立在"只信新鲜数据"上。

**关键**：`GetArmStatus()` 的整臂状态可以是 `0x00` 健康，而 `GetArmLowSpdInfoMsgs()` 里
某个电机已经锁存了 `driver_error_status`。**任何决定要不要重新使能的逻辑必须看电机级，不能只看整臂级。**

---

## 4. 设计决策与依据

### 4.1 MANUAL 用增量（离合）映射，不是绝对映射

按下示教按钮会让后臂失力下沉 0.2–0.84 rad。若前臂追随后臂的**绝对姿态**，
接管一开始任务臂就要自己走十几到几十度去匹配那个下沉——**一次无人指令的大幅运动**，比报错危险得多。

因此按下按钮那一刻冻结一个离合参考点：

```text
front_ref = 前臂当时姿态
rear_ref  = 后臂当时姿态（已下沉，多少都无所谓）

MANUAL 期间：front_目标 = front_ref + (rear_当前 − rear_ref)
```

- 下沉被吸收，不传给前臂
- 你拖多少前臂走多少
- 接管瞬间零跳变（此刻 `rear_当前 == rear_ref`，位移为零）
- 代价：MANUAL 期间双手位置与前臂存在固定偏移，按 S 后由 hold 清除

### 4.2 三档同步判据，各有各的前提

| 时机 | 参数 | 默认 | 判什么 |
|---|---|---|---|
| M 键入口 | `joint_sync_tolerance_rad` | 0.05 rad | 四臂收同一条 policy 指令，本该对得很准 |
| 按 S（从 MANUAL） | `tracking_tolerance_rad` | 0.10 rad | **离合不变量**：`(前−后)现在 ≈ (前−后)参考`，即"前臂 1:1 跟住了" |
| 发 hold 前（任何来源） | `initial_sync_tolerance_rad` | 0.60 rad | hold 位移上限，低速 + 有人扶 |
| hold 执行完之后 | `resync_tolerance_rad` | 0.10 rad | 纠偏之后必须对得准 |
| 夹爪（始终） | `gripper_sync_tolerance_m` | 0.015 m | **绝对判据**：夹爪不受重力影响，不会下沉 |

**为什么按 S 不能用绝对判据**：改成离合映射后，MANUAL 期间前后臂之间**天然存在**等于下沉量的偏移。
实测一次下沉 0.84 rad，绝对判据必然拒绝一次完全正常的接管。真正该守的是偏移量没有漂移。

**为什么冷启动要宽容差**：开机时后臂停在哪儿是随机的，与前臂没有任何关系。
hold 本来就是纠偏手段，要求纠偏之前已经对准是倒因为果。
早期版本用统一的 0.10 rad，导致每次开机都要手动把耦合的腕关节拧到 0.1 rad 以内——不可行。

### 4.3 M 和 S 的按键顺序相反，由代码强制

```text
M：先按键盘 M，再各按一下两条后臂的示教按钮
S：先各按一下两条后臂的示教按钮退出示教，再按键盘 S
```

- **M 必须键盘在先** —— policy 要先停下来，人才能安全去抓后臂。M 之后协调器**等待**操作员（`teach_wait_timeout_sec`，60 s）
- **S 必须按钮在先** —— 后臂要先退出示教、停稳、重新对齐。S **不等待操作员**，只做前置条件检查（`teach_confirm_timeout_sec`，2 s）

按 S 时若还在示教模式，请求被**软拒绝，不 FAULT**：

```text
rear arms are still in drag teaching; press each rear teach button once
to leave teaching, then press S again
```

协调器留在 MANUAL、前臂继续跟随，退出示教后再按一次 S 即可。
把常见时序错误做成软拒绝而非硬故障，是为了避免动不动就要 `reset_fault` 重走流程。

### 4.4 MANUAL 期间全程持续转发，不检查 `teach_active`

示教按钮是切换式，两条后臂**必然一先一后**退出（人要走过去按第二个）。
早期版本要求"两侧都在示教中才转发"，会在第一条臂退出的瞬间切断跟随，
而第二条臂此时仍是软的、人走过去碰它就会带偏，前臂不跟 → 失同步 → 随后的 hold 校验 FAULT。

持续转发是安全的：这段窗口里 policy 已暂停，没有任何东西和人工输入竞争前臂；
退出示教的后臂是使能保持住的，前臂只会跟着它停下来。

**离合参考点的生命周期**必须活过按 S 那一刻，直到真正发出 hold（进入 `ARMING_POLICY`）才丢弃。
早期把它和"清空指令缓冲"放在同一个函数里，导致按 S 瞬间参考点就没了——
既制造了新故障（冷启动时缺参考点），又没达成原目标（跟随立刻中断）。

### 4.5 MANUAL 的转发不能用命令配对原语

`PairBuffer` 的语义是"左右必须严格交替，同侧连来两次即错"，因为它是给模型输出的**成对指令**设计的。
但两条后臂是各自独立的 200 Hz 传感器流，到达顺序随机，"同侧连来两次"是常态。

MANUAL 改为：各侧保留**最新**样本，两侧都新鲜且时间戳偏差在限内时发一对，限流 200 Hz。
偶发抖动**跳过不发**（前臂原地不动），而不是升级成故障——
200 Hz 传感器流的抖动是常态，为此 fault 会让系统没法用。

### 4.6 自愈与故障的边界

| 情况 | 处置 |
|---|---|
| 掉出 CAN 控制模式 | **自愈**：每条指令都重新断言 `MotionCtrl_2`（照抄原厂做法） |
| 短暂掉线（< `ready_grace_sec` 3.0 s） | **不报错**，只打警告，等指令流治好它 |
| 失去使能，但机械臂报告健康 | **自愈**：以 5 Hz 重试 `EnableArm(7)` |
| 失去使能，且**任一电机锁存保护标志** | **绝不重试**。它因过流/碰撞/堵转跳闸是有原因的，反复使能只会重复触发 |
| 持续掉线超过宽限窗口 | FAULT，故障信息带**具体哪个电机 + 哪些标志** |
| 操作员按下示教按钮 | **立刻停止指令流**（判据用原始 `ctrl_mode`，5 ms 内响应，不能等 0.2 s 防抖）——操作员的物理动作永远优先于软件保活 |
| **反馈不新鲜导致 READY 丢失** | 必须**报故障**。早期实现只是静默置 `motion_ready=False`，不打日志、不报错、不参与宽限窗口 |
| **协调器持续监控 `motion_ready`** | 在 `ARMING_POLICY`/`RESUMING`/`POLICY` 期间任一后臂失去就绪立即 FAULT。`PAUSED`（未武装）和 `MANUAL`（臂归操作员）下不就绪是正常的 |

**静默失效是本次发现的最严重的安全洞**：一条后臂停止执行指令，驱动不吭声、
协调器仍显示 `resuming` 且 `fault` 为空。推理跑起来后四臂会持续失同步而无人知晓，
等操作员按 M 想接管时才发现后臂位置早已跑偏——那时人已经伸手过去了。
驱动日志里一条 WARN 都没有，只能靠对比硬件寄存器倒推，排查代价极高。

### 4.7 锁的划分

- `_hardware_lock` 只保护**写操作**（`JointCtrl`/`MotionCtrl_*`/`EnableArm`），武装期间会持有数秒
- `_state_lock` 保护本节点自己的门控和示教跟踪状态
- SDK 的 `Get*` 是读解析缓存，**两把锁都不需要**

早期让发布循环去抢 `_hardware_lock`，导致武装期间 `joint_states` 黑屏 **0.77 秒**，
协调器判定反馈过期 → 首次武装必然失败。

### 4.8 失能窗口必须最小化

`MotionCtrl_1(0x02,0,0)` 失能电机后，早期版本要等两个模式确认跑完才重新使能，
臂**自由落体半秒到一秒**。现在复位帧一发完立刻开始重使能，并在后续所有等待轮询中持续尝试。

`auto_enable=false` 时**绝不**擅自使能——那是显式的"不许碰"指令；
重新上电的过程中**绝不**附带位置指令（只上电，不动作）。

---

## 5. 交付文件

Teleop 仓库（可追踪源码）：

```text
multi_arm_launch_tools/
├── task2_teach_driver/
│   ├── task2_rear_teach_core.py            门控与示教防抖（无 ROS 依赖）
│   └── piper_rear_teach_task2_node.py      后臂 ROS1 驱动
├── task2_teach_handover/
│   ├── task2_teach_handover_core.py        状态机
│   ├── task2_teach_handover_node.py        协调器
│   ├── task2_teach_handover_keyboard.py    M/S/Q 单键客户端
│   └── task2_teach_acceptance_pause_stub.py  验收专用 pause 服务桩
├── launch/start_ms_piper_5arm_handover_teach_task2.launch
├── install_task2_teach_handover.sh
├── task2_probe_master_slave_cycle.py       方案一诊断探针（固件升级后复验用）
└── task2_policy_adapters/pi05/interface_task2_teach_live.sh

tests/task2/
├── test_teach_core.py                      核心逻辑
├── test_rear_teach_node.py                 驱动（含全部实机 bug 的回归）
└── test_teach_handover_node.py             协调器 M/S 流程
```

运行安装位置：`Piper_ros_private-ros-noetic/src/piper/scripts/`。

`task2_teach_handover_core.py` **import** 了 `task2_handover_core` 的
`validate_joint` / `PairBuffer` / `arms_are_synchronized`，不是复制。
因此安装 teach 版之前必须先装好 `install_task2_handover.sh --install`，teach 安装脚本会检查这个前置条件。

后双臂**永远是从臂身份**，运行期一次 Flash 都不写。一条 AST 断言测试强制这一点：
teach 驱动与协调器不得出现 `MasterSlaveConfig`、`ReqMasterArmMoveToHome`、`GetArmJointCtrl` 调用。

### 验收专用 pause 桩

协调器拒绝把控制权交还给一个无法确认已暂停的 policy。这个检查是对的、必须保留，
但阶段 1–6 故意不启动模型，就没有东西回答它。`task2_teach_acceptance_pause_stub.py`
只应答服务、**不发布任何话题**，因此不可能让任何机械臂动起来。
阶段 7–8 必须换成真实的 policy 适配器；两者同时运行会抢同一个服务名，那次运行作废。

**注意**：`roslaunch` 会带起新的 ROS master，桩会掉线，每次重启 launch 后都要重新拉起。

---

## 6. 控制边界

```text
π0.5 → /task2/policy/joint_left,right
               ↓
        task2_teach_handover_node          ← 四臂软件指令的唯一发布源
   POLICY: → /master/joint_{left,right}                前双臂
           → /task2/teach/rear_{left,right}/joint_cmd  后双臂（保持同步）
   MANUAL: /task2/teach/rear_*/joint_states → 离合映射 → /master/joint_*  仅前双臂
```

---

## 7. 后臂工作区碰撞：方案二引入的新约束

**2026-08-06 现场排查，根因为机械干涉，非硬件故障。**

### 现象

后左臂 joint 5 在武装、向 hold 目标移动的途中反复堵转跳闸，锁存
`driver_overcurrent` + `stall_status` + `collision_status` + `driver_error_status`，
电机失能，软件 `EnableArm` 无法恢复，j5 关节指示灯由绿闪转为**红闪**。
断电重启可清除锁存与灯码，再次加载即复现。当日复现 5 次。

### 根因

**后左臂下方的握把顶在支架柱子上。** 前臂当时的作业姿态偏低，
镜像到后臂后，后臂的夹爪向下探，与支架结构发生机械干涉。
关节持续对抗一个它推不动的障碍 → 过流 → 保护跳闸。

`collision_status` 这个标志字面正确：**它报的就是真实碰撞**。

### 这条排查走过的弯路（值得记住）

当时收集到的证据一度全部指向"驱动板硬件故障"：

| 观测 | 当时的解读 | 实际 |
|---|---|---|
| 断电后手动可自由转动 | 排除机械阻塞 | **试的是臂离开碰撞位置之后**，那个角度才碰 |
| 26–36°C 凉态即复现 | 排除温度 | 正确，但与根因无关 |
| 手转顿挫感与右臂一致 | 排除电机绕组 | 正确，但与根因无关 |
| 右臂同关节 69°C 正常 | 指向左臂个体缺陷 | **右臂位姿没有干涉** |
| 一天内劣化（1 分钟 → 5 秒） | 硬件恶化 | **前臂姿态在过程中变低了，接触更早** |
| 首次报 `motor_overheating` | 传感器虚报 | 长时间顶着障碍确实会发热 |

**教训**：控制器报「碰撞」时，先去工作区找碰撞，再怀疑传感器。
诊断应当从"整条臂在环境里是什么姿态"开始，而不是一头扎进单个关节的寄存器。
差一点就把一条完好的机械臂送去返修。

### 由此确立的设计约束

> **后臂与前臂的关节角完全相同**（POLICY 阶段协调器把同一对指令发给四条臂），
> 但两者处在**不同的物理环境**中：前臂在任务台上方作业，后臂装在支架上。
>
> **同一组关节角，在前臂那边无碰撞，不代表在后臂那边也无碰撞。**

这是方案二引入的新约束。原三臂方案里后臂由人拖动，操作员会自然避开障碍；
现在后臂被指令去镜像前臂，**它不会自己躲**。

**部署前必须确认**：任务的整个工作姿态范围，对
**前臂工作区**和**后臂支架区**同时无碰撞。

j5 是腕部俯仰轴，决定夹爪朝下还是朝前。前臂姿态偏低 → j5 角度大 →
后臂夹爪下探 → 最容易撞到支架下方结构。**它是最先报警的关节，也是排查时最该先看的。**

### 排查清单

后臂关节报 `collision_status` / `stall_status` 时，按顺序检查：

1. **看整条后臂在支架上的姿态**，找它顶住了什么——柱子、线槽、另一条臂、桌面
2. 对照前臂当时的姿态：后臂正是被指令到这个姿态的
3. 抬高/调整前臂的作业姿态，使镜像到后臂时不产生干涉
4. 只有以上都排除，才考虑硬件问题

### 附带观察：j5 是负荷最重的关节

`m5` 在两条臂上都是最热的（实测右臂达 **69°C**，其余关节 33–41°C）。
j5 持续对抗重力托住夹爪，而本方案要求后臂长时间保持武装、锁住姿态。

**建议**：任务起始姿态让 j5 尽量接近中立（手腕伸直），
既降低 m5 温度，也减小后臂下探撞到支架的风险。

## 8. 另一处待处理：运动参数配置漂移

```
                最大速度    最大加速度
can_left          300         500
can_right         300         500
can_rear_left     200         250     ← 只有它不同
can_rear_right    300         500
```

六个关节全都是这个规律。**这不会造成故障**（更慢更柔），但会导致推理时左后臂跟不上其余三条臂的节奏，
四臂同步会有系统性偏差。应统一。

碰撞防护等级四条臂一致，都是 **1**（`0=不检测，1–8 越大越迟钝`），即全部设在最灵敏档。
目前没有证据表明它造成了问题，但记录在此。

---

## 9. 每次上电前检查

1. 五个臂均有人扶持，急停可立即按下。
2. 六路 CAN 全部 UP：`can0`、`can_left`、`can_right`、`can_mid`、`can_rear_left`、`can_rear_right`。
3. 后臂多股铜线完整压接，无散丝、裸铜短接或松动；航空插头插牢。
4. 没有遗留的 roslaunch、旧 inference、tmux 或 tmux 内后台节点。
5. `rostopic info /master/joint_left` 与 `right` 上没有未知 publisher。
6. **启动 launch 时扶住后双臂**：若上次留下示教残留，驱动会清残留并短暂失能（已最小化，但仍有瞬间）。
7. 一旦下电，继续扶住五个臂直到确认不会坠落。

工控机重启后 CAN 会回到未配置状态，必须重跑：

```bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
bash multi_arm_launch_tools/can_config_task2.sh
```

机械臂单独断电重启**不影响** CAN（USB-CAN 模块由 USB 供电），不需要重配。

---

## 10. 终端布局

### 终端 1：CAN

```bash
conda activate aloha
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
bash multi_arm_launch_tools/can_config_task2.sh
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
ip -br link show type can
```

### 终端 2：五臂驱动与协调器

实机第 1 阶段必须禁止自动使能：

```bash
roslaunch \
  /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_handover_teach_task2.launch \
  front_auto_enable:=false mid_auto_enable:=false rear_auto_enable:=false
```

阶段 5 起改为 `front_auto_enable:=true rear_auto_enable:=true`（中臂是相机臂，始终不参与）。

### 终端 3–4：相机与中路相机预设位姿

```bash
roslaunch .../multi_camera_shuai.launch
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop && python teleop/fix_mid_camera_pose.py
```

### 终端 5：π0.5 与单键控制（仅在阶段 1–7 通过后）

```bash
cd /home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05
./interface_task2_teach_live.sh 2000
```

---

## 11. 操作时序

```text
M  -> 协调器暂停 policy、校验四臂同步(0.05 rad) -> 提示 "... TO ENTER"
   -> 操作员各按一下左右后臂示教按钮进入示教 -> MANUAL，后臂反馈经离合映射驱动前臂
S  -> (前置：操作员已各按一下两侧按钮退出示教)
   -> 协调器确认两侧均已退出 -> 校验离合不变量(0.10 rad)
   -> 发 hold 给后双臂 -> 等待收敛(最多 15 s) -> policy fresh resume
   -> 第一对新动作同时发四臂 -> POLICY
```

MANUAL 期间松开一侧按钮**不报错**，前臂继续跟随；两侧都退出后前臂跟着停下。

---

## 12. 故障处理

```bash
rostopic echo -n 1 /task2/teach_handover/mode
rostopic echo -n 1 /task2/teach_handover/fault
rostopic echo -n 1 /task2/teach_handover/prompt
rostopic echo -n 1 /task2/teach/rear_left/fault
rostopic echo -n 1 /task2/teach/rear_right/fault
```

`reset_fault` 清软件锁并回到 PAUSED，**并连带清除两个后臂驱动的 fault**
（早期版本只清协调器，导致驱动永久卡死、必须重启 launch）：

```bash
rosservice call /task2/teach_handover/reset_fault
```

它**不会**退出物理示教、不使能、不回零、不切换任何 Piper 身份、不移动机械臂。
只有亲眼确认五臂物理状态与工作区安全后才能调用。清除后仍需重新 `request_policy` 武装后臂。

### 故障信息现在会自带定位信息

```text
front/rear differ by 0.1234 rad at left j4, limit 0.1000
feedback older than 0.100s: front_left=0.253s
left j5 did not track: clutch offset drifted 0.2100 rad, limit 0.1000
armed arm lost motor enable: motor(s) 5 disabled (111101)
  [motor 5: driver_overcurrent, collision_status, driver_error_status, stall_status]
arms did not converge within 15.0s: front/rear differ by 0.2424 rad at right j4
```

**一个不能告诉你为什么跳闸的安全判据，现场要浪费大量时间。**
本文件记录的多个根因都是在错误信息加上具体数字之后立刻定位的。

### 电机锁存保护如何清除

若某个电机报 `driver_error_status` / `stall_status` / `driver_overcurrent`，
软件的 `EnableArm` **在保护状态下不被接受**，必须**给那条臂断电至少 10 秒**再上电。

---

## 13. 剩余工作

1. **多轮 M/S 循环可重复性验证** —— 被第 7 节的 j5 故障阻塞
2. **移植 π0.5 适配器**到 `junfeng/workspace/pi05_cobot`
   （现有 `task2_policy_adapters/pi05/` 接的是 `cobot_magic/task3/jiaan/deployments/in_the_pot/pi05`，
   不是日常跑 put_fruit 的那套。该部署的 `robot/inference_pi05.py` 结构一致、
   `inference_pi05.sh` 已暴露 `--puppet-arm-*-cmd-topic`，移植量不大）
3. 阶段 7：π0.5 shadow 的 pause / fresh resume
4. 阶段 8：π0.5 live 完整 M/S
5. 统一四条臂的最大速度/加速度配置（第 8 节）
6. 向松灵确认固件 ≥ V1.7-4 的可行性（第 2 节）
7. 调整任务起始姿态，使后臂镜像时不与支架干涉（第 7 节）

## 10. 2026-08-07 重大修正：CAN 控制进入是掉落的唯一来源，方案改为按钮直驱

本节推翻了本文档 §3.3、§4.1、§4.3、§4.8 的部分结论。上面那些小节保留原样，因为
它们记录的实测数据仍然有效，只是当时对数据的解释错了。

### 10.1 固件的 0.5 秒关节通信故障

**现象**（50 Hz 全程采样，四轮独立复现，时间点从未偏过）：

```
t+0.000s  MotionCtrl_2(0x01) 生效，ctrl_mode = 0x01
t+0.495s  arm_status = 0x05 (JOINT_COMMUNICATION_ERR), err_code = 0x0001
t+0.510s  err_code = 0x003F（六个关节全部置位），被踢回 ctrl_mode = 0x00
t+0.600s  自愈，回到 ctrl_mode = 0x01，err_code = 0x0000
```

期间约 80~130 毫秒内关节读数是坏帧（相邻 20 毫秒采样跳变 16~18 度，物理上不可能），
伺服无力矩，手臂自由下坠然后被顶回原位。操作员看到的就是"掉下去又回来"。

**逐条排除的假设**（每条都有实测，不是推理）：

| 假设 | 做法 | 结果 |
|---|---|---|
| 指令频率打爆总线 | ride-through 从 20 Hz 降到 4 Hz | 故障时间点完全不变 |
| 待机停留不够长 | 无条件停留 1.0 秒 | 停留期间干净，故障在进入之后 |
| 旧退出指令的残留 | `MotionCtrl_1(0x02)` 换成 `(0,0,0x00)` | 故障照旧 |
| 关节需要重新初始化 | 待机期间无条件补发 `EnableArm(7)` | 故障照旧，0.495 秒 |
| 顶住使能就不会掉 | 50 Hz 使能广播，实测使能位全程 111111 | **操作员报告掉落幅度没有任何变化** |

最后一条是关键：**使能位是状态字，不是力矩保证**。关节通信断开时驱动器收不到指令，
使能位仍然是 1 而手臂已经软了。任何"盯着使能位"的缓解措施都是无效的。

### 10.2 待机（`ctrl_mode = 0x00`）由抱闸保持，不掉

每一轮轨迹里的待机停留段：六个关节位移 0.00 度，`err_code = 0x0000`，电机保持使能。
右后臂曾在待机状态放置十几分钟未移动。

**§4.8 里"待机不保持位置"的说法是错的**，它当时是从"删掉停留后掉落变严重"反推出来
的，没有直接测过。

### 10.3 结论：不要进 CAN 控制

后臂进 CAN 控制的唯一理由，是让操作员的手位和前臂对齐。而 §4.1 的离合映射在接管
瞬间抓取前后臂偏移，本来就不要求两者对齐——这个跟随是绝对映射时代的遗产。

去掉它，M/S 循环里就没有任何一次 CAN 控制进入，故障连触发的机会都没有。

### 10.4 空闲态用失能而不是待机

待机能解决掉落，但带来一个新问题：抱闸锁死，操作员**无法徒手把后臂搬到舒服的位置**。
而离合参考是在按下示教按钮那一刻抓取的，所以任何重新摆位都必须发生在按钮按下之前，
也就是必须发生在一个"能用手推、但不转发"的状态里。只有失能能提供这个状态。

失能的后臂会在重力下垂落。这是**输入设备的静止姿态**，不是"运行中的手臂掉了"，两者
的风险完全不同。

### 10.5 按钮直驱：M 和 S 被删除

示教按钮本身就是操作员意图的明确表达，再要求他按 M 授权一次是多余的，而且 §4.3 记录
的那个强制顺序本身就是这个多余环节带来的负担。

```
空闲      两条后臂失能（软），前臂跑推理
按下 X 侧示教按钮
          → 推理全局暂停、动作块清空
          → 抓取该侧离合参考，X 前臂跟随 X 后臂
          → 另一侧前臂定住不动，其后臂仍然软着
再按一下  → 该侧后臂立刻收到 DisableArm，变软
          → 该侧前臂停在最后位置
          → 没有任何按钮还按着 → 推理自动恢复
```

接管是**每侧独立**的，但推理暂停是**全局**的：未被接管的那条手臂不能继续执行一个
为"操作员正在用手改变的世界"制定的计划。

### 10.6 实测验证（2026-08-07）

211 秒连续采样，左臂 5 个完整接管周期，右臂 1 个：

```
can_rear_left   10003 样本   进过 ctrl_mode=0x01 的样本: 0   armst/err 异常样本: 0
can_rear_right  10003 样本   进过 ctrl_mode=0x01 的样本: 0   armst/err 异常样本: 0
```

操作员报告：后臂可自由搬动、示教后前臂同步、退出示教后自动失能可再次搬动、增量映射
正确、全程无掉落。

按钮按下时固件**逐个关节**上电（`000000 → 100000 → 100100 → 100110 → 111111`，约
110 毫秒），退出时驱动发 `DisableArm(7)` 后同样逐个卸力（约 80 毫秒）。这个逐关节的
特性也解释了 §10.1 中一次通信中断为何能让整条手臂失力。

### 10.7 交付物变化

| 文件 | 说明 |
|---|---|
| `task2_teach_handover/task2_teach_button_node.py` | 新协调器，按钮直驱、每侧独立、无 M/S |
| `launch/start_ms_piper_5arm_button_teach_task2.launch` | 对应的 launch |
| `task2_teach_driver/piper_rear_teach_task2_node.py` | 新增 `~idle_disabled`（默认真），退出示教立即失能 |
| `tests/task2/test_teach_button_node.py` | 19 项 |

旧的 M/S 协调器（`task2_teach_handover_node.py`）和它的 launch 保留未删，其测试仍然
全部通过，作为固件修复后的回退选项。

### 10.8 仍未解决，应向 AgileX 反馈

§10.1 的故障本身没有被修复，只是被绕开了。固件 S-V1.7-3 / 硬件 H-V1.2-1 / build
250708 上，进入 `ctrl_mode = 0x01` 后固定 0.5 秒抛出 `arm_status = 0x05` +
`err_code = 0x003F` 再自愈，与指令频率、停留时长、使能状态均无关。若后续需要后臂在
推理期间跟随前臂（例如换用绝对映射），必须先解决它。

### 10.9 这条排查走过的弯路

- 把裸脚本的测量结果直接当成完整驱动链路的结论，导致"1 秒待机停留解决了掉落"这个
  错误结论被报告出去，实际它只是把猛烈掉落换成了缓慢下沉
- 用首尾两点位置对比判断"没掉落"，而实际现象是"掉下去又回到原位"，这种方法在原理上
  就看不见它。操作员的肉眼观察三次纠正了采样得出的错误结论
- ride-through 里的 `EnableArm` 广播被当成"抢救一个不存在的问题"删掉，删掉后失能变
  得可见且更严重——它确实在做事，只是做的不是注释里写的那件事

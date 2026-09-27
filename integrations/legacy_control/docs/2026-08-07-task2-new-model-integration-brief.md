# 让新模型的部署脚本支持示教接管 —— 交给策略产出方的说明

> **这份文档是给"负责产出部署脚本的人/对话"看的。**
> 直接把这个文件路径发给它就行，不需要额外解释。
>
> 机器人：`agilex@10.7.132.66`
> 本文件：`/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/docs/2026-08-07-task2-new-model-integration-brief.md`

---

## 1. 你要做什么

这台机器（Cobot Magic，4 条 AgileX Piper 臂 + 1 条相机臂）已经支持**物理示教接管**：
π0.5 推理跑着的时候，操作员按一下后臂上的示教按钮就接管那一侧的前臂，改完再按一下交还，
推理自动继续。

**机器人侧全部做好了，不用你碰。** 你只需要让**策略侧**满足下面的接口契约。

你要产出两样东西：

1. **一个暂停感知的推理客户端** —— 照抄参考实现的模式，改动约 100 行
2. **一个部署入口 `.sh`** —— 复制模板，改三个变量

**注意：光改 `.sh` 是不够的。** `.sh` 是最简单的部分。真正的工作在推理客户端进程里。

---

## 2. 先读这三个文件

| 路径 | 用途 |
|---|---|
| `.../pi05/common/robot/inference_pi05_rtc_task2.py` | **参考实现，照着改**。如果新模型也是 pi0.5 RTC 那套 runtime，直接抄它的模式 |
| `.../multi_arm_launch_tools/task2_policy_deploy_template.sh` | `.sh` 模板，改三个变量即可 |
| `.../multi_arm_launch_tools/docs/2026-08-07-task2-integration-contract.md` | 完整契约（§4）和项目背景。有疑问查这份 |

完整路径前缀：
- `.../pi05/` = `/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05/`
- `.../multi_arm_launch_tools/` = `/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/`

---

## 3. 接口契约：6 条

| # | 要求 |
|---|---|
| 1 | 提供 ROS 服务 `/task2/policy/set_paused`（`std_srvs/SetBool`） |
| 2 | **启动后处于暂停状态**，不发布任何指令，等操作员显式恢复 |
| 3 | 关节指令发到 `/task2/policy/joint_left` 和 `/task2/policy/joint_right`。**绝对不要直接发 `/master/joint_*`** —— 那是协调器的话题，它是前臂唯一的指令源 |
| 4 | 消息是 `sensor_msgs/JointState`，`name = [joint0..joint6]`，`position` **7 个值**（6 关节 + 夹爪），带有效 `header.stamp` |
| 5 | `set_paused(true)`：立即停止发布，并**丢弃已排队的动作** |
| 6 | `set_paused(false)`：用**全新观测**重新规划，并把步长限幅锚定在**实测关节位置**（`/puppet/joint_*`），不是自己上一条指令 |

另外三条禁忌：

- **不要**在被恢复之前把手臂开到预设初始位姿（`USE_INIT_POSE` 必须为 `false`）。手臂在哪由操作员决定
- **不要**同时跑两个提供 `/task2/policy/set_paused` 的进程，协调器会连到后注册的那个
- **不要**假设后臂和前臂位置一致。映射是离合式增量，它们本来就不一致

---

## 4. 第 5、6 条展开：暂停时必做的三件事

**这是最容易漏、后果最直接的部分。** 三件事必须一起做，少一件手臂就会在交还瞬间跳。

### a) 停止发布

不再往指令话题发东西。

### b) 丢弃动作块

暂停期间攒下的动作是为"操作员介入前的世界"算的。恢复时执行它们，等于按几秒前的场景动手。

**如果用 `AsyncRTCController`：必须 `close()` 之后重建一个新的，不能用 `reset()`。**

`reset()` 只清动作队列和标志位，**不停后台推理线程**。下一次 `initialize()` 又起一个，
两个 worker 抢同一个唤醒事件，输的那个在 `begin_inference()` 撞上"推理已在途"抛异常
→ `_fail()` → `sink.safe_stop()` → `rospy.signal_shutdown()`。

主循环的退出条件是 `not rospy.is_shutdown()`，于是**干净地走完 `finally` 返回 0**。

症状极具迷惑性：**交还后手臂动一个 chunk（约 1.2 秒）就停，整个进程"正常"退出，一行报错都没有。**

正确做法：

```python
# 暂停时
controller.close(timeout=args.close_timeout)   # 会 join 掉推理线程
controller = None

# 恢复时
episode += 1
observation = ros.wait_for_observation()        # 阻塞等一帧全新的
controller = new_controller(episode)            # 全新实例，新 session_id
controller.initialize(observation)
```

### c) 清空 `last_command`

步长限幅器是从上一条**指令**开始爬的。接管之后，那条指令描述的是手臂**原来**的位置。
不清它，恢复瞬间限幅器会朝旧位置爬，手臂往回跳。

```python
ros.last_command = None
# 顺带清掉观测缓存，强制用接管之后的新帧
ros.front_images.clear(); ros.left_images.clear(); ros.right_images.clear()
ros.left_joints.clear(); ros.right_joints.clear()
```

---

## 5. 部署入口 `.sh`

复制 `task2_policy_deploy_template.sh`，改开头三个变量：

```bash
POLICY_NAME="my_new_model"
POLICY_RUNNER="/abs/path/to/run_checkpoint_xxx.sh"
POLICY_RUNNER_ARGS=("$@")
```

模板已经处理好：协调器在线检查、fault 自动清除、等待 `set_paused` 注册、操作确认、
显式解除暂停、退出时清理子进程。**不要自己重写这些。**

对应的 runner 脚本（`POLICY_RUNNER` 指向的那个）要设置：

```bash
export PUPPET_ARM_LEFT_CMD_TOPIC="/task2/policy/joint_left"
export PUPPET_ARM_RIGHT_CMD_TOPIC="/task2/policy/joint_right"
export USE_INIT_POSE=false
```

如果 runtime 的启动器写死了客户端脚本路径，加一个环境变量覆盖点。
参考 `.../pi05/common/inference_pi05_rtc.sh` 里的 `PI05_RTC_CLIENT_SCRIPT`。

---

## 6. 自检，不需要人工确认

```bash
/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/task2_policy_contract_check.sh
```

逐条检查契约。默认只做**不动手臂**的检查（话题/服务是否存在、发布者身份、暂停时是否静默）。
加 `--live` 才会真的解除暂停验证转发 —— **那一步手臂会动**。

它会告诉你 `set_paused` 是策略自己提供的还是通用闸门提供的，两者后果不同（见下节）。

跑之前先起协调器：

```bash
roslaunch /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_button_teach_task2.launch \
  front_mode:=1 front_auto_enable:=true mid_auto_enable:=true rear_auto_enable:=true
```

---

## 7. 改不了推理客户端怎么办

比如 runtime 是别人的、不方便动。那就退到**通用闸门**，零改动：

```bash
/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/task2_wrap_policy.sh \
  <你产出的 interface.sh> [参数...]
```

闸门代替策略提供 `set_paused`，把策略的指令话题接到自己的输入：

```
策略 --/task2/policy_raw/joint_*--> 闸门 --/task2/policy/joint_*--> 协调器
```

**但它做不到第 4 节的 b) 和 c)。** 它在策略进程外面，清不掉策略内部的动作块，
也重置不了策略的限幅器。分块策略在交还后会接着执行接管前算好的动作。

闸门能做的只是把由此产生的**跳变**限速：恢复后从手臂实测位置按 `ramp_step_rad`
（默认 0.01 rad/次）向策略指令逼近，追上后转直通。

**动作是旧的，只是到得平滑。演示够用，正经跑请走深度集成。**

---

## 8. 操作流程（做完之后是这样用的）

```
空闲      后臂失能（软的），可徒手搬到顺手位置；搬动不影响前臂
          前臂跑推理

按下 X 侧示教按钮
   ├─ 推理暂停、动作块丢弃
   ├─ X 前臂跟随 X 后臂（离合式增量映射）
   └─ 另一侧前臂定住不动

拖动 X 后臂，X 前臂跟随

再按一下
   ├─ X 后臂立刻失能变软
   ├─ X 前臂停在最后位置
   └─ 没有按钮还按着 → 推理以全新观测自动继续
```

没有键盘、没有模式切换、没有顺序要求。两侧独立，可以只接管一侧。

---

## 9. 一条背景约束（解释为什么契约长这样）

固件 S-V1.7-3 上，机械臂每次进入 `ctrl_mode=0x01`（CAN 控制）后**固定 0.5 秒**会抛
`arm_status=0x05` + `err_code=0x003F`，六个关节通信同时中断约 100 毫秒，伺服失力、
手臂下坠、然后自愈。与指令频率、待机时长、使能状态全都无关，改不掉。

所以整套设计的核心不变式是：**后臂永远不进 CAN 控制**。它们只被操作员的手移动，
软件从不驱动它们。前后臂的映射因此必须是**离合式增量**（按下按钮那一刻记住偏移），
而不是绝对对齐。

这也是为什么契约要求你"锚定实测关节位置"而不是"回到某个已知位姿"——
在这套设计里，手臂在哪，只有传感器知道。

细节见 `2026-08-06-task2-physical-teach-handover-runbook.md` §10。

---

## 10. 交付清单

做完之后应该有：

- [ ] 一个暂停感知的推理客户端（满足第 3 节 6 条 + 第 4 节三件事）
- [ ] 一个 runner 脚本（设置了第 5 节那三个环境变量）
- [ ] 一个部署入口 `.sh`（从模板复制）
- [ ] `task2_policy_contract_check.sh` 静态检查通过
- [ ] `task2_policy_contract_check.sh --live` 通过（手臂会动，确认现场安全再跑）
- [ ] 真机跑一轮：按按钮接管 → 拖动 → 再按交还 → 推理继续，**重点看交还瞬间前臂有没有往回跳**

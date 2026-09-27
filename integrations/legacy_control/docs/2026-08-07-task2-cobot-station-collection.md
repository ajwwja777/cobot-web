# 用 cobot-station 网页采集（示教按钮版）

在网页里点 Start/Stop 采集数据，机械臂用示教按钮驱动。

**录制的开始和结束由前端决定，示教按钮不决定。** 按钮只管后臂有没有力、前臂跟不跟随。

---

## 1. cobot-station 不需要改

它的录制器只订阅四个关节话题：

```python
JOINT_TOPICS = {
    "master_left":  "/master/joint_left",     # action
    "master_right": "/master/joint_right",
    "puppet_left":  "/puppet/joint_left",     # state
    "puppet_right": "/puppet/joint_right",
}
```

`/master/joint_*` 正是按钮协调器发布的（接管期间是离合映射后的人的动作），
`/puppet/joint_*` 是前臂反馈。`configs/station.yaml` 里 `required_topics` 要的
三路相机和四个臂话题，这套全都有。

所以后端、前端、配置**一行都不用改**，只需要换一个启动机械臂的脚本。

输出还是原来的格式，14 维（左 7 + 右 7）：

```
observations/qpos, qvel, effort            ← /puppet/joint_{left,right}
observations/images/{cam_high,cam_left_wrist,cam_right_wrist}
action                                     ← /master/joint_{left,right}
base_action                                ← 全 0（保持兼容）
```

---

## 2. 启动顺序（5 个终端）

**终端 1 — 机械臂**

```bash
/home/agilex/cobot_magic/cobot-station/scripts/start_arm_ros_task2.sh
```

它做三件事：`can_config_cobot.sh task2` → 起
`start_ms_piper_5arm_teleop_collect.launch` → 把操作顺序打在屏幕上。
需要 sudo 密码时在这个终端输入。

> 用的不是 cobot-station 自带的 `start_arm_ros.sh`。那个用
> `start_ms_piper.launch mode:=0`，依赖主臂和从臂挂在同一条 CAN 总线上、
> 由固件做联动。现在后臂在自己的总线上，走的是示教按钮 + 协调器。

**终端 2 — 相机**

```bash
/home/agilex/cobot_magic/cobot-station/scripts/start_cameras_ros.sh
```

或者等价的（话题名一样，都是 `camera_f/l/r`）：

```bash
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
roslaunch /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/multi_camera_shuai.launch
```

**终端 3 — 相机臂定位**（一直挂着，不要关）

```bash
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
cd /home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop
python teleop/fix_mid_camera_pose.py
```

**终端 4 — 后端**

```bash
cd /home/agilex/cobot_magic/cobot-station
./scripts/start_backend.sh
```

**终端 5 — 前端**

```bash
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
cd /home/agilex/cobot_magic/cobot-station
./scripts/start_frontend.sh
```

然后浏览器打开 Dashboard。

---

## 3. 采集一条 episode

```
0. task2_home_cli.py all   四条臂到统一起点，后臂停在起点举着等
1. 按下【两条】后臂的示教按钮 → 后臂上电、重力补偿，前臂开始跟随
                              （此时还没有录任何东西）
2. 前端点 Start            → 开始录制
3. 做任务
4. 前端点 Stop             → 结束录制，保存 episode
5. 两个按钮各再按一下       → 后臂失能变软
6. 把后臂放回重力静止位
7. task2_home_cli.py all   → 回到起点，进入下一条
```

第 0 步和第 7 步是同一件事：让每条 episode 都从同一个起点开始，人也不用
从很低的姿势把手臂拉起来。归位命令见
[`task2_homing/`](../task2_homing/)，第一次用要先 `capture` 定起点。

### 两个按钮都要按

只按一侧不会报错，网页上两条臂的提示也都会消失——因为协调器会给**未接管
的那一侧发保持指令**让它定住。但那一侧录进文件的 action 是一条**完全恒定
的直线**。

实测一条只按左侧的 19.5 秒 episode：

```
左臂 action 幅度   60.10 deg     ← 真实的人手动作
右臂 action 幅度    0.00 deg     ← 584 帧一模一样
```

这不是"右臂没动"，是把一段不存在的演示写进了训练数据，模型会学到"右臂在
这个任务里永远保持这个姿势"。**哪怕某条 episode 右臂确实不需要动，也让它
跟着人手自然停着**——真人的手会微动，那是有信息的；数学上完美的直线没有。

---

## 4. 顺序是必须的，不是习惯

**第 1 步必须在第 2 步之前。**

协调器只在**有一侧接管时**才发布 `/master/joint_*`。空闲时那两个话题一条消息都没有。
而录制器（`collector.py::_capture_frame`）遇到缺少新鲜关节消息的帧会**直接跳过**。

所以没按按钮就点 Start，会录出一个空文件或者极短的文件。前端的就绪指示会
显示臂话题没有数据——那正是在提醒你还没按按钮。

**第 4 步必须在第 5 步之前。** 先松按钮的话，最后那几秒没有 action，那些帧会被丢掉。

**`allow_joint_fallback` 必须保持关闭。** 打开之后，缺失的关节会被填成 0
并照样写进文件。那是在污染训练数据，而且事后很难发现。

---

## 4.5 存储要先算一笔账

图像是**原始 uint8** 存进 HDF5 的，没有压缩。实测：

```
1.6 GB / 19.5 秒 = 82 MB/s ≈ 每分钟 4.9 GB
```

一条 2 分钟的 episode 约 **10 GB**，采 50 条就是 **500 GB**。

**2026-08-07 实测磁盘只剩 137 GB（已用 93%）**，按这个码率只够录约 **27 分钟**。
开采之前一定先看：

```bash
df -h /home/agilex/cobot_magic/cobot-station/data
```

空间不够的话有两个方向：HDF5 开 gzip 压缩（改 `collector.py` 的
`create_dataset`，读取端不用动），或者图像改存 JPEG 字节（省得多，但
`convert_cobot_hdf5_to_lerobot.py` 要跟着改）。

---

## 5. 检查

开录之前，Dashboard 上应该看到：

| 项 | 期望 |
|---|---|
| 三路相机 | 都在线，约 30 Hz |
| `/puppet/joint_left`、`/puppet/joint_right` | 在线，约 200 Hz |
| `/master/joint_left`、`/master/joint_right` | **按下示教按钮之后**才有数据 |

命令行核对：

```bash
source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash
rostopic hz /puppet/joint_left /puppet/joint_right
rostopic hz /master/joint_left            # 按住示教按钮时才有
rostopic echo -n 1 /task2/teach_handover/mode      # policy / manual:left / manual:left+right
rostopic echo -n 1 /task2/teach_handover/fault     # 应该是空
```

录完之后，episode 在 `configs/station.yaml` 的 `default_dataset_dir`：
`/home/agilex/cobot_magic/cobot-station/data/episodes`

---

## 6. 出问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 点 Start 后帧数不涨 | 没按示教按钮，`/master/joint_*` 无数据 | 先按按钮再点 Start |
| 按按钮前臂不跟随 | 协调器 fault | `rosservice call /task2/teach_handover/reset_fault` |
| 后臂搬不动 | 空闲态被设成了抱闸 | 确认 launch 的 `rear_idle_disabled:=true` |
| 后臂按了按钮也没反应 | 该臂驱动 fault | `rosservice call /task2/teach/rear_left/reset_fault` |
| CAN 配置报"缺少接口" | 适配器没插好 | `can_config_cobot.sh probe` 看实际接线 |

协调器状态（`/task2/teach_handover/mode`）：

- `policy` —— 空闲，没有接管（这里没有策略在跑，这个名字只是沿用）
- `manual:left` / `manual:right` / `manual:left+right` —— 接管中
- `fault` —— 出错了，`fault` 话题里有原因

---

## 7. 和其他 profile 的关系

| | cobot-station 采集 | 接管推理 | 旧 3 臂部署 |
|---|---|---|---|
| CAN | `can_config_cobot.sh task2` | 同左 | `legacy3` |
| launch | `start_ms_piper_5arm_teleop_collect.launch` | `start_ms_piper_5arm_button_teach_task2.launch` | `start_ms_piper_3arm.launch` |
| 后臂空闲 | 失能（可徒手搬） | 失能 | 不参与 |
| 协调器 | 有 | 有 | 无 |
| 谁决定录制 | **前端 Start/Stop** | 不录 | 不录 |
| 策略 | 无 | π0.5 | π0.5 |

两个 5 臂 launch 的差别只有 `rear_idle_disabled` 这一个参数可调，
采集和推理的机械行为是同一套。

其余见 `2026-08-07-task2-deployment-profiles.md`。

---

## 8. 验证状态

**2026-08-07 已在真机跑通一条完整 episode。** 实测结果：

```
584 帧 / 19.5 秒 @ 30 Hz     三路相机全部有画面
NaN                          无
全零帧                        0
左臂 action 幅度              60.10 deg（真实人手动作）
左臂 action 与 state 跟随误差  最大 0.186 rad（跟随滞后，正常）
```

同一条 episode 也实测出了"只按一侧按钮"的后果，见第 3 节。

**仍未验证**：连续多条 episode 的稳定性、长时间录制时帧率是否保持 30 Hz、
以及 `convert_cobot_hdf5_to_lerobot.py` 对这批数据的转换。

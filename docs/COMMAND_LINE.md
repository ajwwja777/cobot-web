# Cobot 命令行操作手册

网页和终端操作归 `cobot-web` 维护；硬件实现仍交 `cobot-control`，数据格式交 `cobot-dagger`，模型／RL 算法交所属平台。不需要另开 ops 项目对话。

本文核对当前仓库入口，日期 2026-09-28。所有现场命令在 **Cobot** 的 Bash 终端执行：

```bash
ssh agilex@10.7.165.64
cd /home/agilex/jiaan/project/cobot-web
python3 scripts/console.py --help
```

工具只需系统 Python 3，不用激活网页虚拟环境。代码／Git／维护在 A6000：`/data/LFT-W02_data/jiaan/jiaan/projects/cobot-web`；Cobot 是运行副本。当前 runtime 由 `configs/local.json` 指定，使用 `/home/agilex/jiaan/project/cobot-web/runtime`，由网页项目直接管理。

## 与旧目录相比

| 内容 | 现在 |
|---|---|
| 终端工作目录 | /home/agilex/jiaan/project/cobot-web |
| 网页启停／状态 | ./scripts/ui_up.sh、./scripts/ui_down.sh、./scripts/ui_status.sh；脚本名称沿用 |
| 统一操作 | python3 scripts/console.py；复用网页后端的状态机，不再手写大部分 curl 请求 |
| 网页日志／PID／任务回执 | 本项目 runtime/，已是实体目录；旧 cobot-ops 已删除 |
| Python 环境与 uv | 本项目 .venv/；tools/uv，依赖在 pyproject.toml 与 uv.lock |
| RLT／模型位置 | /media/agilex/Getea1/jiaan/model/rl-platform/rlt/plug_insertion 放权重；/media/agilex/Getea1/jiaan/data/datasets/plug_insertion/derived/rl-platform/rlt 放 Replay；项目 outputs 只放运行日志 |

ui_down／ui_up 只重启网页，不会自动清理机械臂、相机、模型或 ROS 子进程。前端异常但后端可用时可以用 console.py；8015 不可用时先用 recovery status／snapshot 排查。采集和共享模型 CLI 仍需后端正常，独立硬件脚本和 recovery 的适用范围见下文。

## 两层入口及可用范围

| 情况 | 使用方式 |
|---|---|
| 网页正常，想在终端操作 | `console.py` 调用与网页相同的 API、参数校验和状态机 |
| 浏览器／前端坏了，8015 API 正常 | 同一套 CLI 可以继续模型、采集、评测和设备操作，无需浏览器 |
| 8015 后端也无响应 | `console.py recovery ...`、本地硬件脚本、Linux／ROS 工具仍可排查；先停止机器人，再恢复后端 |
| 8015 录制后端停止 | 普通采集与当前 RLT 录制不能继续；不能另外启动重复录制器来“绕过”占用 |
| 只关了网页标签 | 模型、采集、ROS 任务仍可能继续运行 |

CLI 没有另写一套控制状态机。`device` 走同一套 confirm/action 协议；模型与采集复用共享模型管理；每次暂停／保存会重新读取 episode 身份和 generation；失败不会自动重试。命令执行可能导致运动／删除的含义与网页按钮一致，输入命令就是主动操作。

日常命令通常返回 JSON。`operation` 未空或 `phase=loading` 表示已受理仍在执行，不等于加载成功；设备 `running` 不等于反馈就绪，应查 `state devices`。命令超时不取消后台任务。

## 网页按钮、终端命令与实际实现

本节按 2026-09-28 已部署源码核对。所有 CLI 在 Cobot 的 `/home/agilex/jiaan/project/cobot-web` 中执行；下表“CLI 参数”前均加 `python3 scripts/console.py `。模型、臂、位姿和数据目录须替换为当次实际选择；表中的命令是逐项对照，不是应连续执行的脚本。

`W` 表示 `/home/agilex/jiaan/project/cobot-web`，`C` 表示 `/home/agilex/jiaan/project/cobot-control`，`R` 表示 `/home/agilex/jiaan/project/rl-platform`，`V` 表示 `/home/agilex/jiaan/project/vla-platform`。这些缩写只为说明路径，不是要求设置的 shell 变量。

### 终端直接执行的例子

硬件命令先进入 control；例如配置 CAN：

```bash
cd /home/agilex/jiaan/project/cobot-control
./scripts/can_up.sh
```

该脚本真正执行的是 `integrations/legacy_control/can_config_cobot.sh task2`，由 sudo 正常提示密码。`can_web.sh` 的 stdin 密码协议只供网页后端使用，不作为日常终端范例。两者有一个区别：网页配置失败会自动重置并重试一次，`can_up.sh` 执行一次配置／链路检查；重置的纯终端命令见第 1 节。

机械臂与相机分别在两个终端前台运行：

```bash
cd /home/agilex/jiaan/project/cobot-control
./scripts/arms_up.sh
```

```bash
cd /home/agilex/jiaan/project/cobot-control
./scripts/cameras_up.sh
```

机械臂脚本加载已登记环境后执行 `roslaunch robot/arms/arms.launch`；相机脚本执行 `roslaunch integrations/legacy_control/launch/multi_camera_shuai.launch`，具体节点见下表后的说明。已有对应节点时先查状态，不重复 launch。

如果需要和网页完全相同的任务管理、前置检查和停止入口，则回到 web 使用下一节的 CLI。例如：

```bash
cd /home/agilex/jiaan/project/cobot-web
python3 scripts/console.py device can configure
```

### 设备按钮

| 网页操作 | CLI 参数 | 实际执行链与实现 |
|---|---|---|
| 配置 CAN | `device can configure` | `W/scripts/can_web.sh configure` → `C/scripts/can_web.sh` → `C/integrations/legacy_control/can_config_cobot.sh`；配置 USB/CAN 映射、速率并检查链路 |
| 重置 CAN | `device can reset` | 同上转入 control 的 reset 分支；按现有五臂接口配置 1 Mbps、restart-ms 100 |
| 启动 ROS | `device roscore start` | `W/scripts/roscore_up.sh` → `C/scripts/roscore_up.sh` → `/opt/ros/noetic/bin/roscore -p 11311`；脚本将 ROS Master 放入后台 |
| 启动机械臂 | `device arms start` | `W/scripts/arms_up.sh` → `C/scripts/arms_up.sh` → `roslaunch C/robot/arms/arms.launch` |
| 启动相机 | `device cameras start` | `W/scripts/cameras_up.sh` → `C/scripts/cameras_up.sh` → `C/integrations/legacy_control/launch/multi_camera_shuai.launch` |
| 停止机械臂 | `device arms stop` | `C/src/cobot_control/device_control.py` 核对登记进程身份、向登记进程组发送 SIGINT 并等待退出；网页模块保留转发入口 |
| 停止相机 | `device cameras stop` | 同上，对相机启动进程组执行停止 |
| 停止 ROS | `device roscore stop` | 同上；先检查机械臂、相机依赖，仍运行时拒绝停止 ROS |
| 选臂归位 | `device home run --target selection --arms mid,front-right --pose plug2` | `W/scripts/home.sh selected --targets mid,front-right --pose plug2 --yes` → `C/scripts/home.sh` → `C/robot/home.py` |
| 停止归位 | `device home stop` | 当前网页设备任务管理器停止登记的 home 进程组；停止请求后仍须查看现场反馈 |
| 保存位姿 | `device pose capture --target selection --arms front-right --pose my_pose` | `C/robot/home.py capture` 读取实测位姿，写入正式位姿文件 |
| 删除位姿 | `device pose delete --target all --pose my_pose` | `C/robot/home.py delete` 删除该命名位姿记录 |
| 恢复对应臂 | `device recover run --target front-right` | `W/scripts/recover.sh` → `C/scripts/recover.sh` → `C/robot/recover.py`，按目标执行现有恢复检查与操作 |

五臂 launch 的真正节点是 `C/robot/arms/code/piper_start_ms_node.py`（前臂、中臂）、`piper_rear_teach_task2_node.py`（后臂）和 `teach_handover.py`（控制权交接）。驱动使用登记的 aloha 环境中的 Piper SDK；ROS 消息仍来自已安装的 Piper 工作区。

三相机 launch 进一步 include `$(find astra_camera)/launch/dabai.launch`。当前现场解析到 `/home/agilex/cobot_magic/camera_ws/src/ros_astra_camera/launch/dabai.launch`，运行编译后的 `camera_ws/devel/lib/astra_camera/astra_camera_node`。因此顶层入口在 control，相机公共驱动仍在登记的现场工作区。

归位由 home.py 检查选择、位姿和控制状态，再调用已有协调器／后臂节点服务或相应中臂实现；不会另起一套前后臂控制权。正式位姿在 `/media/agilex/Getea1/jiaan/data/motion/poses/home_poses.yaml`。硬件诊断和设备任务规则已归 control/src/cobot_control；网页接口保留兼容转发。

### 模型、采集和评测按钮

| 网页操作 | CLI 参数 | 实际实现 |
|---|---|---|
| 加载模型 | `model load --id plug-v3-warmup-5k` | 共享 `collection_model.py`／`deployment.py` 管理器 → `W/scripts/deployment_run.sh`；具体模型选择下述 RLT 或 π0.5 链路 |
| 等待加载成功 | `model wait --seconds 600` | 轮询已提交加载任务；不会另开一份模型 |
| 释放模型 | `model unload` | 共享管理器暂停、结束相应 Session、停止登记的模型进程；不是只杀输出栏中的某一个子 PID |
| 开始／结束 Session | `model session-start` ／ `model session-stop` | 共享管理器处理生命周期；准备 Session 不自动开始机器人推理 |
| 选择采集目录 | `storage normal /media/agilex/Getea1/jiaan/data/datasets/plug_insertion/recordings/demonstrations/manual` | 普通采集目录检查；RLT 使用 `storage rlt 路径`，评测使用 `storage evaluation 路径` |
| 开始无模型采集 | `capture start --data-root /media/agilex/Getea1/jiaan/data/datasets/plug_insertion/recordings/demonstrations/manual` | `segmented_capture/api.py` → `capture_service.py` → `capture_core` 中的录制与 HDF5 写入 |
| 加载模型后开始一轮 | `capture start --model plug-v3-warmup-5k` | CLI 根据模型类型进入普通模型辅助采集或 RLT Session；RLT 根据当前身份选择 start／next |
| 暂停并打节点／继续并打节点 | `capture pause` ／ `capture resume` | 操作当前录制器和模型暂停状态；RLT 请求携带刚读取的 episode/generation |
| 只打节点 | `capture marker` | 调用当前模式的节点接口，不重新加载模型 |
| 结束保存，不标成功失败 | `--timeout 120 capture save` | 普通采集终结录制；RLT 使用 operator_save，不当作成功／失败提交 |
| 成功／失败 | `--timeout 120 capture success` ／ `--timeout 120 capture failure` | 按当前采集模式保存相应 outcome；CLI 的明确操作不读取浏览器勾选框 |
| 结束并放弃 | `capture discard` | 使用当前模式的放弃接口，按既有规则删除本轮，不新增放弃记录 |
| 开始部署评测 | `evaluate start` | `deployment.py` 建立评测轮次并启动已加载策略；与训练采集用途分开 |
| 暂停／继续评测 | `evaluate pause` ／ `evaluate resume` | 调用已加载策略的暂停／继续接口，不再启动模型服务器 |
| 评测成功／失败／放弃 | `evaluate success` ／ `evaluate failure` ／ `evaluate abort` | 从当前 active 取得 trial_id，再由部署管理器校验并终结轮次 |

普通及 π0.5 采集的状态机在 `W/app/backend/segmented_capture/`，录制底层在 `capture_core/`；RLT 操作由 web 的 Session 适配转给已启动的 RL 服务。并非每次按按钮都执行一条新的 Linux 进程命令，暂停、节点和保存主要是现有服务的 API 调用。

RLT 加载实际链：`deployment_run.sh → W/scripts/rlt_v3_up.sh → R/scripts/rlt_up.sh → methods.openpi_rlt.scripts.online_role`，并按配置使用 Stage 1 模型服务。π0.5 加载实际链：`deployment_run.sh → W/scripts/deployment_pi05.sh → V/integrations/cobot/pi05/{baseline,dagger}/common/inference_pi05_rtc.sh`，启动相应策略服务和客户端。外部 Python/ROS 环境依赖以主机配置及各项目记录为准；此处不是绕过共享管理器重新启动模型的操作建议。

网页“保存／成功／失败／放弃并复位”还包含后续 home 操作。CLI 不读取网页本地的自动复位勾选和选臂结果：先确认终结完成，再执行选定臂／位姿的 `device home run ...`；不要把两步不加状态检查地串成一条命令。详见第 4 节。

### 是不是每个任务开一个终端

网页启动机械臂、相机和 home 等设备任务时，后端用 `subprocess.Popen(..., start_new_session=True)` 创建独立后台会话，stdout 和 stderr 写入该任务日志。没有逐个打开可见终端，也没有为每个任务分配可交互的 PTY；输出栏是任务日志查看器，不能在其中输入任意 shell 命令。ROS launch、模型服务器还可以创建多个子进程。

手动执行时可以这样区分：

| 使用方式 | 是否需要多个终端 | Ctrl+C 的含义 |
|---|---|---|
| `console.py device ...`、模型／采集 CLI | 通常一个终端即可，API 返回后后台任务继续 | 不能据 CLI 被打断断定后台任务已取消 |
| 直接前台执行 `C/scripts/arms_up.sh`、`C/scripts/cameras_up.sh` | 建议机械臂一个终端、相机另一个终端；ROS 启动脚本本身会后台化 | 在原启动终端请求停止对应前台 launch，随后核对残留节点 |
| `tail -F 实际日志路径` | 可以为每个日志单开终端，也可只看当前关心的任务 | 只停止看日志，不停止机械臂／相机／模型 |
| 网页“终止任务”或正常 stop／unload | 不需要可见终端 | 后端核对当前任务身份再停止；模型行走模型卸载，不能等同于对任意 PID 发 kill |

硬件任务的 launch PID、具体驱动节点 PID、GPU 策略服务 PID 可以不同。不要拿一个 PID 去推断整个任务只有一个进程，停止后仍应使用 `recovery status` 核实。

### 输出栏已经展示什么

选择任务后打开“命令”：

- “本次执行”默认显示可直接复制的终端命令，例如先 `cd .../cobot-control`，再 `./scripts/can_up.sh`。下方以 `# 实际实现：` 注释说明真实脚本／launch／Python 链路；复制整段时说明不会当作命令执行。
- “实际启动与进程”保留原始启动入口、工作目录和 `/proc/<pid>/cmdline`，供核对网页内部调用、exec 后的 roslaunch／Python 进程。此视图是排查信息，不是应整段执行的启动流程。
- 任务栏显示 PID、状态，命令区保留日志路径；“常用命令”按机械臂、相机、CAN、归位、恢复、部署、数采等分类，项目命令采用 `cd` 加相对脚本的形式。
- 尚无对应终端配方的任务仍显示真实登记信息，不伪造另一个入口；PID 身份有效且满足进程组条件时才提供停止命令。

说明来自已核对的实现，不自动展开完整 ROS include 或子进程树。没有独立进程的 API 操作不会凭空出现一个新 PID。输出内容可能采用“关键输出”筛选，可切换“原始日志”查看完整任务日志。

终端可读取相同信息：

```bash
cd /home/agilex/jiaan/project/cobot-web
python3 scripts/console.py state outputs
python3 scripts/console.py recovery status
```

来源：2026-09-28 用户要求补充按钮／终端／真正实现对照，并将网页中的内部密码管道改为实际终端命令。本批修改命令展示与手册，不执行上述硬件控制命令。

## 更换本机启动路径和模型

设置中的“启动路径与模型”，以及采集／部署中的“选择路径 / 登记模型”打开同一个配置面板。浏览的是运行网页的服务器文件，不是笔记本文件；选择和保存只更新配置，不会启动机械臂、加载模型或开始推理。默认记住选择，仍由人点击“启动／加载”。

### 机械臂和相机

1. 先结束采集、释放模型并停止设备任务，再选择“机械臂 / 相机”。
2. 选择自己的前台 `.sh` 或 ROS 1 `.launch`。launch 文件还要选择提供 ROS 包路径的 `setup.bash`；脚本也可自行激活 Python／ROS 环境。工作目录留空时使用启动文件所在目录，启动参数每行一个。
3. 保存后，设备面板的原启动按钮使用该路径；“恢复内置入口”可回到当前 control 脚本。
4. 输出栏继续显示启动命令、PID、日志和终止入口，常用命令也跟随配置。自定义脚本保持前台运行，不使用 `nohup`、`&` 或自行脱离会话，否则无法保证网页跟踪到完整进程生命周期。

自定义入口由 web 的 `scripts/site_device.py` 保持稳定的任务 PID，子任务在同一会话运行；路径和参数通过 argv 传递，不拼进任意 shell 命令。保存配置时拒绝正在运行的设备任务，避免更换后无法正确停止旧任务。

**更换 launch 路径不等于适配另一种机器人。** 设备健康、示教、归位、相机订阅仍使用当前 Cobot 话题／服务契约；节点名、话题、动作顺序不同，需要 cobot-control 适配。前台脚本由现场使用者提供其正确环境。

### 已有和自选权重

- 内置 RLT Reference、5k、在线／冻结和两个 π0.5 模型继续可用。已补充同一 plug_v3 配置的历史 20k actor，作为冻结对比项；它不是新的推荐默认模型。
- 权重清单自动扫描本机配置的 `model_root`，后台约每 30 秒刷新。Getea1 上的 FluxVLA、Galaxea、DM0.5、LingBot、Xiaomi 和其他历史权重也可看见；“需适配”表示还没有对应的网页控制入口，不代表权重文件损坏。DM0.5 的 Getea1 目录仅有清单，界面另标“仅元数据，本机缺少权重”；实际历史权重记录在 A6000 的 /data/LFT-W02_data/jiaan/jiaan/projects/vla-platform/models/history/dm0-5/step_4000。
- “检查并登记”用于复用**同任务、同相机／动作布局**的已有适配器。选择模板和权重，确认其任务、归一化和控制约定一致后保存；可同时设为默认选择。配置面板不会试运行权重。
- 当前可复用三个模板：plug_insertion 的冻结 RLT actor，以及 in_the_pot 的 π0.5 baseline RTC／DAgger RTC。RLT 选择 `actor_snapshot.pkl`，其上级运行目录需保留 `action_norm_stats.json`；不能把 learner 的 `latest.pkl` 当成 actor。π0.5 选择完整 checkpoint 目录，保留 `params`、checkpoint 元数据和模板对应 asset 的 norm_stats。
- 检查通过只表示文件与已登记入口满足基本要求，不是推理或成功率验收。新场景、不同架构、FluxVLA 的 safetensors、分布式训练 checkpoint 等，不能仅换路径冒充上述模板。

新模型适配归 vla-platform／rl-platform：提供推理加载／预处理、相机与状态输入、动作映射、暂停／继续、HIL、状态查询和退出接口，再接入 web 的共享模型生命周期。不要把另一个能运动的脚本直接当作“加载模型”入口；加载必须保持暂停，开始 Episode 才推理。历史模型已有命令行实现时优先复用，但还要核对网页的暂停与释放协议。

### 配置在哪里

| 内容 | Cobot 实际位置 |
|---|---|
| 本机启动路径、自选模型登记 | `/home/agilex/jiaan/project/cobot-web/runtime/data-console/site-options.json` |
| 全局默认模型、评测目录 | `/home/agilex/jiaan/project/cobot-web/runtime/deployment/settings.json` |
| 本机模型根与额外浏览根 | `/home/agilex/jiaan/project/cobot-web/configs/local.json` 的 `model_root`、`extension_roots` |
| 当前内置权重根 | `/media/agilex/Getea1/jiaan/model` |

浏览默认允许本机用户目录、项目根、模型根和登记的 ROS 工作区；其他共享目录可加入 `extension_roots`。另一台机器使用自己的绝对路径和环境，不需修改前端源码。机器配置保存在本机系统盘；代码和配置示例仍由 A6000／Git 管理，权重不复制进 web 仓库。修改 model_root／extension_roots 后，在任务结束时重启网页生效。

来源：2026-09-28 用户要求可选择路径、补充磁盘模型清单，并确认“记住选择，手动启动／加载”。本批不升级 ROS 驱动，不执行真实模型推理或机器人动作。

## 1. 开机至可以采集的流程

先检查机械臂工作范围、电源及连接。下面的顺序是完整流程，**不要把整段不加判断地批量执行**；已有服务使用状态检查，不重复启动。

### 先查实际进程与磁盘

```bash
python3 scripts/console.py recovery status
df -h /
lsblk -o NAME,TRAN,SIZE,FSTYPE,MOUNTPOINT
```

日志中出现 `Input/output error` 时先按 [故障手册](WEB_RECOVERY.md) 排查存储，不要继续加载模型或写数据。

### 启动网页／采集后端

```bash
./scripts/ui_up.sh
timeout 10s ./scripts/ui_status.sh
python3 scripts/console.py state console
```

这一步只启动 8015 后端，不会自动让机械臂运动。浏览器可不开。

### 配置 CAN、启动 ROS、机械臂和相机

```bash
python3 scripts/console.py device can configure
python3 scripts/console.py device roscore start
python3 scripts/console.py state devices
python3 scripts/console.py device arms start
python3 scripts/console.py device cameras start
python3 scripts/console.py state devices
python3 scripts/console.py state cameras
python3 scripts/console.py state console
```

CAN 命令在终端隐藏输入 sudo 密码，不把密码放在命令行参数、历史或示例中。每个启动返回任务登记；看 `state devices` 的 CAN／ROS、各臂的 reason/remedy 与反馈，及三相机时间戳／状态，确认就绪再采集。

### 不经过 8015 的原始硬件入口

后端尚未启动／出故障时也可使用。同一设备只选一种启动入口，不同时启动两份。

终端一：

```bash
cd /home/agilex/jiaan/project/cobot-control
./scripts/can_up.sh
./scripts/roscore_up.sh
./scripts/arms_up.sh
```

终端二：

```bash
cd /home/agilex/jiaan/project/cobot-control
./scripts/cameras_up.sh
```

机械臂与相机 launch 在前台运行，可以实时看完整输出；原终端 Ctrl+C 请求停止该 launch。ROS 子节点会创建各自的进程组，Ctrl+C 后仍应核对残留。手动启动的未登记任务不要按截图中的旧 PID 终止。网页或后端恢复后，以实际状态避免重复 launch。

CAN 重置会中断通信，必须先停止推理／采集并确认现场安全。网页对应的重置参数为 1 Mbps、restart-ms 100；确需在纯终端重置时：

```bash
cd /home/agilex/jiaan/project/cobot-control
sudo -v
sudo modprobe gs_usb
for iface in can_left can_right can_mid can_rear_left can_rear_right; do
  sudo ip link set "$iface" down || break
  sudo ip link set "$iface" type can bitrate 1000000 restart-ms 100 || break
  sudo ip link set "$iface" up || break
done
./scripts/can_up.sh
```

缺接口时先查电源／USB-CAN，不用反复 reset 代替排查。

## 2. 选择路径与普通采集

以下模型、采集和评测 CLI 仍在 web 项目执行；如果刚使用了 control 的直接脚本，先切回：

```bash
cd /home/agilex/jiaan/project/cobot-web
```

路径可以先于模型选择。当前允许数据根由主机配置决定；新数据建议在 `/media/agilex/Getea1/jiaan/data` 下，历史根只有明确登记的路径可用。

```bash
python3 scripts/console.py storage normal /media/agilex/Getea1/jiaan/data/datasets/plug_insertion/recordings/demonstrations/manual
python3 scripts/console.py capture start --data-root /media/agilex/Getea1/jiaan/data/datasets/plug_insertion/recordings/demonstrations/manual
python3 scripts/console.py capture pause
python3 scripts/console.py capture resume
python3 scripts/console.py capture marker
python3 scripts/console.py capture save
```

操作含义：

| CLI | 网页含义 |
|---|---|
| `capture start` | 开始一轮；加载并选择模型时开始推理 |
| `capture pause` | 暂停并打节点 |
| `capture resume` | 继续并打节点 |
| `capture marker` | 只打节点 |
| `capture save` | 结束保存为 unknown，不标成功／失败 |
| `capture success` / `failure` | 保存并标成功／失败；CLI 明确执行，不依赖浏览器勾选状态 |
| `capture discard` | 结束并放弃，按现有实现删除本轮，不保留放弃记录 |

`storage normal` 检查／准备目录；普通采集每次 `start` 仍明确传 `--data-root`，不会从另一浏览器的 localStorage 偷读路径。可用 `--task`、`--dataset-model`、`--checkpoint`、`--round` 设置数据身份；默认 flat 布局，不改变既有录制格式／HIL mask。

检查状态和历史：

```bash
python3 scripts/console.py state capture
python3 scripts/console.py api GET '/api/segmented-teach/episodes?data_root=/media/agilex/Getea1/jiaan/data/datasets/plug_insertion/recordings/demonstrations/manual&limit=20'
```

命令示例之间应按需要选择；不要在一轮已保存后继续执行 discard。停止失败先看记录和 writer 状态，不重复开始。

## 3. 共用模型：π0.5、RLT、Session

先列实际模型，输出包含 `id`、`kind`、`checkpoint`、可用状态与相关配置路径：

```bash
python3 scripts/console.py model list
python3 scripts/console.py model load --id plug-v3-warmup-5k
python3 scripts/console.py model wait --seconds 600
python3 scripts/console.py model session-start
```

load 只提交加载；wait 等待就绪或报告错误／超时，超时不会杀进程。成功输出 ready／paused，Session 准备不会自动开始推理。模型 ID 用于稳定寻址，真实路径以 model list/status 为准，不将 ID 当作权重路径。

### π0.5 DAgger 采集例子

结束上一轮／Session、释放旧模型后：

```bash
python3 scripts/console.py model load --id pi05-in-the-pot-dagger
python3 scripts/console.py model wait --seconds 600
python3 scripts/console.py model session-start
python3 scripts/console.py capture start --model pi05-in-the-pot-dagger --data-root /media/agilex/Getea1/jiaan/data/datasets/in_the_pot/recordings/cobot-dagger/round_002
python3 scripts/console.py capture pause
python3 scripts/console.py capture resume
python3 scripts/console.py capture save
```

仍使用既有物理示教按钮／控制权切换／HIL mask；CLI 不伪造人工干预标签。无模型采集不传 `--model` 即可。

### RLT 采集例子

```bash
python3 scripts/console.py storage rlt /media/agilex/Getea1/jiaan/data/datasets/plug_insertion/recordings/rl-platform/rlt/online/three_camera_v3
python3 scripts/console.py model load --id plug-v3-warmup-5k
python3 scripts/console.py model wait --seconds 600
python3 scripts/console.py model session-start
python3 scripts/console.py capture start --model plug-v3-warmup-5k
python3 scripts/console.py capture pause
python3 scripts/console.py capture marker
python3 scripts/console.py capture resume
python3 scripts/console.py --timeout 120 capture success
```

最后一条也可选 `failure`、`save` 或 `discard`，一次只选一个。RLT 准备、下一轮、暂停、节点、保存均调用现有 Session 接口，携带刚读取的身份。处于 waiting_scene 时 start 自动调用 next；不会重复创建另一个 worker。

路径在每轮开始时固定，轮中设置的新路径用于下一轮。HTTP 超时或结果不明确时先查：

```bash
python3 scripts/console.py state session
python3 scripts/console.py state recorder
python3 scripts/console.py api GET /api/rlt/history
python3 scripts/console.py recovery logs model
```

RLT 的 save 不标成功／失败，也不按成功失败进入 replay 提交流程。CLI 终结请求明确关闭旧后端固定 home；选择臂／位姿的归位见下一节。

结束 Session／释放共享模型：

```bash
python3 scripts/console.py model session-stop
python3 scripts/console.py model unload
python3 scripts/console.py model status
```

后台卸载可能异步执行；检查 operation 为空且 offline，再确认进程和显存。不要连续提交 unload。

## 4. 归位、命名位姿和恢复

先读取可用目标和位姿，参数必须存在；模型不在推理、没有冲突操作时才允许 home，不要求为 idle／waiting_scene 的已加载模型强制卸载：

```bash
python3 scripts/console.py state devices
python3 scripts/console.py device home run --target selection --arms mid,front-right --pose plug2
python3 scripts/console.py state devices
```

保存／成功／失败／放弃完成后，需要复位就执行上述 home；CLI 不沿用浏览器的自动复位勾选，避免把另一个浏览器的选择带入终端。先核实终结已完成，不能在结果未明时自动归位。

其他操作示例，按需要单独选用：

```bash
python3 scripts/console.py device pose capture --target selection --arms front-right --pose my_pose
python3 scripts/console.py device recover run --target front-right
python3 scripts/console.py device home stop
```

删除位姿：`device pose delete --target all --pose my_pose`，会删除该命名位姿的记录。手动恢复是否会失能／使能及目标选项，以 `robot/recover.py` 和设备提示为准。

底层原始入口（在安全前提下，由脚本执行现有 preflight）：

```bash
./scripts/home.sh selected --targets mid,front-right --pose plug2 --yes
./scripts/home.sh capture --targets front-right --pose my_pose
./scripts/recover.sh front-right
```

底层脚本不是绕过安全检查的“强行运动”入口。help／只读诊断：

```bash
./scripts/home.sh --help
./scripts/recover.sh --help
source ./scripts/environment.sh
source "$TASK5_ROS_SETUP"
python3 scripts/verify_device_health.py --help
rosnode list
rostopic list
ip -details -statistics link show type can
```

## 5. 模型部署评测与成功率

部署和采集共用已加载模型，但每一轮用途独立；开始评测前先结束采集轮次／Session。

```bash
python3 scripts/console.py storage evaluation /media/agilex/Getea1/jiaan/data/evaluations
python3 scripts/console.py model load --id plug-v3-warmup-5k
python3 scripts/console.py model wait --seconds 600
python3 scripts/console.py evaluate start
python3 scripts/console.py evaluate pause
python3 scripts/console.py evaluate resume
python3 scripts/console.py --timeout 120 evaluate success
python3 scripts/console.py state model
python3 scripts/console.py state records
```

success 可换 failure；abort 放弃本次，不写放弃记录。CLI 从当前 active 读取 trial_id，仍由后台验证，避免对过期评测请求结束。评测记录与采集／训练 replay 不混用；完成后归位仍用第 4 节。在线更新模型不能当冻结评测模型使用。

## 6. RL 在线更新、训练与额外工具

当前网页“训练”主要查看模型、配置和诊断，不是任意训练脚本的通用启动器。CLI 对应：

```bash
python3 scripts/console.py state training
python3 scripts/console.py model list
python3 scripts/console.py api GET /api/rlt/releases
```

需要现有 RLT 在线更新时，在确认数据／已有轮次和配置后，加载 listing 中登记的 `plug_v3-online-latest`，准备 Session 并按第 3 节采集；它保留原 online_rl.yaml／learner 入口。冻结评测选 `plug_v3-frozen-latest` 或具体 warmup 候选。这是模式选择，不是重新运行离线 warmup。

底层链路：`deployment_run.sh → rlt_v3_up.sh → methods.openpi_rlt.scripts.online_role`，用当前登记配置启动；Stage 1 单独运行。π0.5 链路是 `deployment_run.sh → deployment_pi05.sh`。以 `state outputs` 中实际命令／cwd／配置路径为准。

**不要把 `scripts/warmup.sh` 当本次 plug_v3 忠实复现训练入口**：它仍封装旧 plug_v2.training_flow。离线训练与数据比例调整归 rl-platform／原 RLT 实验记录，需选择已验证配置，不在网页使用手册发明新的训练参数。

数据转换等高级功能已有原始 CLI，可独立于浏览器使用；先看参数与原项目规范：

```bash
PYTHONPATH=app/backend .venv/bin/python -m converters.convert_rollout_hdf5_to_lerobot --help
```

前后端接口索引见下一节，涵盖网页隐藏的审核、节点图、回放、删除等 API。不要把“能够调用”理解成可以跳过版本／占用检查。

## 7. 完整 API、相机、历史与输出

常用操作封装在 CLI 中；**全部注册 API** 可经 api 子命令访问，不受网页按钮是否可见限制：

```bash
python3 scripts/console.py routes
python3 scripts/console.py routes --recorder
mkdir -p outputs/diagnostics
python3 scripts/console.py api GET /openapi.json --output outputs/diagnostics/cobot-openapi.json
python3 scripts/console.py api GET /api/rlt-recorder/openapi.json --output outputs/diagnostics/cobot-recorder-openapi.json
python3 scripts/console.py state outputs
```

OpenAPI 包含请求 schema 和字段；/api/rlt-recorder 是单独挂载的子应用，必须看它自己的 schema。复杂 JSON 推荐文件：`api POST /实际接口 --file /实际请求.json`，避免 shell 引号错误。查询参数由调用者正确编码。所有 POST／PUT／DELETE 都不会自动重试。

| 领域 | 查询／底层 API |
|---|---|
| 配置与路径 | GET /api/console/config、/api/console/host |
| 设备、CAN、ROS、home、pose | GET /api/console/devices；POST /api/console/devices/confirm → /action |
| 相机状态／预览 | GET /api/console/cameras；/api/console/cameras/{camera_key}.mjpg |
| 共享模型／Session | GET/POST /api/collection/model |
| 普通／π0.5 采集 | /api/segmented-teach/start、pause、resume、marker、stop、discard |
| RLT 采集 | /api/rlt/session/*、/api/rlt/episode/*，每次带 episode_id/generation |
| 数据历史与回放 | /api/segmented-teach/episodes/*；/api/rlt-recorder/api/* |
| 部署评测 | /api/deployment/action、storage、records、frame |
| 输出与终止 | GET /api/console/outputs；POST /api/console/outputs/stop，必须用最新身份 |
| 训练诊断／release | GET /api/console/diagnostics、/api/rlt/releases |
| 故障录制恢复 | GET /api/rlt/recorder-diagnostics；POST /api/rlt/recover-recorder |

相机在不打开主页面时可直接查看，例如浏览器／支持 MJPEG 的播放器打开 `http://10.7.165.64:8015/api/console/cameras/camera_high.mjpg`。静态帧需要先读 cameras 返回的 generation；二进制 API 下载用 `--output 文件`，不要当 JSON 解析。

`recover-recorder` 只在 Session 已 stopped 且 policy_paused 时允许调用，可能保留不完整文件供核查；不清空数据来“消除报错”。JSON 例子：

```bash
python3 scripts/console.py api POST /api/rlt/recover-recorder --json '{}'
```

## 8. 日志、停止与重启

```bash
python3 scripts/console.py state outputs
python3 scripts/console.py recovery logs model
python3 scripts/console.py recovery logs arms
python3 scripts/console.py recovery status
python3 scripts/console.py recovery snapshot
```

看实时输出：对上面显示的真实 log_path 执行 `tail -F`；此终端 Ctrl+C 只结束 tail，不结束任务。

正常顺序：暂停 → 保存或放弃 → Session stop → unload → 按需要 stop cameras／arms／roscore → ui_down。不要把“重启网页”当作“停止一切”。

网页/API 失效时，独立恢复入口无需 8015 正常：

```bash
python3 scripts/console.py recovery pause
python3 scripts/console.py recovery interrupt model
```

第二条只预览。确认目标和机器人已停，再用 `recovery interrupt model --execute --robot-stopped`；旧入口启动的 RLT 则检查 rlt。Stage 1、ROS 独立子进程、TERM 升级、web 单 PID 停止的顺序及限制见 [故障手册](WEB_RECOVERY.md)，不要凭父 PID 消失就判断所有任务都停了。

正常重启网页：

```bash
./scripts/ui_down.sh
```

确认停止成功、8015 没有旧进程占用后再执行：

```bash
./scripts/ui_up.sh
```

若 down 拒绝，先排查占用／状态，不删 PID 文件强行重开。恢复后主动确认模型和数据目录，不自动恢复上一次动作。

## 9. 网页错误提示与现场故障入口

输出栏的“诊断”按钮展示本次页面捕获的接口错误：时间、请求方法、路径、HTTP 状态、原始 detail、结果是否待确认、对应排查建议和只读终端命令。不会自动重试请求或执行恢复。可手动刷新检查 CAN／ROS／设备、相机、采集、模型、录制器和工控机磁盘；HTTP 200 不等于设备就绪，展开看真实 phase、reason 与 remedy。

错误只在当前页面内保留，最多 20 种并合并重复次数；需要留证据使用 snapshot。网页整体打不开时直接用本手册的现场副本与终端入口，不依赖诊断按钮。

遇到不可确认的控制／存储问题，先结束演示并保持现场安全。故障文档说明哪些可以自助恢复，哪些属于接口修复或硬件故障；不会承诺通过重启修好所有问题。

## 维护验证范围

输出栏常用命令也已更新为上述共享 CLI，暂停会读取当前身份，释放会调用完整模型卸载。

命令按现有 API/schema 和原生脚本核对，测试验证参数、身份、失败不重试及恢复进程边界；2026-09-28已追加共享模型加载／释放、独立在线更新、媒体读取和硬件被动启动／停止验证。未进行真实采集、归位或动作，上电运动仍需现场验收。更改公共控制逻辑时仍需对应领域的现场验收。

### 历史模型的原 .sh 入口（2026-09-28）

以下目录均在 Cobot 的 /home/agilex/jiaan/project/vla-platform/integrations/cobot 下；A6000 主代码在 /data/LFT-W02_data/jiaan/jiaan/projects/vla-platform/integrations/cobot。先 cd 到表中目录，再执行对应命令。标“终端”的旧 live 入口会按原流程运动，不能当成仅加载命令；本次没有改变其初始位姿、RTC、动作映射或滤波参数。

| 模型/场景 | 相对目录 | 原终端启动命令 | 网页 |
|---|---|---|---|
| FluxVLA π0.5 / in_the_pot / 5000 | fluxvla_pi05 | ./interface_task2_teach_rtc_live.sh 5000 | 暂停加载入口已接入 |
| G0.5 / in_the_pot / 4000 | galaxea_g05/baseline | ./interface_task2_teach_rtc_live.sh 4000 | 暂停加载入口已接入 |
| G0.5 DAgger / 4000+4000 | galaxea_g05/dagger | ./interface_task2_teach_rtc_live.sh 4000 | 暂停加载入口已接入 |
| XR1 / in_the_pot / 4000 | xiaomi/xr1 | ./interface_task2_teach_rtc_live.sh 4000 | 暂停加载入口已接入 |
| XR0 / in_the_pot / 4000 | xiaomi/xr0 | ./interface_live.sh 4000 | 原终端入口 |
| LingBot V2 / in_the_pot / 2000、4000 | lingbot_v2 | ./interface_live.sh 4000 | 原终端入口 |
| π0.5 / lift_book / 2000 | pi05/lift_book | ./interface_live.sh 2000 | 原终端入口 |
| π0.5 / put_two_fruits / 2000、4999 | pi05/put_two_fruits | ./interface_live.sh 4999 | 原终端入口 |

网页的“加载”通过 deployment_run.sh → vla-platform/integrations/cobot/managed_model.py → 对应旧 run_checkpoint*.sh 加载服务和暂停客户端。点击“开始/继续”才调用原 /task2/policy/arm 与 /task2/policy/set_paused。一个受管理的进程组包含客户端和它启动的服务，输出栏仍可看 PID 并释放整个模型；不自动启动独立终端窗口。启动计划可用 managed_model.py <模型ID> --check 做只读文件/命令检查。

完整版本、checkpoint、依赖和具体缺失文件以 configs/cobot_models.json 与网页模型详情为准。DM0.5 和 XR1 DAgger 在本机缺权重；已有 Python/ROS 安装仍是现场依赖，不能仅复制 .sh 就认为新机器已具备环境。本批是入口迁入、控制接入和无动作验证，不代表重新完成真机成功率测试。

## 2026-09-29：统一CLI与网页下层调用

结构见ARCHITECTURE.md，安装见DEPLOYMENT.md。

```bash
cd /home/agilex/jiaan/project/cobot-control
/usr/bin/python3 scripts/control.py status
/usr/bin/python3 scripts/control.py diagnose --seconds 2
# 现场确认后，和网页相同管理规则：
/usr/bin/python3 scripts/control.py start cameras
/usr/bin/python3 scripts/control.py stop cameras
cd /home/agilex/jiaan/project/cobot-web
.venv/bin/python scripts/models.py list
.venv/bin/python scripts/models.py check plug-v3-warmup-5k
.venv/bin/python scripts/models.py status
.venv/bin/python scripts/models.py logs
```

硬件CLI与/api/console/devices使用同一个cobot_control.device_control。CAN configure/reset经can_web.sh→can_config_cobot.sh→modprobe/ip/ethtool/cansend，保留1Mbps/restart-ms100；probe现为只读，不发探测帧或复位。终端直接scripts/can_up.sh会正常提示sudo密码，不需要手写网页密码管道。

网页使用子进程、独立进程组、日志文件，不为任务新开可见终端。前台roslaunch用Ctrl-C；管理CLI通过PID+start_ticks停同一组。不要按htop的一行高亮或旧PID盲目kill，先status核对身份。网页关闭后control仍能管理硬件，models.py也无需HTTP服务在线。RLT录制则依赖登记的recorder HTTP，关闭网页前先结束录制Session。

## 2026-09-29：场景与模型联动选择

采集和部署共用三段选择：Scene → Model → Steps（场景、模型家族、具体 checkpoint）。场景标识及模型/步数选项使用英文；选择 All scenes 可跨场景查看，选中 checkpoint 后定位对应场景。两页同步当前选择并记住浏览器偏好，过滤或选中不等于加载，仍需手动点击“加载模型”。

第三框显示版本/步数及可用状态，RLT 分列 Stage1、Learner 与 Actor；完整权重路径位于选择框下方，可直接选取复制。不可用项在两页均置灰禁用，保留缺文件、仅终端或待适配等原因；没有可加载模型的场景显示明确提示。正在加载或执行活动轮次时锁定选择，不改当前任务。

分类来自现有登记的task字段；新增同类模型无需修改网页场景清单。CLI scripts/models.py 的模型ID、命令和底层运行协议保持不变。

### 采集与部署面板排列

默认四框为“保存位置 | 模型 / 数据（部署为评估记录）| 采集或部署控制”。进入左下角 **设置 → 编辑布局**，拖动任一框的标题栏到另一位置，再点 **完成布局**。两页分别记住排列，刷新保留；**恢复默认位置** 会恢复默认网页布局。窄内容区依次排成单列，仍可上下拖动。设置卡默认至少 410px 高，完整权重路径无彩色底；模型详情默认折叠，展开后卡片增高，不在模型卡内上下滚动。两页保存位置均有浏览、检查并使用、最近目录和当前目录；部署不提供“启用模型”勾选框。布局调整不会启动任务或改变采集/部署参数。

## 选臂记录位姿与单夹爪（2026-09-29）

“目标位姿”用于归位；“位姿名称”可选择已有名称或输入新名称。“记录哪些臂”只决定读取/保存哪些实测值，不改变设备图上的归位选择。已有名称显示“替换所选臂位姿”，新名称显示“记录新位姿”。记录不移动机器人。

- 只记录左前臂：左前、左后均可使用；右侧同理。
- 记录前双臂：前后四臂可使用。重录前臂时清除该名称中同侧旧后臂覆盖值，使后臂复用新前臂值。
- 同次记录前后四臂/五臂：各自保存实测值，后臂显式值优先。
- 未勾选的其他臂、其他位姿名称保留。归位可单选、Ctrl 多选或框选，只提供所有所选臂都能解析的位姿。

实现为 control/robot/home.py 实时读取→锁内合并→原子替换 YAML；后臂无独立值时由 resolve_selected 使用同侧前臂值。正式数据仍在 /media/agilex/Getea1/jiaan/data/motion/poses/home_poses.yaml，本批未改现场数据。

终端例子（分别执行；归位和夹爪动作需现场准备）：

~~~bash
cd /home/agilex/jiaan/project/cobot-control
# 只记录，不移动；同名只更新选择范围及同侧后臂共用关系。
./scripts/home.sh capture --targets front-left,front-right --pose my_pose
# 单臂/多臂归位，保留交互确认。
./scripts/home.sh selected --targets front-left,rear-left --pose my_pose
# 单夹爪保持当前开度的受控恢复；另一侧用 gripper-right。
./scripts/recover.sh gripper-left
# 单夹爪张开70mm、到位后停0.5秒、闭合；另一侧用 --side right。
./scripts/home.sh gripper --side left --pose reinit
~~~

设备图选一个夹爪，按钮只操作该侧；Ctrl 选两个可处理双夹爪。“恢复”在详情底部独立整行。Recover 与“张开/闭合”不同：前者保留既有恢复协议，后者显式开合；两者保留控制权、反馈与故障检查。算法、动作参数、ROS 接口和自动归位规则未改变。

CAN TX 堵塞检测及受控修复边界见 /home/agilex/jiaan/project/cobot-control/docs/DEPLOYMENT.md。本批未开启后台自动 CAN 复位。


## 保留模型恢复录制（2026-09-29）

采集模型卡新增“录制检查与恢复（保留模型）”。对应 CLI 为 scripts/console.py api POST /api/rlt/recorder-check 和 /api/rlt/recover-recorder；故障 Session 停止后无需重新加载权重。完整 JSON 示例、预检含义和拒绝恢复的情况见 [故障手册](WEB_RECOVERY.md#rlt-录制失败保留已加载模型2026-09-29)。

## RLT：训练步数、发布权重与实际使用（2026-09-29）

Learner caught up ... global_step=6915 只表示 Learner 已消耗当前更新预算，不表示6915已发布。现有配置每500个Learner步发布Actor；正常情况下6915时最新周期发布为6500，但页面从实际快照读取，不按整数除法猜测，因为启动/冻结等路径可能强制导出。

页面模型选项显示 published 步数；详情区分 Learner trained step / Learner internal Actor / Published learner step / Published Actor / Last inference Actor / Inference episode。Last inference 来自Session最后一次模型调用；waiting_scene时表示最近已完成轮次，不声称机器人正在推理。快照已发布也不能代替“实际使用”证据。

Cobot只读终端核对：
    cd /home/agilex/jiaan/project/rl-platform
    cat outputs/rlt/plug_v3_yyshadow/online/metrics/learner_status.json
    curl --noproxy '*' -s http://127.0.0.1:8015/api/deployment/status

第二项JSON的models对应模型提供published_learner_step/published_actor_version，session.actor_version提供实际最近推理版本。Learner内部Actor每2步更新，不能将Actor版本号直接当成Learner步数。

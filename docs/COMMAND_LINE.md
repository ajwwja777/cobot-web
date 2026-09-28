# Cobot 命令行操作手册

网页和终端操作归 `cobot-web` 维护；硬件实现仍交 `cobot-control`，数据格式交 `cobot-dagger`，模型／RL 算法交所属平台。不需要另开 ops 项目对话。

本文核对当前仓库入口，日期 2026-09-27。所有现场命令在 **Cobot** 的 Bash 终端执行：

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
cd /home/agilex/jiaan/project/cobot-web
./scripts/can_up.sh
./scripts/roscore_up.sh
./scripts/arms_up.sh
```

终端二：

```bash
cd /home/agilex/jiaan/project/cobot-web
./scripts/cameras_up.sh
```

机械臂与相机 launch 在前台运行，可以实时看完整输出；原终端 Ctrl+C 请求停止该 launch。ROS 子节点会创建各自的进程组，Ctrl+C 后仍应核对残留。手动启动的未登记任务不要按截图中的旧 PID 终止。网页或后端恢复后，以实际状态避免重复 launch。

CAN 重置会中断通信，必须先停止推理／采集并确认现场安全。网页对应的重置参数为 1 Mbps、restart-ms 100；确需在纯终端重置时：

```bash
sudo -v
for iface in can_left can_right can_mid can_rear_left can_rear_right; do
  sudo ip link set "$iface" down || break
  sudo ip link set "$iface" type can bitrate 1000000 restart-ms 100 || break
  sudo ip link set "$iface" up || break
done
./scripts/can_up.sh
```

缺接口时先查电源／USB-CAN，不用反复 reset 代替排查。

## 2. 选择路径与普通采集

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

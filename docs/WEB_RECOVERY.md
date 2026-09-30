# 网页故障与终端恢复手册

适用：Cobot 8015 网页、共享模型加载、普通／模型采集与 RLT。核对日期：2026-09-27。命令在 **Cobot 工控机**以 `agilex` 用户执行；在 A6000 或笔记本先 `ssh agilex@10.7.165.64`，不要在 A6000 本机执行 Cobot 启停命令。

主文档和 Git 在 A6000：`/data/LFT-W02_data/jiaan/jiaan/projects/cobot-web`。现场副本在 Cobot：`/home/agilex/jiaan/project/cobot-web`。现场查看：`less /home/agilex/jiaan/project/cobot-web/docs/WEB_RECOVERY.md`。

完整开机、采集、模型与评测流程见 [命令行操作手册](COMMAND_LINE.md)。命令按配置读取 runtime；当前为 `/home/agilex/jiaan/project/cobot-web/runtime`；恢复工具与证据随网页项目保存。

## 先记住这四点

1. **刷新页面不会停止模型或机械臂。** 页面显示失败、断线、卡住，都不能作为机器人已停止的依据。
2. **`ui_down` / `ui_up` 只重启网页。** 不会完整释放模型、Stage 1、ROS、相机或机械臂任务。
3. **HTTP 失败不一定等于操作没执行。** “开始／保存／成功／失败”超时后先查状态、记录和日志，避免连续点击造成重复操作。
4. **先暂停并确认机器人实际停住，再处理进程。** 暂停推理不等于断电或禁止所有其他控制源；软件暂停不能确认时，使用现场物理停止措施。

## 演示现场：先做什么

机械臂安全停住后，在终端查看状态、保留证据：

```bash
cd /home/agilex/jiaan/project/cobot-web
python3 scripts/console_recovery.py status
python3 scripts/console_recovery.py snapshot
```

这两个命令不发运动或停止命令。`snapshot` 会把状态、PID／PGID／SID、HTTP 结果和各任务最近 64 KiB 日志保存到 `runtime/incidents/<时间-进程号>/`；只保存排障证据，不新增训练／评测 episode。输出有实际保存路径。

| 看到的现象 | 首选处理 | 刷新或重启是否有用 |
|---|---|---|
| 排版错乱、按钮旧样式，接口还能响应 | 暂停后 `Ctrl+Shift+R` 强制刷新；核对是否访问 `10.7.165.64:8015` | 缓存问题通常有效 |
| 网络断开、浏览器一直转圈 | 先查 SSH、8015 和状态；确认服务退出再启动 | 刷新只能重连；网页进程故障才需重启 |
| 模型还在加载 | 看模型任务日志及实际进程，避免重复加载 | 刷新／重启不会加快权重加载 |
| HTTP 409 | 查当前 episode、Session、推理和采集状态；完成必要的暂停／结束步骤 | 多为状态冲突，重启通常不是正确步骤 |
| HTTP 422 | 查响应 detail 中的字段、输入、前后端版本 | 参数／接口问题，反复重启不能修复 |
| HTTP 500 / 503 | 查对应任务日志、8026 Session 和保存结果 | 后端掉线可恢复；程序错误或接口不兼容需要修复版本 |
| 超时、502 / 504、`signal is aborted` | 请求结果未知；先看是否已完成，再决定重试 | 页面重连可能恢复显示，不代表撤销了请求 |
| GPU 占用但模型显示已停止 | 查 `model`、`rlt`、`stage1` 及残留进程 | UI 重启不释放这些进程 |
| `Input/output error`、只读文件系统、进程状态 `D` | 停止新增读写，检查存储；见后文 | 属于存储／内核 I/O 问题，重启网页不能修复 |

本次“点击失败返回 503”的已查明原因是结束记录接口缺少 `operator_nodes` 参数支持，部分结果已经写入后才报错。已在网页迁移批次修复，但这不表示任何未来 503 都是同一原因；每次仍需查看实际日志和记录。

## 网页正常时的退出顺序

暂停推理 → 按需要保存或放弃当前轮次 → 结束 Session → 释放模型。再按需要停止相机、机械臂和 ROS；只是重新加载模型时，不必把硬件服务全部停掉。

结束／释放也报 HTTP 错误时，停止重复操作，查下面的终端入口。故障恢复命令不会替你标注成功失败、保存未完成轮次或执行复位。未完成 episode 可能留待人工检查；“停止进程成功”不代表数据成功提交到 replay 或训练。

### 仅重启网页

适用前提：机器人已停止；采集已结束；模型已释放，或已核实处于不会发动作的状态。`ui_down.sh` 会检查普通采集是否占用，但它不是覆盖所有模型状态的安全判定。

```bash
cd /home/agilex/jiaan/project/cobot-web
timeout 10s ./scripts/ui_status.sh
./scripts/ui_down.sh
```

确认上一条显示网页停止成功后，再单独执行：

```bash
./scripts/ui_up.sh
timeout 10s ./scripts/ui_status.sh
```

随后刷新浏览器。若 `ui_down` 提示 busy、状态无法确认或进程仍存活，不要直接再开一份服务，也不要手工删 PID 文件；使用下述恢复入口确认当前任务。UI 重启不会自动补做失败的保存／训练更新。

## 不依赖网页按钮的恢复入口

`scripts/console_recovery.py` 只依赖系统 Python 3 标准库，无需 conda／uv 激活。状态查询会限时访问 HTTP；进程身份与停止直接查 Linux `/proc`，不依赖 8015 响应。

### 查看与暂停

```bash
cd /home/agilex/jiaan/project/cobot-web
python3 scripts/console_recovery.py status
python3 scripts/console_recovery.py logs model
python3 scripts/console_recovery.py logs web
python3 scripts/console_recovery.py pause
```

`logs` 打印最近 64 KiB 输出和真实日志位置。实时追踪可对显示的日志路径执行 `tail -F`；此时 `Ctrl+C` 只退出看日志，不会停止生产日志的任务。

`pause` 优先直接访问本机 8026 RLT Session，带当前 episode／generation 请求暂停并复查；π0.5 则通过已部署的 ROS 暂停服务。不会 home、结束轮次或恢复推理。只有明确返回暂停确认才算软件确认；RLT 暂停回调仍可能受采集服务故障影响，失败时不要认为机器人已停。

### 中断单个任务：相当于对其任务进程组按 Ctrl+C

先只查看要停止的对象：

```bash
python3 scripts/console_recovery.py interrupt model
```

确认输出中的命令、PID、PGID、SID 是目标任务，并且机器人实际已停，才执行：

```bash
python3 scripts/console_recovery.py interrupt model --execute --robot-stopped
```

`--robot-stopped` 是操作者对现场状态的确认，**该参数本身不会停止机器人**。不带 `--execute` 永远只预览。`SIGINT` 发给经过登记身份校验的任务进程组，最多等 8 秒，报告残留进程；不会自动升级到更强信号。

| 任务名 | 对应内容 | 使用时机 |
|---|---|---|
| `model` | 共享加载的部署／采集模型 worker | 通常先停止它，再释放 Stage 1 |
| `rlt` | 旧 RLT 任务启动入口的 worker／learner | 状态显示它仍存活时处理；与 `model` 区分 |
| `stage1` | RLT 基础模型服务，通常持有大量显存 | 所有模型／RLT worker 停止后，确需释放显存才处理 |
| `arms` | 机械臂 launch 及所属节点 | 模型停止且现场安全后；不是网页问题的默认操作 |
| `cameras` | 相机 launch 及所属节点 | 无采集／推理使用相机时处理 |
| `home` / `recover` | 正在进行的归位／恢复任务 | 已现场停止，需中断挂住的操作时 |
| `roscore` | 单独启动的 ROS Core | 模型、机械臂、相机都停止后才考虑 |
| `web` | 8015 网页服务 | 仅向已核验的网页 PID 发信号，不杀其共享进程组 |

若 `rlt` 仍活着，对它同样先预览，再加 `--execute --robot-stopped`。模型停止后，确需释放 Stage 1：

```bash
python3 scripts/console_recovery.py interrupt stage1
python3 scripts/console_recovery.py interrupt stage1 --execute
python3 scripts/console_recovery.py status
nvidia-smi
```

Stage 1 命令会检查模型／RLT worker 和 Session 是否仍在使用它，发现使用者就拒绝。停止后仍有显存占用，不一定是同一模型；核对 GPU PID，不要终止其他训练任务。

若 SIGINT 后仍存在，先看日志及进程状态。确认不是 `D` 状态，并已完成现场停止，才考虑显式 TERM，例如：

```bash
python3 scripts/console_recovery.py interrupt model --execute --robot-stopped --signal TERM
```

这一步可能中断尚未写完的数据。工具没有 SIGKILL／`kill -9` 选项；不使用 `killall python`、泛匹配 `pkill` 或按过期截图里的 PID 杀进程。若任务不是网页登记的独立 session，工具会拒绝；优先回到原始启动终端按 Ctrl+C。

### 8015 完全无响应，普通 ui_down 也拒绝

先保证机器人已停，按上文处理仍运行的模型；保存 snapshot。再检查网页目标：

```bash
python3 scripts/console_recovery.py interrupt web
```

确认是本项目 8015 后执行：

```bash
python3 scripts/console_recovery.py interrupt web --execute --robot-stopped --signal TERM
```

它只结束网页进程，不会自动补保存、不清理 episode、不删除任务登记。确认输出 `Exited: web` 且端口释放后，回到网页目录执行 `./scripts/ui_up.sh`。如果仍残留，先排查日志／存储，不要同时启动第二份网页。

### 工具的边界

它核对 PID、启动时间、用户、独立 SID 和真实父子关系；ROS 子节点自行创建 session 时也纳入已确认的进程树。执行中断前，已见到的子进程身份另存于 `runtime/recovery-tasks/`，用于父进程退出后的残留检查／再次中断，不修改网页原登记。网页由于并非独立启动 session，只允许向已核验的单个 PID 发信号。

历史任务如果没有启动时间登记，或者在本工具检查之前父进程已退出、子进程又处于别的 session，不能仅凭原 PID 证明归属。状态输出会提示已识别但未登记的模型进程；这不是扫描所有潜在控制程序的完整清单。出现拒绝／未登记进程时，检查原终端和进程树；不要把“没有登记的活进程”理解成“机器上完全没有相关任务”。

故障保存日志也会访问磁盘。磁盘 I/O 已挂死时，SSH／Python／日志读取同样可能阻塞；这不是一个能替代物理停止或修复硬盘的工具。

## PID、终端、htop 和 nvitop 的关系

网页不是每个任务打开一个交互式终端。它用子进程启动命令，为任务建立独立 session，并把标准输出／错误写入日志；网页“输出”在读取这些日志。ROS launch、Python worker 还会继续产生子进程。

- `PID`：一个进程，网页显示的常常是 launcher，而 GPU 上显示的是子进程。
- `PGID`：进程组；终端 Ctrl+C 的效果是向前台进程组发送 SIGINT。
- `SID`：session；一个网页任务内可能有多个进程组。只杀父 PID 可能留下子进程。
- Stage 1 是另一个登记的服务，不一定随 worker 退出，这也是为什么关网页后 GPU 仍可能占用。

下面是只读命令；`<PID>` 必须换成**本次状态查询**给出的数字：

```bash
ps -p <PID> -o pid,ppid,pgid,sid,stat,lstart,args
pstree -ap <PID>
ps -e -o pid,ppid,pgid,sid,stat,args --forest
nvidia-smi
ss -ltnp
```

`ps`／`htop` 查看系统进程；`nvitop`／`nvidia-smi` 主要看 GPU 使用者，不能替代系统进程列表。htop 的高亮可能只是当前选中行，不证明搜索数字与 PID 匹配；需要精确核对时用 `ps -p`。查看线程时也可能出现不同 TID。

端口用于辨别服务，不作为随意杀进程的依据：

| 端口 | 当前用途 |
|---|---|
| 8015 | 网页和采集 API |
| 8026 | RLT Session／操作接口 |
| 8030 | RLT Stage 1 模型服务 |
| 9131 / 9132 | RLT actor / replay |
| 11311 | ROS Master |

快速检查网页身份（不用系统 HTTP 代理，最多等 3 秒）：

```bash
curl --noproxy '*' --max-time 3 http://127.0.0.1:8015/api/console/identity
```

## 数据保存错误时保留什么

记录出错时间、操作、模型真实路径、数据目录、episode ID、generation；保存 snapshot，核对采集历史和日志。尤其区分：标签文件已写入、视频仍在落盘、replay 尚未接受、learner 尚未更新，这几件事不是同一个“保存成功”。

不要通过删除 episode、清空 runtime、手改标签或换数据目录掩盖失败。本工具不改变数据；用户主动“放弃本轮”仍按产品的放弃语义处理。恢复后从明确的新轮次开始，先核对上一轮是否完整，避免演示记录被当成已验收训练数据。

## 出现 Input/output error 或磁盘满

停止采集／模型继续写数据，先确保机器人安全。可在终端检查：

```bash
df -h /
df -i /
lsblk -o NAME,TRAN,SIZE,FSTYPE,MOUNTPOINT
findmnt
sudo dmesg -T | tail -n 80
```

检查内置盘与实际数据盘；当前数据和模型实体均在 `/media/agilex/Getea1/jiaan/{data,model}`，代码/环境/日志在系统盘项目目录。读写错误不等于磁盘满：也可能是 USB 连接／供电、桥接芯片、文件系统或盘本体故障，应以系统日志定位，不能仅凭网页症状断言。

正在挂载、写入或有 `D` 状态进程时，不要直接热拔数据盘，也不要反复重启制造新写入。停止使用者后按存储维护流程安全卸载；卸载失败或持续硬件报错时，保留证据并停止现场演示。已有事故证据在 `runtime/recovery/20260927-getea-offline/`，不要执行其中历史脚本来“重放修复”。

## 恢复后再开始

检查 CAN／ROS／机械臂／相机真实状态 → 确认没有重复模型或控制源 → 确认模型路径与数据目录 → 加载模型，等待就绪 → 由操作者主动开始 Session／Episode。不要通过重启自动恢复旧动作；归位也是会运动的独立操作，应确认目标臂和位姿后执行。

仅模型问题不要重启所有硬件；仅页面问题不要重置 CAN。旧 `rlt_down.sh` 仍有历史版本入口，`rlt_v3_down.sh` 主要释放 Stage 1，不能当成“完整停止当前模型”的通用命令。优先用本手册按实际登记任务检查。

## 维护与验证

主代码在 A6000 的 cobot-web，现场只同步脚本和文档；运行时证据不入 Git。问题交接时带 snapshot 路径、网页版本与复现步骤：页面／任务管理交 cobot-web，数据交 cobot-dagger，模型与 RL 交相应平台，硬件与 CAN 交 cobot-control。

脚本的隔离测试覆盖 PID 复用拒绝、缺失身份拒绝、多进程组／ROS 式独立子 session、父进程退出后的子进程、网页只停止单 PID、暂停令牌与不可达接口。真实机械臂的停止／恢复没有在本次文档交付中演练；现场验证只做只读查询与停止预览。具体部署版本、校验值和验证记录见 [迁移记录](MIGRATION.md)。

## RLT 录制失败，保留已加载模型（2026-09-29）

在采集页模型卡展开“录制检查与恢复（保留模型）”；Session 报 fault 时自动展开。
先点“检查录制”，它检查三相机、关节反馈、控制权和所选保存目录的可写性/空间，不启动推理、不创建 Episode。
根据显示的具体原因处理：camera_stale 检查相机；handover_stale 检查节点/按钮；streams_not_ready 按列出的流检查；not_writable / disk_space_low 检查 Getea1 挂载、权限或容量。

输入恢复后点“恢复录制（保留模型）”。对暂停且无未决 Episode 的启动失败，它仅停止故障 Session、恢复退出后的错误录制器，并再次预检；模型/在线 Learner 保留。完成后手动点击“开始 Session”，再开始采集。不会自动重试推理、归位、释放模型或丢弃未决数据。正在写入、仍有未完成 Episode 或策略未暂停时拒绝恢复并提示原因。

故障状态也可点“结束 Session”，不再要求释放模型。刷新浏览器不能清除后端故障；ui_down/up 可重启网页，但不是这类问题的第一步，且进行中的录制不可重启。

终端与网页使用同一接口（把路径换成页面实际保存目录）：

~~~bash
cd /home/agilex/jiaan/project/cobot-web
python3 scripts/console.py state recorder
python3 scripts/console.py api POST /api/rlt/recorder-check --json '{"data_root":"/media/agilex/Getea1/jiaan/data/datasets/test"}'
python3 scripts/console.py api POST /api/rlt/recover-recorder --json '{"reset_fault_session":true,"data_root":"/media/agilex/Getea1/jiaan/data/datasets/test"}'
python3 scripts/console.py state model
~~~

检查返回 preflight.status=ok 才表示该时刻预检通过；model_retained 表示未释放，不代表真实推理/整轮验收通过。工作线程未退出或数据未决时不强杀 PID，请先按输出处理该轮。恢复不会修复 USB 掉线、硬盘 I/O 故障或机械臂故障本身。

## π0.5 在 Recover 后点击开始仍暂停（2026-09-29）

后臂 Recover 完成后失能、协调器回到 policy 是正常待机。旧网页 π0.5 客户端把所有非网页的 pause 请求都误记为 HIL，Recover 的保护性暂停因此可能留下外部锁；日志反复 paused / takeover 不等于又按了示教按钮。

已修正的新客户端区分手动、保护与示教暂停，仅实际协调器的请求计入 HIL。Recover 保持手动保护暂停，并退休恢复前的旧HIL锁；之后由操作者点击开始/继续。开始前会核对协调器已回policy且故障为空，真实示教或未恢复完成仍阻止继续。新版在下一次正常加载时生效，不需要为当前故障释放已加载权重。

已经加载的旧版可用下面的有界恢复入口。先停止当前操作并松开示教按钮，确认设备安全；此命令检查已登记的 π0.5 PID/启动时间、ROS 服务所有者、协调器 policy 和三处空故障，持有与网页相同的模型操作锁，先锁定手动暂停再清除旧外部锁。检查不通过就保留暂停，不执行 Recover、归位、启动推理或结束模型。

~~~bash
cd /home/agilex/jiaan/project/cobot-web
source /opt/ros/noetic/setup.bash
/usr/bin/python3 app/backend/cobot_console/deployment_ros.py repair-pause
cat runtime/deployment/pi05-gate.json
~~~

成功返回 Legacy latch cleared; manually paused，paused 与 manual_pause 都应为 true。随后由操作者在网页点击开始（无活动轮次）或继续（暂停的活动轮次）。已加载的旧客户端在你主动点击开始/继续时，也会先完成同样的有界检查和暂停锁协调，再执行本次请求；无需每次手动运行修复命令。真实示教或故障时拒绝继续。下次正常重载客户端后直接使用新版分类。恢复保留原 intervention_count 和已有评测记录，不改写历史标签；下一轮沿原规则取新的计数基线。

网页调用的 bridge 同时检查服务的实际回复；若仍为 paused，返回明确失败原因，不再把服务返回 success 当作推理已继续。repair-pause 不适用于 RLT/其他模型、新协议客户端、真实示教中或硬件故障；这些情况按具体状态处理。

## RLT 提示 latest episode labels are incomplete（2026-09-29）

旧版本在含人工示范或其他模型记录的目录开始 RLT 时，可能将已完成但未标注的历史记录当作阻塞。新版统一 flat 采集允许这些记录继续保留，不自动补成功/失败或加入训练。独立 legacy 录制接口维持原标签门禁。

网页采集控制中打开“录制检查与恢复（保留模型）”，先“检查录制”；若 Session fault，点击“恢复录制（保留模型）”，通过后手动开始 Session/采集。结果会显示实际检查目录，不能只凭“模型已加载”判断录制可用。若提示 latest_episode_incomplete 或 latest_episode_invalid，保留原文件，等待原写入完成或选择另一保存目录后重查；不要删除未知记录或反复开始。恢复不会执行机器人运动或重载 GPU 权重。

## RLT runtime exited / RTC 执行超时（2026-09-30）

采集与部署的模型卡会显示具体故障、Stage1 是否仍保留，以及“重新检查状态”“恢复运行进程（保留模型）”“查看输出”。运行进程退出时不再把最终 Supervisor traceback 当作模型加载失败，也不会把它归成录制故障。Stage1 保留不等于 Actor/Session 就绪；页面只有运行进程恢复就绪后才允许手动开始。

本次 19:50:31 的直接错误为 RTCActionStateError: actual delay exceeded predicted delay；所选 plug-v3-credit-mc30-rtc50 的 RTC 窗口是 4 个 20 Hz 逻辑步（200 ms），不是 50 Hz 发布步。超过窗口时拒绝安装迟到动作，暂停策略并退出 EnvDriver；Supervisor 随后停止 Learner、Actor 和 Replay。Learner 保存了 step7000，Stage1 PID233064 仍在。旧日志没有完整的端到端分项耗时，不能确定是模型、录制 HTTP、调度还是其他负载导致。新日志记录实际/允许延迟及模型和录制检查耗时；没有放宽窗口或修改算法。

先点“重新检查状态”，再查看输出。确认故障原因后可点“恢复运行进程（保留模型）”：复用同 checkpoint 的已登记 Stage1，只重建本模型运行进程。恢复不会开始 Session/推理、归位、删除录制、补标签或隐式提交 Replay。旧运行进程仍存在、Stage1 不在、存在评测轮次、录制仍在写入或待收尾时拒绝恢复。孤立 writer lease 只有在录制已经 committed 后才释放。Stage1 在检查后掉线，启动脚本也拒绝重读权重。

终端与按钮对应的调用：

~~~bash
cd /home/agilex/jiaan/project/cobot-web
python3 scripts/console.py state model
python3 scripts/console.py api POST /api/rlt/recover-runtime
python3 scripts/console.py state model
~~~

底层是 cobot_console/deployment.py:ManagedRuntime.recover_runtime，持有与加载相同的进程锁并调用原 scripts/deployment_run.sh → rl-platform/scripts/rlt_up.sh；设置 COBOT_RLT_REQUIRE_RESIDENT_STAGE1=1，保留 Stage1。就绪后手动开始 Session/新轮次。不要同时另起终端运行同一模型。

若 RTC 超时重复出现，停止本轮并选原 plug-v3-credit-mc30（同步20 Hz，同候选权重/训练分支），再手动加载。故障进程完全退出且 Stage1 保留时，可在采集/部署卡改选 RLT 并加载，无需先释放 Stage1。不要仅降低发布 Hz 就认为 RTC 延迟窗口变大；四种发布频率的逻辑窗口相同。刷新页面只更新显示；ui_down/up 仅重启网页，不能复活已退出的 EnvDriver。完整真机延迟/争用验收仍待完成。

网页停止入口已识别“录制 stopped/complete/committed、RLT 运行进程及所属进程组已完全退出”的孤立占用标记；仅此情况下可正常 ui_down/up，保留完整录制与 Stage1。活 writer、未提交文件、状态过期、普通采集、评测或模型操作仍拒绝停止。检查实现位于 app/backend/cobot_console/ui_shutdown.py，停止脚本仍仅 SIGTERM 已登记的网页 PID，不杀模型/硬件。

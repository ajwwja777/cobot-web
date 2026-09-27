# Cobot Web 迁移记录

日期：2026-09-27。当前结果：**网页代码、环境、正式 8015、runtime 与 uv 已在新项目；A6000／Cobot／笔记本 ops 目录已删除。旧 cobot-platform、模型与 RLT 仍有依赖，保留。**

以下较早批次保留当时状态；最终路径、删除范围和验证结论见本页末尾“runtime 收尾与旧目录清理”。

用户已明确停止当前任务并授权切换。核对部署、录制和原 RLT 状态后，正常停止残留的故障 Session／学习进程，再切换网页；没有重新启动机械臂、相机或发送运动指令。

## 位置与版本

| 内容 | 实际位置 |
|---|---|
| A6000 主代码和 Git | `/data/LFT-W02_data/jiaan/jiaan/projects/cobot-web` |
| Cobot 运行副本 | `/home/agilex/jiaan/project/cobot-web` |
| Cobot uv | `/home/agilex/jiaan/project/cobot-web/tools/uv` |
| 日志、PID、uv 缓存 | `/home/agilex/jiaan/project/cobot-web/runtime/` |
| 只读预览运行记录 | `/home/agilex/jiaan/project/cobot-web/runtime/web-preview/` |
| 新数据根 | `/home/agilex/jiaan/data` |
| 正式 8015 当前来源 | `/home/agilex/jiaan/project/cobot-web/app/backend` |

仓库：https://github.com/ajwwja777/cobot-web，main。源码提交 `1cf4797f4942f2f27d3815943f7e23cab79452df`；启动依赖隔离修复 `117fe141aea17e9081a1db535e557a155fa136f3`。均已 push 并核验远端一致。

A6000 的 `scripts/sync_cobot.py` 仅同步已提交运行文件，不重启进程。共享模型批次 Cobot `.release.json` 记录版本 `3b54cf7`，274 个运行文件 SHA-256 一致。开发测试、Git、模型、数据不随源码同步。文档后续提交可能领先运行副本，运行版本以 .release.json 为准。

## 原件与成果保留

从当前实际工作树保存源码，包含未提交和未跟踪成果，没有仅依赖旧 Git HEAD。证据位于：

- A6000：`/data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/outputs/migrations/20260927-web/`。
- Cobot：`/home/agilex/jiaan/project/cobot-ops/runtime/migrations/20260927-web/`。

`platform-source.tar.gz` 的 SHA-256 为 `6d0b7fa630e8ccc14884b4df1eed8c865a46b1603b49af00ad5e96eaab716819`；清单逐文件核验通过。旧后端 HEAD 为 `2a3ea22d0f284e8161533136088c467b6b4bb048`，完整 Git bundle、工作树 diff、状态与原始解包副本均保留。共享 schema、控制辅助代码与 RLT 缓存 writer 的来源见 `docs/SOURCE_PROVENANCE.json`。

只在本项目保留一份兼容实现，未把相同控制／采集代码复制进多个项目。旧 `task5_console`、`task5_hil`、`task5_segmented_teach` 分别迁为 `cobot_console`、`capture_core`、`segmented_capture`。控制辅助代码暂在 `integrations/legacy_control`，后续由 cobot-control 接管。历史 ROS 话题、HTTP 和数据协议、历史文件名继续兼容。

## 本批改动

- 五臂状态使用只读 CAN 反馈、ROS 节点与控制通路。已使能的前臂／中臂正常显示绿色；只有后臂示教状态、协调器模式、新鲜反馈和实际跟随同时成立才显示蓝色。夹爪继承对应前臂状态，自身故障单独标黄。
- CAN、ROS、节点、控制通路、独立示教、退出示教未失能等情况提供中英文原因和处理建议。这是展示层观察，不改变真实控制状态机。
- 工控机空间读取系统盘 `/`。CAN／ROS 状态增加圆点。
- 设备面板支持拖拽矩形框选、Ctrl 累加框选及 Ctrl 点击切换，取消拖拽不改变选择。
- 修复 ROS 日志 latest 链接的并发创建竞争；补丁限定于本项目启动器，不修改系统 ROS。任务输出默认显示重要信息，保留原始日志切换及真实故障，不把错误伪装成启动成功。
- 根目录 `pyproject.toml`、`uv.lock` 固定 Python 3.8 网页依赖；A6000 和 Cobot 使用各自项目 `.venv`。Node/jsdom 只在 A6000 测试。
- 启动时移除已不再需要的外部 RLT Python 搜索目录；预览使用自己空的录制目录，不初始化或解析正在使用的 rollout 目录。

## 已验证

| 检查 | 结果与范围 |
|---|---|
| 网页后端完整回归 | 529 passed，11 skipped；跳过项依赖未接入 A6000 的外部 RLT 集成 |
| 前端 Node/jsdom | 28 passed；含真实 DOM 拖拽、Ctrl 累加／切换、取消与键盘交互 |
| 最后启动器变更 | 相关 14 项通过；shell 语法检查通过 |
| 独立 handover 测试 | 修复旧绝对路径后 9 passed，无硬件操作 |
| 日志并发修复 | 并发创建／替换 latest 链接回归通过，非链接目录受保护 |
| Cobot 真实只读观察 | 前左、前右、中臂反馈 6/6 使能，左右控制通路就绪；全程仅订阅／接收，没有运动指令 |
| Cobot 8018 预览 | 新入口约 0.8 秒就绪；29 个页面资源均 HTTP 200 |
| API | identity、host、config、模型目录 HTTP 200；返回 port=8018；系统盘路径为 / |
| 预览操作隔离 | POST 操作返回 403；不创建 ROS 录制订阅或运动发布者 |
| 正式服务 | 初次预览期间保留旧 PID 628249；授权切换后由新目录 PID 893608 接管 8015 |

机器可读摘要见 `docs/verification-20260927.json`，详细日志在证据目录。未连接可操作浏览器，因此没有完成实际浏览器画面／动画验收；DOM 和资源检查不等于目视验收。蓝色同步及故障矩阵通过模拟测试，但未为本批迁移人为进入示教、拔 CAN 或操作机械臂。

预览第一次重启曾在 FUSE 路径访问处等待并超时，8015 部分状态接口也一度超时。随后目录 stat 恢复正常，近 30 分钟内核日志没有对应警告／错误。移除不必要的旧磁盘导入路径并隔离预览录制根后启动通过；不能由这一次现象判定硬盘再次损坏，仍应关注原路径依赖。

## 尚未验收的兼容部分

扩展执行历史 robot 测试时发现既有问题：A6000 没有现场 ROS 模块，部分测试收集失败；排除三组依赖测试后的历史组合运行记录为 269 passed、7 failed、9 errors。其中 9 个路径错误随后修正，独立 handover 9 项通过。其余包含 ROS stub／模块隔离问题，以及夹爪 `0x10/0x20` 提示位处理与旧断言不一致；当前 `robot/gripper_cycle.py` 与迁移前原件逐字节一致。没有为了让测试通过修改硬件容错或控制规则。该部分交 cobot-control 核对原设计，再补齐兼容与现场验收；**不宣称硬件套件全部通过**。

模型权重、RLT 算法环境和 RLT 原始数据仍在原位置，见 `configs/hosts/cobot.json`：

- RLT：`/media/agilex/Getea1/jiaan/projects/rlt`。
- 旧数据：`/media/agilex/Getea1/jiaan/data`。
- π0.5 与 DAgger 部署、Piper／ROS 驱动：仍使用已登记的 `/home/agilex/cobot_magic/` 现场依赖。
- 这些依赖后续分别归 rl-platform、vla-platform、cobot-control。尚未复制大权重，没有改变用户在线更新的目录、比例或参数。

## 切换与清理状态

首批源码迁移时可清理清单为空，原件当时全部保留；最终清理情况见本页末尾。新目录中仅移除了本批草稿复制产生的重复 `app/backend/pyproject.toml`，使用根目录的唯一依赖声明。

## 8015 切换验收与收尾接口修复

2026-09-27，在用户明确授权的停止窗口内完成：

- 旧 353 个源码文件与迁移快照再次逐项比较，无新增变化；位姿保持原值。旧网页正常停止，新网页 PID 893608 从新目录启动。
- 机械臂 PID 724550、相机 PID 631028 保持不变。原 RLT PID 843135 处于终止失败后的故障状态，经正常 SIGINT 停止进程组，未自动重开模型或训练。
- 承接 8 个任务状态及模型选择，保留实际命令／日志来源；旧 pose.json 本身含多余 JSON，原件归档到 cutover/invalid-legacy-pose.json，未把损坏文件当有效状态。
- 部署评测的 672 个文件（11,849,135 字节）复制到 /home/agilex/jiaan/data/evaluations，逐文件 SHA-256 相同。旧数据原件保留，当前评测设置使用新根。
- identity、设备、相机、主机、部署状态／记录、任务输出及 console 状态均返回 200；五臂状态 ready，三相机同步 ready。历史读取成功。
- RL“失败”HTTP 503 的原因：原生客户端在保存 outcome 后提交 operator_nodes，但网页请求 schema 未接收此字段，导致 422 再被上层包装成 503。新增严格节点字段、operator_save 原因，以及已有 prepare／marker／save 路由白名单。失败的旧 episode 原件和已有 failure 标签保留。
- 收尾与进程身份相关 82 项回归通过；提交 14b4484 已 push，同步 273 个运行文件核验一致。实际任务启动命令同时识别登记的新／旧根，不放宽为任意同名路径。

证据：两台机器上述迁移证据目录中的 cutover/，包括 before.json、after.json、runtime-transfer.json 和 recording-contract-tests.log。未执行真实机械臂采集循环；不把接口回归称为现场成功率验收。

## 共享模型与目录选择发布

2026-09-27 源码 3b54cf7f58f1a35f70756194cb218484ee39c0e9 已 push 并核验远端，274 个运行文件逐项同步。核对录制 idle、无评测轮次、RLT offline 后重启网页；正式 8015 当前 PID 916985，cwd 为新目录 app/backend。机械臂 724550、相机 631028 保持不变。

- 部署／采集共用 DeploymentManager、模型目录、加载进程和状态。同一模型重复加载复用已有进程；加载后给出成功状态，准备 Session 保持暂停。
- 六个入口的实际权重路径均可读：warmup 5k、Stage 1 Reference、π0.5 DAgger 2000+3000、π0.5 step2000、当前 actor 冻结版本、当前 actor 在线更新版本。在线入口仍沿用原 online.yaml 和 learner；评测拒绝在线更新入口并提示选择同路径冻结版本，避免评测期间权重变化。
- shared_model_env.py 仅在 episode 边界选择用途：采集使用原 Task5 客户端和原 replay 门控，评测使用内存 lifecycle 且不提交 replay/trace；原 actor、执行器、HIL 状态机未改写。用途和数据路径每轮开始时固定，收尾及 replay 完成前不切换。
- 数据目录编辑独立于模型加载；RLT 在录制时可为下一轮选目录。设置写入 cobot-ops/runtime/data-console/rlt-storage.json，兼容读取旧设置。切换时保留此前实际录制目录 /media/agilex/Getea1/jiaan/data/rlt/plug_v3_yyshadow/online；原始 rollout 尚未迁移，不以新默认根掩盖已有数据。
- 原报错 episode cb5af67e-0f9b-484f-8a85-7488210faf74 的 failure 原件保留，历史列表已可读取。没有伪造已提交训练 replay 的结论，也没有重新标注或补写训练。
- 前端保留后端 error 文本，避免所有异常仅显示 HTTP 503；正在加载仍用文字预计时间，不增加进度条。

验证：完整网页后端 545 passed、15 skipped；之后新增的目录 HTTP 回归所在集合 12 passed（其中 3 项新覆盖 offline/loading/paused）。前端 31 passed，含真实 DOM 两页共享状态、路径展示和加载期间目录编辑。用现场原生 adapter 快照在独立 Python 3.10 环境测试：成功、失败、保存未标注连同节点写入，以及采集转评测隔离，共 4 passed。主开发 .venv 仍为 ROS 兼容的 Python 3.8。现场 Python 3.10 只做配置生成／资产存在性检查，三个冻结／Reference 配置引用的权重、归一化和 replay 文件存在；未加载模型、未运行真机 Episode。

发布后 12 个 API、34 个页面资源均 HTTP 200；相机同步 ready，系统盘显示 /。证据为 cutover/after-shared-release.json、shared-storage-migration.json、shared-final-pytest.log、shared-storage-tests.log、shared-final-node.log、native-shared-contract.log 和 config-checks/；upstream-contract/manifest.json 记录只读合同快照来源和 SHA-256。after-shared-release.json 是目录设置承接之前的瞬时快照，最终录制目录以 shared-storage-migration.json 为准。

下一批迁移 RLT 与模型资产前核对原路径依赖。旧网页硬件代码仍被运行中的节点引用，旧目录继续保留。实际模型加载／真机采集循环与浏览器目视体验仍需现场验收；这一批没有为了验证网页自动运动。

早期入口初始化提交：`7d81a477adb6e89625bb9454d6c3d465cc237966`；初始化记录提交：`d08e656a6e8f16b3bd4ab64269c270dd9e52a5b8`。本记录更新的是实际迁移进展，不撤销已保留的原成果。


## 2026-09-27：命令行流程、网页诊断与 ops 归并（已部署）

- 用户取消独立 ops 维护层：网页使用、任务管理、日志／PID 与 HTTP 恢复归 cobot-web；硬件／数据／算法直接在所属项目处理。旧 ops 保留 Git 历史、迁移记录和兼容转发，不再维护第二份实现。
- 从 cobot-ops `da1f9a9` 迁入恢复工具、13 项进程身份／暂停测试及故障手册；工具按网页主机配置读取 runtime。当前 `/home/agilex/jiaan/project/cobot-ops/runtime`、恢复证据和 tools/uv 仍有使用者，本批未移动或删除。
- 新入口 `scripts/console.py`：模型／Session、普通与模型采集、评测、路径、设备、状态、完整 API/schema，以及不依赖 8015 正常的 recovery。与网页同一套状态机，读取新鲜 episode／trial 身份，不自动重试写操作、不隐式复位。
- `docs/COMMAND_LINE.md` 补齐开机、CAN／ROS／机械臂／相机、模型、采集、评测、home、输出、退出及纯终端原始入口；`docs/WEB_RECOVERY.md` 继续负责故障处置。旧 warmup.sh 是 plug_v2，不误写成 plug_v3 忠实复现入口。
- 输出栏增加“诊断”，捕获方法／路径／状态／时间／原始错误并给出建议和终端命令，区分响应失败与操作未执行。可手动查设备／CAN／ROS、相机、采集、模型、录制器、磁盘。诊断不自动执行恢复，原始错误只在页面内保留。
- 常用命令同步修正：旧无身份暂停请求改为读取当前 Session 后暂停；原“释放 RLT 模型”只关 Stage 1 的示例改为完整共享模型 unload；增加模型、采集与只读诊断命令及英文标签。
- 功能发布 `91a5fe0ecc7f455dce12bffd6e620ccaa29d858f`，命令登记修正 `94840c1175227e7e339acbf3b8d15494065be8b6`，均已 push 并核对远端。同步工具现在包含 docs／AGENTS，285 个运行文件 SHA-256 校验一致。
- 验证：完整 Python 571 passed／15 skipped；前端 34 passed。首轮全量中既有 HDF5 slow-drain 时序用例曾返回 stopping，单独复查与完整复跑均通过，未改录制代码规避；原始结果均保留。后续常用命令修改的相关 30 项 Python 回归、34 项前端通过，新增 locale 文件语法通过。
- Cobot 只读 CLI：帮助、恢复状态、6 个模型列表、65 个主应用 API／20 个 recorder API，以及 console/model/devices/cameras/recorder/host/outputs/records/storage/training 全部成功；首页及诊断 JS/CSS 返回 200 且内容与同步文件一致。
- 为常用命令后端更新生效，重新核验 active_mode 为空、模型 offline、无 active trial 和 operation 后，只重启 8015：PID `916985 → 967289`。启动就绪；机械臂 7、相机 4、ROS 3 个进程的 PID 与启动时间逐项不变。当前输出提供 61 条常用命令。
- 没有启动采集、加载模型、执行运动／归位／恢复或停止硬件；未做真实模型回合验收、浏览器目视验收。UI 做了 DOM／错误捕获／脱敏／不重试测试；现场只读与静态资源验证不等于实机操作成功。
- A6000 证据：`outputs/verification/20260927-cli-recovery/`；Cobot 同项目同相对目录。同步逐文件校验另在 `outputs/deployments/`；运行输出不入 Git。
- 后续：在所属项目补充现场问题与验收；活跃 runtime 的实体搬迁另选可验收范围，不因此再建立独立 ops 管理层。

## 2026-09-27：runtime 收尾与旧目录清理（已验收）

用户要求验证新框架独立运行后清理旧项目，包括 ops。本批独立性指网页／终端不再依赖 ops 目录；不表示硬件驱动、RLT 和全部模型资产已迁移。

### 实际迁移与删除

- 发现 cobot-web/runtime 原本是指向 cobot-ops/runtime 的软链接；本批移除该链接，将 runtime 和 tools 实体归入 cobot-web。停止写入者后迁移，2,819 个文件／链接条目在改路径前逐项比对一致；保留日志、PID、任务记录、模型／数据目录设置、恢复证据、缓存和 uv。
- 32 个 uv 缓存绝对链接改成新位置的相对链接；home 历史任务的 log_path 改到迁移后的同一日志。历史启动命令、PID／启动时间与原始事故正文保持真实来源，不批量改写旧证据。
- 8015、8018 在确认采集 idle、模型 offline、无操作和评测轮次后正常停止，再从新 runtime 启动；8015 PID 967289 → 982318，8018 PID 845046 → 982430。机械臂／相机／ROS 共 14 个进程 PID 与启动时间不变。未发送运动、归位或模型推理指令。
- configs/local.json 与已提交的 configs/hosts/cobot.json 使用 /home/agilex/jiaan/project/cobot-web/runtime。数据路径和模型选择逐项比较保持一致；RLT storage 的实际选择以迁移前状态保留，没有强行改成新数据根。
- 验收并复制异机备份后删除 /home/agilex/jiaan/project/cobot-ops（迁出内容后的 7 个残留文件）、/data/LFT-W02_data/jiaan/jiaan/projects/cobot-ops（103 个已归档文件）、D:\Code\jiaan_workspace\cobot-ops（只有 1 个入口文件）。未保留旧路径软链接。
- 旧 ops Git 最终提交 b67e3e074135450c9f6441409fbec0ee38c9bfee 已 push、核验并保存完整 bundle；GitHub 历史仓库保留为退休指引，不作为开发或运行入口。guide 原 ops 摘要归并至 cobot-web，旧摘要目录删除。
- 五个领域入口中残留的 ops 归属改为所属项目；网页的任务／PID／故障处理归 cobot-web，不另建维护项目。

### 验证

- 网页配置切换提交 b1190ddfc2ef609ceccbbc528db90e0e61fc64c2 已 push；285 个运行文件 SHA-256 同步核验。
- 命令行与恢复相关测试 23 passed；本批未修改采集／推理／硬件业务实现，因此不重复扩大全量测试范围。此前完整验证为 Python 571 passed／15 skipped、前端 34 passed。
- Cobot uv 0.11.2 离线 sync 检查 32 个包，pip check 全部兼容；.venv 无 ops 链接，缓存无悬空链接。
- 正式网页 11 类只读状态、37 个引用资源、6 个模型目录可读取；6 组 CLI（启停状态、恢复状态、home 历史日志、模型列表、主／recorder API 索引）通过。普通模式访问 RLT Session 返回 409 rlt_mode_not_selected 属于模式边界，未为测试切换 Session。
- 删除 ops 后再次读取 8015 identity 正常；无同用户活跃进程的 cwd、环境、命令或打开文件仍引用 ops。恢复证据内容核验一致。
- 未加载大模型、未采集真实 Episode、未重启硬件进程；接口和环境验证不等于运动或成功率验收。

### 备份与证据

A6000 本项目相对路径：
- outputs/verification/20260927-runtime-cleanup/：前后状态、2,819 条迁移清单、API／资源结果、23 项测试、现场删除回执与现场备份。
- 现场备份 cobot-ops-site-backup.tar.gz：SHA-256 898861cd20f3aa4319ad27d8634d05cc0c9c3277fc7490d77f4b34c4f7caac70；101 个非缓存文件逐项核验，uv 可再生成缓存未重复打包，但实体迁移已校验。
- outputs/migrations/20260927-ops-retirement/：A6000 原 checkout 完整备份、Git bundle、旧 outputs 原件、笔记本入口备份与删除清单。
- 笔记本入口备份 SHA-256 048d297db3a5cc78aa347c833b1bef3b4dd7c4a643eec791b91e61e0f52b2b16。
- 原硬盘事故证据现在位于 Cobot 本项目 runtime/recovery/20260927-getea-offline；A6000 原备份在上述 previous-outputs/migrations/20260927-recovery。现场 runtime/incidents 和 runtime/migrations 一起承接，历史路径正文不改写。

### 尚不能删除的旧范围

| 旧位置 | 仍需保留的原因／下一步 |
|---|---|
| /media/agilex/Getea1/jiaan/projects/cobot-platform | 当前 arms、camera、ROS 进程仍有代码／cwd／日志依赖；archive 约 22 GiB 有 dm0-5 与 Xiaomi DAgger 等历史资产，未做完整归属和迁移验收。下一批先由 cobot-control 验证节点从新路径启动，再核对归档资产。 |
| /media/agilex/Getea1/jiaan/projects/rlt 与旧 data/rlt | 算法、Python 环境、权重、replay、原始 rollout 仍被 6 模型入口引用；下一批归 rl-platform，保留忠实复现配置并做加载对照。 |
| /home/agilex/cobot_magic 的 Piper／camera／aloha／π0.5 相关目录 | 现场驱动、SDK 和 π0.5／DAgger 模型依赖，分别归 cobot-control／vla-platform 后续验收。不能整棵删除 task3/task5 或共用工作区。 |
| A6000 旧 VLA／RL 项目 | 新框架其他领域多数仅完成入口初始化，历史训练与模型资产未整体迁移；不因网页可运行就删除算法仓库。 |

终端进入 /home/agilex/jiaan/project/cobot-web；旧同名 ui_up.sh、ui_down.sh、ui_status.sh、arms_up.sh、cameras_up.sh、home.sh 仍由新项目 scripts 提供。推荐 scripts/console.py 统一状态、共享模型、采集和评测；recovery 子命令不依赖 8015。完整流程及限制见 COMMAND_LINE.md、WEB_RECOVERY.md。

### 本批更新的 Markdown 位置（A6000）

- /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/AGENTS.md
- /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/README.md
- /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/docs/COMMAND_LINE.md
- /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/docs/WEB_RECOVERY.md
- /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/docs/MIGRATION.md
- /data/LFT-W02_data/jiaan/jiaan/projects/cobot-control/README.md
- /data/LFT-W02_data/jiaan/jiaan/projects/cobot-dagger/README.md
- /data/LFT-W02_data/jiaan/jiaan/projects/vla-platform/docs/JIAAN.md
- /data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/README.md
- /data/LFT-W02_data/jiaan/jiaan/agent-guide/README.md
- /data/LFT-W02_data/jiaan/jiaan/agent-guide/projects/README.md
- /data/LFT-W02_data/jiaan/jiaan/agent-guide/projects/cobot-web/README.md
- /data/LFT-W02_data/jiaan/jiaan/agent-guide/projects/cobot-control/README.md
- /data/LFT-W02_data/jiaan/jiaan/agent-guide/projects/rl-platform/README.md

旧 ops 的 README.md／AGENTS.md 在删除 checkout 前已提交退休指引到 b67e3e0；完整旧绝对路径和删除清单见本节上文与备份回执。原 guide 摘要 /data/LFT-W02_data/jiaan/jiaan/agent-guide/projects/cobot-ops/README.md 已删除并归并；笔记本 D:\Code\jiaan_workspace\cobot-ops\AGENTS.md 已备份后删除。

## 2026-09-27 23:55：RLT／硬件归属切换代码（待现场部署）

本轮 A6000 代码把硬件入口转发给同级 cobot-control，把 RLT v3 入口转发给同级 rl-platform；模型路径分为 models/rlt/plug_v3_yyshadow/{stage1,warmup-5000,online}，Replay/日志归 outputs/rlt/plug_v3_yyshadow，数据统一 /home/agilex/jiaan/data。共享模型、HIL、收尾及评测隔离语义不变。

configs/hosts/cobot.json 是新切换配置；现场 configs/local.json 尚未切换。保留 legacy_platform_root 仅供识别、停止旧进程；当前旧硬件进程仍需核验退出。robot / integrations/legacy_control / 旧位姿副本待 cobot-control 现场验收后再移除；不是最终重复维护结构。

完整后端测试 576 passed / 11 skipped，路径与输出后续回归 63 passed / 1 skipped；已有原生 RLT 成功／失败／未标注／评测隔离合同均通过。用户已确认断电、允许重启节点，但现场 SSH 随后超时，尚未进行本次网页同步或服务重启。最后可读的正式版本仍为 d5fe477，不能把本次新路径记为已上线。

当前继续点：先恢复 Cobot 连接、核查数据校验和 Stage 1 --validate-only 结果，再做节点与网页切换、API／模型路径／数据历史验收及旧目录清理。Guide Git 由另一 agent 管理。本轮未删除旧 RLT、cobot-platform 或旧数据。

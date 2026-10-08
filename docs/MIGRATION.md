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

## 2026-09-28：正式网页切换至新 RLT／硬件／数据根

正式8015于14:32从新web项目启动，PID95649，代码556751e；local.json及runtime目录设置先备份后切换。运行实现分别归web/control/rl-platform。

- 四个RLT模型入口指向新models；两个π0.5入口保留登记的共享旧部署目录，另批归vla-platform，不属于旧cobot-platform。
- 固定Warmup5k与最新在线模型从共享入口到ready、disarmed/paused，Session未开始；加载释放成功。恢复5000/2500/2567，正式权重与Replay未变。
- 11,123数据文件SHA通过。浏览器四个已知目录偏好键按host的data_root_aliases仅匹配目录边界迁移；原始数据不改写。目录前端回归通过，17项后端相关测试通过。
- 全后端572 passed/15 skipped，指定新RL合同根后4个原生合同通过。新目录历史标签、视频和首尾图可读；config/identity/host/deployment/storage接口正常，host查询内置系统盘 /。
- 主Git删除89个跟踪硬件重复文件；现场78个已部署文件核对新旧SHA后删除，网页脚本转发control。
- 本机配置备份、API、加载释放、媒体读取与删除回执：A6000 outputs/verification/20260928-cutover/cobot/；Cobot同路径去掉末尾cobot/。

没有归位、真实Episode或示教。旧平台/RLT全量历史归档仍在执行，原件保留。被动硬件launch已停止；现场使用按COMMAND_LINE和RL RUNBOOK重新启动。guide只更新摘要，不提交Git。

## 2026-09-28：旧平台、旧数据与输出日志清理

- 五个旧数据根 rlt/evaluations/cobot-platform/record/test 在11,123文件SHA、2链接以及最终无活动引用复核后删除；新历史列表、episode171视频和六张首尾图可读。删除回执在rl-platform/outputs/migrations/20260927-rlt/data-cleanup-receipt.json。
- 旧 task3 runtime/cobot-console-jobs-v1 的859文件/141,706,560字节归档，850日志迁入本项目runtime/console-jobs，6个当前任务登记改用新日志路径。历史命令、PID和provenance原样保存；A6000逐文件核验原件后删除旧目录。证据 outputs/migrations/20260928-legacy-task-output/。
- 旧cobot-platform已完整归档到A6000 vla-platform，9,629条目验证通过；最终无变化/活动引用复核后，先改名使旧路径不可用，再冷启动正式8015，核验模型目录、历史、受管理相机启停和home --help，最后删除旧目录。模型/Session未启动，未归位。删除回执 outputs/migrations/20260928-platform-retirement/cobot/retirement.json（现场去掉cobot/）。
- 本次冷启动网页PID153484，预览8018已停止；PID仅记录本次实例，不是永久服务标识。相机任务已停止，无残留；ROS master仍运行。删除host中的legacy_platform_root，不再登记已退休硬件根。
- 旧RLT历史归档仍在传输、校验，原RLT目录尚未删除；Piper/ROS/Astra/aloha和两个π0.5共享部署目录另批处理。

## 2026-09-28：旧 RLT 清理后的最终验收

旧RLT完整SHA归档与环境异机备份通过后，隔离旧RLT路径并重启正式网页，PID153484→222607。最新在线模型经共享入口ready/disarmed/paused，Session未开始；learner5000/actor2500/Replay2567，随后释放为offline。正式权重与Replay未改变，旧RLT及cobot-realworld-rl别名现已删除。

网页6个共享模型路径可用；9类只读API、37个引用资源、episode171标签/视频和首尾六图均HTTP200。系统盘/剩余约77.6GiB，无GPU计算进程。最终证据在相邻rl-platform/outputs/migrations/20260928-retirement/cobot/final-runtime.json，以及同目录加载/释放、learner和清理回执。

后续网页任务已启动机械臂PID148006及相机PID158179，均是新control路径；设备读数为5臂、5CAN、3相机可用。早先相机停止回执对应PID130873，不能据旧回执断言现在硬件全停；保留这些后续任务，没有为收尾中断它们。没有开展真实Episode或浏览器目视动画验收。

两个π0.5共享部署保留原登记位置。删除旧cobot-platform后，两者COBOT_DEPLOY_DRY_RUN入口返回成功，证明路径/命令预检可用，不等同于重新验收π0.5真实推理。证据outputs/verification/20260928-cutover/cobot/pi05-after-platform-retirement-dry-run.json。完整命令及HTTP故障处理继续见COMMAND_LINE.md、WEB_RECOVERY.md；RLT在线手册在相邻rl-platform/docs/RUNBOOK.md。


## 2026-09-28：Getea1 统一存储迁移（进行中）

Cobot 数据与模型统一在 /media/agilex/Getea1/jiaan/data/ 和 /media/agilex/Getea1/jiaan/model/。数据按场景分、模型按项目/模型分；本轮不新增 A6000 权重备份。代码、安装环境、运行日志与 PID 留在 /home/agilex/jiaan/project/<项目>/。完整路径与批次状态见相邻 cobot-web/docs/STORAGE.md。

已在 A6000 接入新存储配置及旧路径映射；逐文件复制/校验正在进行，正式网页已在空闲状态正常停止，机械臂/ROS 进程保留。本段不代表旧源目录已经删除。位姿、回放、示范、RLT rollout/Replay、评测和部署权重按 STORAGE.md 归类。最终运行验证及删除回执待本批完成后追加。

## 2026-09-28 20:00：迁移中遇到 Getea1 USB 掉线

已完成主体 12,059 条目、351,844,176,546 字节及 6 个恢复验证资产、117,047,594 字节的迁移、SHA 校验、运行验收和对应源文件清理。Warmup 与在线模型在新路径加载/释放通过；在线状态 5000/2500/2567，正式权重和 Replay 的 SHA 不变，未启动 Episode 或真机运动。历史读取、92 条有效评测和媒体通过；主副本清理后再次读通。系统盘当时剩余约 404 GiB。

剩余 FluxVLA 环境复制到 libcublasLt.so.12 时出现 I/O error。内核在 19:59:53 将 sda 下线，随后 USB 设备枚举失败；20:00 检查已无 Getea1 块设备和挂载。不能把它归因于单个 Python 包或仅网页错误，也不能仅凭这些日志判定是线缆、供电、硬盘盒或盘本体。

所有迁移进程已退出；正式网页 PID 366090 正常停止，无 GPU 模型进程，临时 ROS master 已停止。本轮未做运动。尚未验收的 FluxVLA 旧目录、暂存副本未清理，**Getea1/jiaan 仅保留 data/model 的目标尚未完成**。已验证结果仅代表掉线前状态，恢复连接后仍须核对文件系统并按迁移收据重新校验新资产，不能直接继续删除或开始在线训练。

证据：相邻 rl-platform/outputs/migrations/20260928-getea-storage/cobot/，现场同目录不带 cobot/。包括 retirement.json、validation/retirement.json、cutover-verification.json、extras/copy-status.json、disk-disconnect.json 和 disk-disconnect-kernel.log。源码和证据位于系统盘/A6000，本轮没有新增 A6000 数据/权重备份。

## 2026-09-28 20:56：Getea1 存储迁移完成

本批已完成复制、哈希与运行验收、切换和对应旧文件清理。Getea1/jiaan 只保留 data、model；旧系统盘数据/模型目录移除。数据按场景/用途/方法归类，位姿与动作回放归 data/motion；模型按项目/模型/场景/版本归类。代码/环境/日志/PID 留在 /home/agilex/jiaan/project/<项目>。

USB 掉线重连后已完成已迁移资产的全量收据复核；尚不能据此认定硬件链路根因已消除。RLT 新路径暂停加载、在线状态恢复与历史媒体通过；FluxVLA 固定版本离线 baseline/prefix-RTC 通过；π0.5 两入口只做 dry-run。本批未启动真实 Episode 或机器人动作。

完整路径、占用、各项验证边界及回执见实际 cobot-web/docs/STORAGE.md。证据位于 rl-platform/outputs/migrations/20260928-getea-storage/cobot/（Cobot 去掉末尾 cobot/）。同批源码与项目记录已按各自仓库发布；guide Git 保持由其他会话管理。

## 2026-09-28：按钮命令与后台进程说明

按用户要求，输出栏的本次执行／常用命令改为实际终端配方：先 cd 对应项目，再执行相对脚本；CAN 配置使用 control/scripts/can_up.sh，重置显示 sudo ip link 的五臂参数，不再让人手工读取密码并通过管道交给 can_web.sh。命令后以注释说明真正实现；“实际启动与进程”保留原始入口、当前进程及 PID。配置入口的差异明确注明：网页失败会重置再重试一次，直接 can_up 只执行一次。

docs/COMMAND_LINE.md 补充设备／模型／采集／评测的按钮、CLI 和真正实现对照，区分后台会话、手动前台终端和 API 请求。暂停／节点等操作不单开进程，tail 的 Ctrl+C 不停止后台任务；CLI 终结不会继承浏览器自动归位选项。

本批仅改网页命令展示和文档，web/control 职责下沉仍按用户要求延期。验证覆盖命令 shell 语法、参数引用、原始命令保留、中英文及前端切换，不执行 CAN、节点启动、归位或模型加载。A6000 相关后端 45 项测试、前端全套 35 项测试通过，全部常用命令通过 bash -n（仅语法解析）。源码提交 0519849 已 push 并同步 Cobot，212 个运行文件 SHA 校验通过。确认模型 offline、采集 idle 后仅重启正式网页；首页、新版本静态文件和输出 API 均通过，现场 59 条命令不含手工密码管道。CAN/相机/home 的历史任务默认显示直接终端配方，同时保留真实登记命令。验证回执：A6000 outputs/deployments/terminal-recipes-verification.json，Cobot runtime/migrations/20260928-terminal-recipes.json。

## 2026-09-28：可配置启动入口和模型登记

新增 site_options 后端与设置面板：服务器文件浏览、机械臂／相机 .sh 或 ROS 1 .launch 路径、环境／工作目录／argv，保留内置恢复入口。独立 site_device 监督进程保持任务身份，配置在运行期间拒绝更改，输出命令跟随配置。模型支持复用同契约适配器登记路径和保存默认选择；不会自动启动或推理。

Getea1 权重按实际格式发现，未接入网页暂停／HIL 协议的历史模型明确显示需适配；不把 safetensors 或训练 checkpoint 冒充当前 RLT／π0.5。基于实际 training_manifest 的 action_dim=7、chunk_len=10、z_dim=2048 及当前 v3 历史记录，补充 experts120_20k_20260925 冻结对比入口（step 20000、actor 10000）；保留当前默认值。本批没有修改模型权重。

主代码在 A6000；现场只同步 web 运行文件，并为本机配置补 model_root，保留其他本地项。配置/适配边界见 COMMAND_LINE.md 新增章节。相关后端 111 passed、1 skipped（原有跳过项），前端 36 passed；额外终端配方与监督进程退出检查通过。未运行 CAN、launch、真实推理或训练。源码 9a12d0a 已 push 并同步，216 个运行文件 SHA 一致；本机配置仅追加 model_root，原配置保存在 runtime/migrations/20260928-site-config-before.json。确认模型 offline／采集 idle 后仅重启网页，实际 API 列出 25 项资产与 7 个可加载入口；DM0.5 仅元数据已独立标注，当前默认 plug_v3-online-latest 和内置硬件入口未变。模型根、control 脚本目录浏览和新静态资源通过。回执：outputs/deployments/site-options-verification.json；现场对应 runtime/migrations/20260928-site-options-verification.json。

## 2026-09-28：复用历史部署脚本与模型身份标注

来源：cobot_rlt 会话；用户要求复用以前直接 .sh 部署的成果，并在权重路径后标明模型、场景、训练步数与状态。

- 历史 Galaxea G0.5（baseline/DAgger）、Xiaomi XR0/XR1/XR1 DAgger、LingBot V2、DM0.5、π0.5 lift_book/put_two_fruits 的脚本、客户端、配置、测试和 XR1 URDF 纳入 vla-platform/integrations/cobot；主代码在 A6000，Cobot 同步运行副本。来源和文件哈希在 configs/assets/legacy_deployment_entries.json。
- configs/cobot_models.json 登记 12 个历史版本/入口；现有 FluxVLA、G05 两版本、XR1 原版复用旧服务、动作映射和 RTC 参数，通过原暂停/arm服务连接网页。加载不 arm、不恢复推理；网页 Start/Resume 才调用原 arm + resume；暂停锁防止示教释放覆盖网页手动暂停。
- XR0、LingBot 两版本、另两场景的 π0.5 三版本保留原终端入口，不将会直接运动的原 live 脚本冒充“仅加载”。列表区分终端可用/网页待接入、缺少文件、基础模型依赖。DM0.5 与 XR1 DAgger 本机缺权重，历史权重仍在 A6000，不擅自复制。
- 旧共享 Python/ROS 与 G05/Xiaomi/LingBot 的已安装 runtime 仍作为显式依赖保留，没有宣称环境整体迁移或跨机器从零重建完成。原部署目录暂保留，未达到完整现场验收的部分不清理。
- 模型选择统一显示真实路径、模型、场景、步数与状态；RLT 分开标 Stage1 checkpoint / learner steps / actor version，π0.5 DAgger 保留 2000+3000 来源。备份目录名字不推断训练步数，没有证据时标待核验。

验证：A6000 web 相关后端 39 passed/1 skipped；前端 37 passed。新网页手动暂停锁 3 passed，原 G05 generation/资产 preflight 6 passed，原 XR1 HIL/旧动作过期处理 26 passed。Cobot 四个 managed 启动计划 --check 通过；G05 两版本的 checkpoint 大小、归一化/配置/processor 哈希 preflight 通过。本批未加载新 GPU 模型、未启动真实 Episode、未发送机器人动作；真实模型加载和现场动作效果仍待逐个验收。

现场发布验收：web 82ced64、vla-platform fd7ee52 已 push 并同步，分别 217 / 280 个运行文件 SHA 一致。模型 offline、采集 idle 时只重启正式 8015，硬件节点未操作。实际目录为 26 项、11 个网页加载入口、6 个保留终端入口、2 个本机缺权重条目，以及历史 RLT/基础依赖；默认 plug_v3-online-latest 未改变。只读回执：cobot-web/outputs/deployments/20260928-legacy-model-entries.json；Cobot 为 cobot-web/runtime/migrations/20260928-legacy-model-entries.json。此数目表示入口与文件预检，新增 4 个网页入口尚未逐个加载 GPU 或验收现场动作。


## 2026-09-29：刷新默认测试目录与最近目录

来源：cobot_rlt 会话，用户要求 datasets/evaluations 各有 test，网页每次刷新默认选择，同时可选最近使用目录。

Cobot 已建立 /media/agilex/Getea1/jiaan/data/datasets/test 和 /media/agilex/Getea1/jiaan/data/evaluations/test。普通／模型辅助／RLT 采集刷新使用前者，部署评测使用后者；正式场景目录、权重、Replay 和算法配置未迁移或改写。刷新选择只初始化一次，之后的状态轮询不覆盖手动选择。采集和部署页均有最近使用目录下拉框，选择后调用原有目录检查／应用接口；目录历史保留，浏览和手工输入继续可用。

服务端对自动刷新选择增加活动任务检查。普通采集状态提供实际 data_root；录制／暂停／收尾或评测活动期间保留本轮目录，模型操作未完成时延后默认初始化。用户主动选择仍沿用原契约，RLT 活动轮次期间的新选择只用于下一轮。只读预览不发送自动目录写请求。test 只是存放位置，不改变在线模型的学习行为；正式在线更新前需要明确选择正式场景目录。

验证与发布：
- A6000 后端相关测试 50 passed、1 skipped（原有跳过项）；前端全套 39 passed，最后目录初始化调整对应的 3 项再次通过。
- 源码 b785ed902bd0c95f1a72082ecdeb608a89e016a7 已 push 并核验远端一致，Cobot 217 个运行文件 SHA 一致。
- 确认模型 offline、采集 idle、无活动评测后，仅重启正式 8015。实际目录准备、RLT 默认选择、部署默认选择、旧路径历史保留及 6 个页面／资源请求通过；测试目录内没有采集 Episode。
- 相机任务 PID 524014 保持运行，机械臂／模型未启动，没有运动、真实采集或训练更新。本批 DOM 回归不等于现场全流程或浏览器动画验收。
- A6000 证据：outputs/verification/20260929-test-directories/；现场回执：runtime/migrations/20260929-test-directories.json。

用户安排下一次现场整体验收 Cobot 使用、普通／模型辅助采集、部署评测及终端恢复，再进行 RLT 在线更新；这些现场验收尚未执行。后续长任务收到后按具体条目继续跟踪，不将本批目录功能验收视为整体任务完成。guide Git 仍由框架维护对话处理。

## 2026-09-29：硬件规则共用（第一批，源码验收）

CAN/ROS 探测、健康判定和设备任务管理移到 cobot-control/src/cobot_control；网页保留 HTTP、翻译、RLT/console 扩展和输出展示。新增 control/scripts/control.py，无需网页运行。67 个网页兼容测试、3 个独立进程用例通过。未改变动作/ROS 参数，未执行硬件运动；尚未同步现场。来源：cobot_rlt 本轮跨项目整理。

## 2026-09-29：目录历史与输出布局

普通采集与 rollout 共用目录索引，按每条记录的 history_format 选择图片/视频接口；不以模型选择决定读取格式，混合目录去重并保留普通采集节点。数据区只显示目录记录数，采集状态集中到采集控制。输出任务列表支持手动折叠和拖拽宽度，缩窄面板不自动隐藏任务。相机缺标定/IR 提示合并解释，真实错误保留，原始日志仍可查看。

A6000 后端全套首次 596 passed/15 skipped，新增日志正则用例暴露转义问题后修复；相关 14 项复测通过。Node 全套首次 38/39，通过更新已改名的采集模式测试替身后，相关 3 项通过。现场只读目录和浏览器布局验收随后记录；本批未启动 Episode 或机器人运动。

## 2026-09-29：职责边界与部署材料

按实际源码、只读现场状态整理，代码先在A6000开发。结构、安装、依赖来源及验证界限见docs/DEPLOYMENT.md；跨项目关系见cobot-web/docs/ARCHITECTURE.md。数据/模型实体未迁移或删除；公共厂商工作区未删除、硬件未重启。guide只写事实、不提交其Git。现场切换与版本见后续发布回执。

## 2026-09-29：正式切换、清理及交付验收

统一模型/任务入口、采集领域迁移和网页修复首次发布63e5839；五项目运行文件逐一SHA核对后切换正式8015。现场模型offline、采集idle时仅重启网页，硬件未重启，相机PID524014保持。网页关闭时control独立状态检查通过。

后端607 passed/6 skipped，DOM39 passed；后续camera路径/外部cwd修正相关44项通过。模型未加载时，指定warmup目录返回111条记录，JPEG23924字节。目录历史不再依赖选择模型；状态去掉重复计数；输出任务列表手动折叠/调宽；日志清理ANSI、归并重复提示并保留原始错误/日志。

清理仅限SHA确认的33个旧采集领域文件和6个已由control接管的旧设备任务登记。现场回执runtime/migrations/20260929-domain-cutover.json及20260929-cleanup.json，A6000证据outputs/verification/20260929-boundaries/site-final.json。没有删除外部共享工作区或数据/模型。

本批无可连接浏览器，DOM与HTTP验证不能替代截图/拖拽视觉验收；真实采集、HIL、模型动作、在线更新和成功率仍待现场。

主代码位于 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web；现场副本 /home/agilex/jiaan/project/cobot-web。后续收尾版本以Git main和现场.release.json为准。guide仅更新事实摘要，不提交其Git。

## 2026-09-29：采集与部署共用场景/模型选择

来源：用户反馈模型路径列表太长、场景混杂，以及两页不可用项行为不同。

新增 model_picker.js / model_picker.css，采集与部署共用左右排列的场景、模型选择框。场景过滤模型，模型反向定位场景；空闲时两页同步并记住选择。选项使用简短身份与训练计数，完整权重路径在下方显示一次；两页使用相同的available/capabilities.load规则，不可用项统一禁用。活动/加载时锁定选择。仅筛选或选择不会加载模型或启动Session。

A6000前端42项通过，相关后端28项通过；修正旧模型元数据测试夹具，使其包含已迁移到VLA项目的真实登记读取器。现场两个API的26个条目和可用性一致，11个可加载；用实际目录验证5类场景的过滤与禁用结果。没有更改模型登记、权重、算法或硬件规则。

本批仅发布静态页面和文档，原网页进程无需重启；同步后逐文件SHA和HTTP资源检查另留回执于 outputs/verification/20260929-scene-model/，现场 runtime/migrations/20260929-scene-model.json。刷新网页加载新资源；真实模型加载与机械臂任务未执行。

## 2026-09-29 页面切换对齐

- 操作台、数据采集、训练、部署统一从内容区左上角开始，内容区上侧和左侧均留 14 px。消除空 page-heading 的 11 px 残余间距；训练状态移入“训练记录”标题栏，避免独立提示行使训练面板下移。
- 切换类别时只重置中央内容区的横纵滚动；重复点击当前类别保留滚动位置。相机与输出停靠位置、用户布局和业务状态保持原逻辑。
- 修改：app/backend/segmented_frontend/{index.html,dock_ui.css,app.js}；HTML 更新资源版本号，浏览器刷新即可获取新样式和脚本，不需重启服务。
- 验证：Node 前端回归 42 项通过；A6000 Chromium 使用本地待发布静态资源和现场只读 GET，在 1600×1000、1024×768、760×900，导航展开/收起设置下检查四页，共 24 个组合，首个面板相对中央区域 x/y 均为 14 px。切换前设置滚动偏移，切换后均归零。布局证据位于 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/outputs/ui-alignment-20260929/geometry.json，训练页截图已人工检查。
- 浏览器验证禁止 POST 等写请求，并跳过图像流；这是布局验收，不代表模型、相机或真机运动验收。同步只替换已提交静态资源，不重启网页、ROS、硬件或模型任务。

## 2026-09-29 采集与部署四框布局、编辑排序

- 两页共用 session_workspace.css：默认左上保存位置、右上模型、左下数据/评估记录、右下采集/部署控制。各行外框等高、两列等宽，间隔 16 px；默认上排 360 px，模型详情与目录内容过长时在框内滚动。按中央内容区实际宽度收为单列，不依赖整窗宽度。
- 采集模型卡归入采集网格；部署保存位置从控制卡拆出。原有字段 ID、事件、模型加载、目录选择、快捷键和终止/保存逻辑不变。
- 设置 → 编辑布局后，四框都可拖动换位；采集和部署分别保存到现有布局偏好的 collection-workspace / deployment-workspace，刷新恢复。语义卡片 ID 取代数量/位置编号；旧三框布局顺序不套用到新四框。单列拖放按纵向中点排序。
- A6000 主代码 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web；Cobot /home/agilex/jiaan/project/cobot-web。变更仅静态前端，源码同步不重启任何业务服务。
- 验证：Node 前端回归 42 项全部通过。Chromium 在 1600、1200、760 px 窗宽验证两页四框位置、窄屏顺序；两列时两页各框 x/y/width 完全对应，上排均 360 px；真实鼠标拖放和刷新后排列恢复在两页通过。已查看两页截图。证据：outputs/four-panel-layout-20260929/（A6000 项目内）。浏览器验证拦截写请求及图像流，仅验证交互布局，不启动模型或运动。

## 2026-09-29：示教状态、CAN TX故障与中臂首次归位

本批硬件修正归同级 cobot-control（robot/mid_home.py、src/cobot_control/device_control.py、device_health.py、can_health.py）；网页只补充健康码翻译。真实示教状态 mode=2/teach=1 不再误判；同步正常显示蓝色，失败会标明按钮/接管/过期话题/跟踪偏差/配对故障。前臂详情保留自身CAN反馈，避免错误复制后臂mode。新增 CAN 发送队列堵塞的中英文原因与恢复提示；不因接收正常而掩盖发送故障。

现场只读确认左右前臂TX堵塞、内核echo异常，未自行执行恢复或归位；详见 control/docs/MIGRATION.md 及 outputs/diagnostics/teach-and-mid-20260929。首轮384项、收尾190项离线回归通过。发布、网页重载与现场动作验证分别记录，不将静态测试作为真机成功。

### 现场发布与复测

control fba6fe5 / web 55c572f 已推送并同步146/197文件；空闲时重载正式8015，原臂和相机launch未重启。用户授权后前双臂CAN堵塞已通过原Recover解除，0.01rad小幅往返实测通过。第一次人工示教复测发现latched模式/故障被误判为过期，已改为按真实状态变化协议判断并补24项健康回归；不得把latched话题套用心跳超时。第二次真实示教复测及中臂首次冷上电home结果另记。

## 2026-09-29：示教蓝黄闪烁

用户确认静止示教蓝色、前臂本体示教黄色正确；新增需求为消除正常接管及移动中的蓝黄闪烁。平滑规则统一归cobot-control的只读健康观察器；网页只补蓝色“示教接管中”和黄色“同步正在恢复确认”的中英文说明。瞬时诊断字段保留，只有已确认异常才覆盖为故障提示，不把普通运动中的短时偏差立即标成故障。

82项相关回归通过；硬件故障/前臂本体示教仍立即黄色。没有调整控制语义或机械臂参数。本批不启动运动；A6000提交后同步Cobot，正式8015空闲时重载。

本批发布：control 8aea6f2 / web 53ceff4 已push并核验远端，同步146/197文件SHA一致；模型offline、采集idle时仅重载正式8015，机械臂PID1318293、相机PID1317979不变。实际HTTP响应与五路TX健康正常。现场回执：control/runtime/diagnostics/teach-display-20260929/release.json；A6000对应control/outputs/diagnostics/teach-display-20260929/release.json。本批不主动运动，移动期间颜色的人工复测仍待用户；不得将离线序列测试写成现场防闪烁已验收。

## 2026-09-29：运动与退出示教降敏

用户确认进入示教已不闪黄，本批只补充“正在退出示教 / Leaving teaching mode”及退出超时的双语展示。实际规则仍由cobot-control维护：普通跟随误差2秒确认、软信号1秒确认，已确认示教退出最多1.5秒绿色过渡；明确故障和前臂独立示教仍即时黄色。完整参数和边界见同级cobot-control/docs/MIGRATION.md最新节。

A6000设备/任务/健康回归100项通过，包含消息顺序、逐步失能、退出超时、重新进入、两侧独立和真实故障优先级；未改变网页按钮、控制参数、HIL或机械臂行为。发布同步及空闲网页重载结果另记，不把离线测试当作用户运动复测。


本批发布：control ace3b1f / web fdd6cdc 已push并核验远端，Cobot 146/197文件SHA一致。模型offline、无活动采集/归位/恢复/CAN任务时仅重载8015；臂PID1318293和相机PID1317979保持不变，正式API七个设备健康、五路TX队列为空。未请求运动。回执：A6000 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-control/outputs/diagnostics/teach-release-20260929/release.json；Cobot /home/agilex/jiaan/project/cobot-control/runtime/diagnostics/teach-release-20260929/release.json。用户已确认上一版进入示教不闪黄；本版运动/退出的实际连续操作颜色仍待用户观察，不能把空闲API检查作为动作验收。


## 2026-09-29：三段模型选择、共用模板及选臂位姿

采集/部署共用 Scene→Model→Steps；场景与模型选项英文，RLT Stage1/Learner/Actor 分列，不可用项一致禁用。两页保存位置/模型使用相同几何布局和控件，部署无启用模型勾选；详情默认折叠、展开增高，权重路径去彩色底，编辑布局拖动保留。部署增加自选归位臂。设备详情支持已有/新位姿名、勾选记录臂；底部独立 Recover，单夹爪开合/恢复分别分发到指定侧。

44项Node测试通过；相关Python回归404项通过。Playwright用本地静态文件和现场只读GET、拦截全部非GET，在1800/1200/760宽度验证两页上排坐标/选择框一致、权重背景透明、展开无内部滚动、无JS异常。证据在 outputs/model-pose-layout-20260929/（不入Git）；实时流屏蔽，截图不用于相机验收。

发布前已有在线RLT加载进程，并观察到 session=fault / task5_start_failed: recorder_not_ready；未由只读预览触发。本批不声称修复此采集错误，不自动释放模型/结束Session或启动推理。发布前后核验任务PID和Session，真机位姿/夹爪动作仍待现场验收。


本批正式发布：control 15c1917 / web fd8d182 已 push 并核验远端，Cobot 146/197 文件 SHA 一致。无活动录制/归位/恢复时，仅重载8015；臂 PID1318293、相机 PID1317979、在线RLT PID1436537 及进程 start_ticks 均保持，RLT Session UUID 保留。现场位姿 YAML SHA 未改变。正式HTTP提供的新静态资产SHA与A6000一致，设备API已提供单夹爪reinit入口。

网页 recorder 从已停止记录的 committed/stopped 状态恢复为 idle，generation 重置为0；RLT Session 仍为原有 fault/recorder_not_ready，不声称重启修复了此故障。未发机械臂/CAN/夹爪动作，未重启驱动或模型。回执：A6000 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/outputs/model-pose-layout-20260929/release.json；Cobot /home/agilex/jiaan/project/cobot-web/runtime/verification/model-pose-layout-20260929/release.json。

## 2026-09-29：RLT 录制故障保留模型恢复

用户要求现场修复且不释放权重。发布前只读检查：模型PID1436537存活、policy_paused=true，Session fault=task5_start_failed/recorder_not_ready、无未决Episode，recorder idle、ROS readiness正常。使用已有的带episode/generation身份的Session stop及recorder recovery，恢复stopped/模型ready；PID/start_ticks不变，没有推理、归位或学习动作。

源码修正：web增加录制预检/恢复页面入口；fault允许结束Session；新恢复选项只处理策略暂停、无未决数据且工作线程已退出的状态，使用模型操作锁，保留模型，拒绝活动录制/待提交数据。HTTP错误保留具体ROS、流、目录和空间原因；短时相机/示教流间隙只在开writer前等最多1秒，不重试有副作用请求，不放宽有效期阈值。

dagger原有snapshot调用先取clock再抢锁，可包含时间更新的callback而产生负age。离线确定复现并修复：实时预检传入clock，在缓存锁内取样；显式历史时间与真正未来时间戳仍按原语义。该机制能导致偶发未就绪，但旧HTTP 503丢失详细原因，不能认定全部历史失败都由它造成。数据格式、HIL/mask、模型算法、Replay及控制参数不改。

254项Python回归通过、1项既有跳过；45项Node通过。证据：outputs/rlt-recorder-recovery-20260929/。发布、模型保持暂停的真实短录制及清理回执另记，不以预检代替写入验证。


现场发布与写入验收：dagger dffc3f1 / web 8dd6983 已push并核验远端，同步45/198文件SHA一致；空闲时只重载8015，模型PID1436537及start_ticks9136435、机械臂PID1318293、相机PID1317979保持。正式预检/恢复API均成功，新静态资源SHA与A6000一致。

Session始终stopped/policy_paused，模型保持加载，在独立 datasets/test/recorder_recovery_check_<UUID> 下连续3次直接录制，每次12帧HDF5成功提交；逐轮通过UUID绑定的discard接口删除，无剩余数据/标签/目录。未向Session发start/resume、未归位、未新增Replay，Session generation10及chunk_count18不变。最终模型ready、recorder idle、Session stopped，可手动开始Session；在线Learner仍5090、Actor2545。该验收证明当前录制器可连续写入，不冒充完整推理/HIL轮次或长期稳定性测试。

回执：A6000 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/outputs/rlt-recorder-recovery-20260929/{release.json,passive-recording.json,final.json}；Cobot /home/agilex/jiaan/project/cobot-web/runtime/verification/rlt-recorder-recovery-20260929/。详细恢复方式见web/docs/WEB_RECOVERY.md。

## 2026-09-29：π0.5 Recover 后暂停锁误判

来源：用户成功部署 in_the_pot DAgger 后点击开始立即暂停，并说明刚执行双后臂 Recover。只读核对：GPU 服务仍就绪；后臂 mode=0、0/6 enabled、无故障，协调器 policy、三处 fault 为空。Recover 通过 task2_recover_cli 调用 set_paused(true)，旧 web wrapper 将所有非网页调用标成 hil_active，导致继续请求仍返回 paused；服务 success 被错误当作已恢复。不是加载失败或后臂失能故障。

本批只改 web 的 π0.5 暂停适配、ROS bridge 与回归，不改控制/模型/RTC/动作参数。新 wrapper 将 Recover/其他保护请求归入手动暂停，只识别实际两个协调器节点作为 HIL，增加来源/版本诊断；真正接管仍阻止继续，保护暂停不能被普通调用解除。bridge 拒绝把旧客户端的 paused 回复视作已继续。

旧模型进程不热替换：新增 repair-pause，在模型操作锁、PID身份与ROS状态检查下保持 manual_pause=true，只清除旧外部锁；不自动恢复推理、不清空HIL计数/评测历史、不释放权重。下一次正常加载才启用新版 wrapper，当前旧进程的显式开始/继续也执行有界兼容协调，不要求重新加载或每次手动修复。CLI与网页暂停命令串行，修复阶段始终保持手动暂停，最终只由本次显式开始/继续请求解除。使用与限制见 docs/WEB_RECOVERY.md。测试、发布及现场恢复结果随后补记。

本批回归63项通过、1项既有跳过，覆盖Recover→Continue、真实HIL阻止恢复、手动暂停不被按钮释放覆盖、未知来源不能解锁、旧版有效状态检查、无运动修复、显式继续先协调再解除与故障拒绝。源码3d05c63首次push并同步198文件后，在现场执行repair-pause成功，维持paused/manual_pause=true；未启动机器人动作。

现场后续只读确认：用户自行于15:43开始eval-20260929T154302-897142dd，网页phase=running、gate paused=false/manual_pause=false，无操作错误、intervened=false。模型supervisor2019008、GPU服务2019058、RTC客户端2025877及两硬件supervisor PID/start_ticks保持。此结果证明残留锁已解除、用户开始请求通过；不冒充动作质量/整轮成功率验收。

后续增强将旧客户端兼容协调放入显式开始/继续bridge，已完成离线回归；不在加载/状态轮询/Recover时自动恢复推理。同步脚本和文档无需重启网页/硬件/模型，新wrapper将在下次正常模型加载启用。现场回执：Cobot /home/agilex/jiaan/project/cobot-web/runtime/verification/pi05-recover-pause-20260929/repair.json；A6000对应outputs/pi05-recover-pause-20260929/。最终版本与逐文件SHA以.release.json和Git main为准。

补充边界：Recover来自已登记的恢复节点时，在保持manual_pause=true下退休旧HIL锁，涵盖恢复前曾接管/协调器故障的情况；所有π0.5显式开始/继续均检查实际policy/空fault，未恢复完成或仍示教时不解除暂停。旧版兼容清理只在显式请求中执行。最终相关回归65通过/1既有跳过；新增自动继续与故障拒绝由隔离测试覆盖，未为验证调用真实开始或Recover。

## 2026-09-29：混用目录的历史未标注记录阻塞 RLT

现场 RLT 开始失败的明确原因是 Task5 latest episode labels are incomplete：选择的 demonstrations/legacy_test 包含其他模型的已完成、未标注人工示范。旧 RLT orphan 恢复只接受同模型/轮次身份，不能处理该示范；旧恢复按钮只预检输入/写入，没有检查目录历史，所以错误重复。

统一录制的 flat 目录允许已有 finalized 未标注记录，保留其标签完整性事实，不自动写 aborted/success/failure，不向 Replay 加数据。legacy 独立接口仍默认要求标签；真正 incomplete、损坏或身份无效的末条记录仍拦截。采集领域提供显式 require_labels 参数；web 挂载接口选择策略，不重复维护数据判断。

网页“检查录制 / 恢复录制（保留模型）”使用同一个目录检查入口，显示检查路径、具体完整性错误与处理建议，新故障清除旧通过提示。不开始推理、不卸载模型、不删除历史数据。64项Python相关回归通过，45项前端通过。现场切换及版本另记；不能将离线测试当作真实推理验收。

## 2026-09-29：区分训练、发布和实际推理版本

用户看到6915误以为已发布。只读核验learner_status为step6915/internal actor3457，而实际actor_snapshot.pkl头部为step6500/version3250，Session最近一次实际推理actor3250。旧model_catalog误用learner内部版本表示可加载权重。现只解析固定快照前512字节的整数头部，不执行pickle或导入模型，未知格式不推算版本；选项显示published步数，详情分别显示Learner trained step、内部Actor、Published learner step/Actor、Last inference Actor、Episode及未发布步数。Session版本来自实际推理返回，不以已发布推定已使用。

本次不改变500步发布周期、算法或当前进程。另将RLT首次加载提示依据本次实测改为4–6分钟估计。24项Python通过/1既有跳过；前端全套和新增版本区分验证另记。当前用户正在在线采集，源码同步后后端仍需空闲窗口重载；不宣称已生效。guide Git不提交。

发布记录：web 81114d1已push并核验origin/main，Cobot同步199运行文件SHA一致；24项Python通过/1既有跳过、46项Node通过。现场新代码只读快照确认6500/3250，Session episode14实际actor3250，模型PID2139119保留。两个修复批次均已同步源码；正式8015后端仍待用户结束连续采集后的重载窗口，本次未重启网页/模型/硬件。此前恢复故障目录对照：4条原示范保留，最新index16，旧规则label_blocked=true、新false，下一index17。


## 2026-09-29：正式后端切换已完成

用户反馈仍无published后，现场确认waiting_scene/policy_paused、无活动writer/操作，持模型操作锁仅重载8015。模型supervisor2139119、Stage12139241、机械臂1318293、相机1317979身份与Session UUID/generation119保持。正式API确认Learner6915/internal3457、published6500/3250、last inference3250；录制标签修复一并生效。未开始推理或释放模型。回执：Cobot /home/agilex/jiaan/project/cobot-web/runtime/verification/rlt-publication-20260929/release.json。guide Git未提交。

## 2026-09-29：训练页整理与新增诊断分析

用户要求新增核心分析板块、训练页保留有用信息并对齐。本批新增独立Analysis导航和只读/api/analysis/rlt；训练页保留进度/发布/配置/Critic与Actor核心曲线，移除混排重复诊断卡，历史Warmup图归入诊断页折叠档案。两页同一响应式卡片网格；浅深色沿用主题，静态和动态文案支持语言切换。

分析来自真实JSONL与只读Replay：按实际Actor和phase/确定性模式拆分自主/接管成功；显示Wilson区间、真实抽样比例、Q和加权目标；按训练step回退拆段，未更新Actor的0不作有效loss。Replay特征为7维proprio和7维平均动作差，PCA显示、14维k-means、固定seed42。3917条有效transition，前两轴解释方差31.50%；不是视觉语义聚类或成功率因果结论。

验证：rl-platform4项统计/只读快照测试；web24项Python、47项Node通过。Playwright只读GET预览，阻断全部写操作与相机流，在1800/1200/760宽度核对两页相同左/上边界、无横向溢出、0 JS异常；检查聚类着色/点击和英文动态文案。证据outputs/rlt-analysis-20260929/，不是相机画面或运动验收。

保留并接回了现场已有的5个web文件改动：部署与Learner区分、NVMe加载时间文案等。rl-platform现场NVMe/probe并行修改未覆盖，本批只部署新增分析模块/脚本/说明；不修改训练、Replay、模型或硬件控制。正式服务切换与SHA记录随后补记；guide Git不提交。

正式发布：web e775308 已push并核验origin/main，同步202运行文件SHA一致；rl-platform 88b3e1c源码已push，仅3个新增分析文件按SHA同步，后续ce8dca9修正文档换行。现场模型offline、capture idle、无writer，持.model-operation.lock仅重载8015；9个硬件相关进程PID/start_ticks保持，未请求模型加载或运动。

正式GET /api/analysis/rlt首读0.20秒：Learner11750、published step11500/Actor5750、Replay3917、179条有效标注日志；展示2400个确定性抽样点。历史run=0/1及最新段接口通过；5个HTTP静态文件SHA与A6000一致。当前日志心跳已停止，界面明确标历史快照，不宣称训练正在运行。最终项目记录提交与源代码SHA由.release.json记录。

回执：A6000 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/outputs/rlt-analysis-20260929/release.json；Cobot /home/agilex/jiaan/project/cobot-web/runtime/verification/rlt-analysis-20260929/release.json。聚类快照：Cobot /home/agilex/jiaan/project/rl-platform/outputs/rlt/plug_v3_yyshadow/analysis/replay_projection.json。生成命令已纳入命令行手册。

## 2026-09-29: Replay audits, directory defaults and immediate device feedback

Analysis now includes Replay/actual-batch composition, retained version audits, success sampling
experiments, group gradients/input ablations and recorded-image sensitivity.
Attention is not claimed; retrospective versions are explicitly not independent validation.
Sampling is implemented in rl-platform, not duplicated in web.
configs/model_directories.json and optional model-owned data_directories provide per-model collection/
evaluation defaults. Explicit user selection applies them; polling preserves manual overrides and active
sessions cannot switch. Weights/data are not moved. Device clicks show pending/accepted state immediately;
CAN fault/drained events are visible. Same control semantics and confirmation remain.
A6000: 53 selected Python tests, 50 Node tests passed. Read-only browser QA at1800/1200/760
checks alignment, no horizontal overflow, no JS errors; offline screenshots in
outputs/rlt-analysis-20260929. Final live release verification is recorded below after synchronization.

### Formal8015 release verification

Code661abd9 pushed before selected-file sync (15 runtime/doc/config files). Modeloffline,
capture/recorderidle, no writer/active mode; held the model-operation lock and restarted only web.
Seven matched ROS/arm/camera process identities remained identical; no robot motion/CAN reset.
Real browser checks:14 version rows,12 experiment rows,6 frames/3 camera overlays,English rendering,
episode-appropriate dimensions,missing historical batch disclosure,0 JS errors.
API analysis0.229s; devices0.0064s (single observed requests, not a latency guarantee).
Warmup/Online/Pi05 metadata returns registered collection/evaluation defaults.
Evidence: outputs/rlt-analysis-20260929/{live-release,api-check,live-browser}.json;
Cobot runtime/verification/rlt-diagnosis-20260929. Training/Analysis alignment at1800/1200/760
has no horizontal overflow. No full robot collection/deployment acceptance is claimed.

## 2026-09-30: credit experiments / action comparison

Added a compact profile/episode action comparison panel with seven dimension-specific curves; 21-run results are expandable. Analysis reads rl-platform JSON only. Registered experimental runtimes resolve configuration, publication and live telemetry by project-owned profile; original entries stay available. Selected files developed/tested/pushed on A6000 then SHA256-synced to Cobot. Tests: 51 Node checks; 22 selected Python checks passed, one environment-dependent test skipped; responsive read-only preview at 1800/1200/760 px has no JS errors or workspace overflow. No robot inference initiated. Candidate process/algorithm evidence and remaining physical/RTC acceptance are in rl-platform/docs/EXPERIMENTS_20260930.md.

## 2026-09-30: RTC runtime failure and retained-model recovery

Investigated the actual19:50:31 log: MC30 async_rtc50 EnvDriver rejected a late
RTC result; final supervisor traceback was a consequence. Stage1 remained
PID233064; recorder reported stopped/complete/committed. No recovery, inference,
robot action, asset deletion or re-labeling was performed during diagnosis.

RLT owns stdlib root-cause classification and detailed actual/allowed timing.
Web collection/deployment share runtime-failure display and guarded explicit
/api/rlt/recover-runtime. It uses the original launcher, verifies no live owned
runtime/pending writer, retains same-checkpoint Stage1, rejects implicit weight
reload and starts no episode. Recording recovery stays a separate action.
A6000 source/tests/push precede selective Cobot sync. Release and live verification
are recorded in the following deployment receipt; guide Git is not submitted.

网页停止入口已识别“录制 stopped/complete/committed、RLT 运行进程及所属进程组已完全退出”的孤立占用标记；仅此情况下可正常 ui_down/up，保留完整录制与 Stage1。活 writer、未提交文件、状态过期、普通采集、评测或模型操作仍拒绝停止。检查实现位于 app/backend/cobot_console/ui_shutdown.py，停止脚本仍仅 SIGTERM 已登记的网页 PID，不杀模型/硬件。

Live release verified: rl-platform d4c798f and cobot-web4f5db63 pushed;18 selected
source/docs files match Cobot SHA256. Full web backend709 passed/6 skipped,56 DOM
checks passed,40 selected RL checks passed; additional orphan-shutdown/CLI32 passed.
Only the idle/completed orphan web service was restarted: Stage1 PID233064 and
14 hardware process identities/start_ticks unchanged. Formal8015 reports
rtc_delay_exceeded, retained Stage1 and recovery allowed, but whole runtime is
not ready. No recovery POST, Session start, inference or motion was performed.
The recovery mechanism is covered offline; end-to-end runtime restart and RTC
timing contention still need operator validation. Receipt on A6000:
projects/cobot-web/outputs/rtc-runtime-recovery-20260930/{release,live-verification}.json
under /data/LFT-W02_data/jiaan/jiaan; Cobot:
 /home/agilex/jiaan/project/cobot-web/runtime/verification/rtc-runtime-recovery-20260930/.

## 2026-09-30: pause-clock and pending-recording recovery follow-up

A6000 source changes: pause/HIL cancels obsolete publication deadlines; optional
async execution moves recorder HTTP off inference critical path with bounded
monitoring and generation guards. Web adds explicit finalization and scoped
runtime restart, preserving Stage1 and unlabeled recordings. Zero-option API
contract remains strict. No algorithm, ROS/action contract or asset layout change.
Operator procedure and code paths: cobot-web/docs/WEB_RECOVERY.md and
rl-platform/docs/RUNBOOK.md, latest sections. Offline regression, Git publication,
selective SHA sync and actual paused restart results follow in the release receipt.


### 2026-09-30: actual keep-model recovery acceptance

Source release: rl-platform3902fa8 / cobot-web6b84116, both pushed and13 selected
source/doc files SHA-verified on Cobot before switching. Web full backend715
passed/6 skipped, frontend58 passed, RL50 selected passed. Follow-up recorder
mode-conflict handling returns409 mode_not_selected/writer_busy instead of a
generic500; unknown start failures retain stable API codes and log their cause.
Its API/recovery regression suite73 passed.

At21:04 the failed runtime was rebuilt with Stage1 PID233064/start_ticks20025113
retained. At21:06 a real 13-frame in-progress test recording was handed to the
new general recovery endpoint; the owned runtime stopped, the writer committed48
frames, its lease cleared, and the new runtime355612 became ready/disarmed with
policy_paused=true and emitted_commands=0. No Session start/resume, homing or
robot motion was requested. Seven sampled hardware PID/start-tick identities
were unchanged. Replay remained3917 and Learner7000/Actor3500.

Retained unlabeled test HDF5 (no labels sidecar, no Replay insertion):
/media/agilex/Getea1/jiaan/data/datasets/test/runtime_recovery_20260930/episode_000001.hdf5
(133151819 bytes; UUID43361fc7-485a-4590-a342-9bc683e806b0).
The earlier operator episode_000035 reached its3000-frame ceiling and had already
committed before the web restart; no label/deletion was applied by this task.
The new pending-writer path was verified separately using the test episode.

Receipts:
- A6000 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/outputs/pause-recovery-20260930/
- Cobot /home/agilex/jiaan/project/cobot-web/runtime/verification/pause-recovery-20260930/

This verifies actual process/recorder recovery without reloading Stage1.
Pause/HIL timing faults are covered by injected offline races. Continuous live
50Hz publication, real pause/resume/HIL cycles and insertion success still need
operator acceptance; do not advertise them as passed from this non-motion test.
The selected MC30 async_rtc50 profile is retained; faithful synchronous20 remains
the documented rollback. Project handoff/concurrent-dialogue guidance now lives
in each project AGENTS.md and the five laptop entry directories.


## 2026-09-30：可选运行配置、暂存跳过与历史补标签

用户提出 50 Hz 模型显示 Control rate 20 Hz，以及录制中断不应结束整个模型任务。
模型详情现区分动作发布频率与逻辑步频/Replay；登记 50 Hz preset 展示发布50、逻辑20。
采集/部署统一模型加载支持可选运行配置：发布20/30/40/50 Hz、RTC、因果平滑。
未勾选自定义使用所选模型默认值；配置变更在活动轮次被拒绝，空闲应用沿用保留Stage1的重建接口。
录制暂存接口结束当前 owned writer，保留文件与模型任务；完整未标注记录可在历史补标签/重标注。
不完整/损坏文件有待处理回执与显式删除；暂存不隐式标成功/失败或插入Replay，下一轮仍为手动开始。
请求超时显示结果待确认，检查Session/采集状态后才允许再次操作；硬件/执行故障仍保留安全暂停与诊断。
标签采用领域锁内版本校验，保持原HDF5、HIL区间与节点，不修改已经提交的Replay。

隔离worktree验证：网页后端725 passed/6 skipped；前端62 passed；采集领域78 passed；
RL相关79 passed。末次频率参数展示改动另通过34项模型/历史回归。
浏览器连接工具无可用浏览器，真实浏览器视觉验收未完成；DOM交互回归通过。
用户确认退出后现场只读核验：模型offline，recorder/capture idle，active_mode为空。
源码部署与网页重载的实际结果另记；本次离线检查不等于真机50 Hz连续发布或成功率验收。

### 现场发布验收（2026-09-30）

用户确认退出后，现场核对模型 offline、普通采集与 recorder idle、无活动模式或 writer。三个项目代码已提交并 push，本批运行文件按 SHA-256 同步核验。

首次源码发布版本：
- web：984887785f1bf0adfd35b5c94bdae6011f90110e
- dagger：bb85a8a6e1cdd8b9cd8802b5fba56f0ae1a61f36
- RL：d878bded9b9880e291f1871154ee003f882ae278

只通过 ui_down/up 重载正式 8015，网页 PID 418472 → 866075。重载后模型仍为 offline，录制 idle、active_mode 为空；12 个采样硬件／模型进程的 PID 与 start_ticks 保持，最终 ROS readiness 为 ok。网页启动初始查询曾出现 camera_stale，后续验收查询已恢复，不能把初始查询等同于最终验收结果。

正式 API 已提供录制暂存／跳过、历史标签和待处理记录接口；目录中的 async_rtc50 登记显示动作发布 50 Hz、逻辑／Replay 20 Hz。当次目录共有 16 个已接入且可用的部署模型，其中 10 个 RLT 条目返回 execution_settings；页面提供可选 Hz／RTC／滤波配置。该快照不证明当时其他 6 个模型已完成共用执行模块接入。正式页面资源响应已记录 SHA。

末次修正 JS／CSS 缓存版本为 20260930-execution-defer，核验后网页 PID 保持 866075；最终 web 发布版本为 e75d3cbbd033f3f606d5d26453497ca6836d4835，dagger／RL 版本保持上述版本。离线 DOM 回归 62 项通过；完整离线测试数量见本节上文及 offline-validation.json。

本批未启动模型运行、硬件或动作，未改写生产数据。真实浏览器视觉、连续真机 50 Hz 发布及成功率仍未验收。

证据目录：/data/LFT-W02_data/jiaan/jiaan/projects/cobot-web/outputs/recording-defer-rate-20260930/；重点文件为 source-versions.json、final-source-versions.json、site-before.json、site-after.json、web-reload.log、cache-verification.log 与 final-release.json。

2026-10-01 修复说明：原段落在 Git 最初提交中已写成字面问号，无法从该提交恢复原句；本段依据上述同期回执、日志和版本记录重写历史事实。原损坏段落备份与修复校验位于 outputs/migration-encoding-20261001/。此处 PID、模型目录数量与状态均为 2026-09-30 当次快照，不代表当前现场。

## 2026-10-01：精简步数、共用执行选项与历史结果选择

模型下拉只显示真实步数；同一权重的 Hz/RTC preset 合为一个选项，路径、来源和状态留在详情/tooltip。采集与部署的 Hz、RTC、滤波为同一行，置于模型选择下方；删除原默认/加载配置说明。Hz 未勾选沿用模型发布频率，RTC/滤波独立选择，运行中锁定；RLT 空闲时保留 Stage1 重建应用，其他已加载模型重载后使用新选择。原生产默认配置未改。

共用配置校验归 VLA，web 向各暂停适配器传递 COBOT_EXECUTION_OPTIONS。历史 Episode 仅显示成功/失败/未知选择并自动保存；原备注与训练许可保留，unknown 排除训练；CAS 防止覆盖另一人的标签，丢响应先 GET 确认，不自动重提 PUT。旧区间审核编辑器从此历史页面隐藏，采集/标注领域接口保留。

A6000 离线验证：前端 67 passed，相关 HTTP/模型/恢复/历史 83 passed、1 skipped；RLT 21 passed；VLA 共用时钟/滤波/RTC 27 passed，G05 15 passed，XR1 暂停/过期结果 26 passed。只做 CPU/合成 I/O，未加载新 GPU 模型、未运行真实 Episode 或发送机器人动作。现场 SHA、8015 重载及 PID 复核见 outputs/execution-compact-20261001/ 最终回执；页面视觉与各模型连续真机发布率仍需现场观察。

## 2026-10-01：方法／步数目录、所选执行频率与历史结果修复

来源：用户反馈选择 50 Hz 后详情仍显示 20 Hz、原版／优化方法不见、未知步数过多和历史结果框不能操作。
模型选择改为场景／模型／方法／纯数字步数；原版与 MC30 独立，同权重冻结／online、频率 alias 与同方法同一步数合并；Stage1 reference 4999 与未核验／未接入资产从此选择器隐藏，资产和底层接口保留。固定 Warmup 5000 保留；新在线分支的步数读取已发布 Actor 前缀，不冒充尚未发布的 Learner 进度。

执行详情立即反映所选 Hz／RTC／滤波；已加载而未应用时另列当前实际配置，RLT 逻辑／Replay 20 Hz 独立保留。加载入口按采集／评测用途传递，固定 5000 的采集加载可创建独立完整训练分支，评测加载使用所选权重的固定策略。跨用途需要释放后重新加载，不把已加载固定模型的采集误称为在线学习。分支训练指标使用自己的运行目录。

历史列表会迁移旧目录，但旧标签接口未迁移，导致 409 和禁用；标签 GET／PUT 现与历史共用目录别名。普通采集节点元数据原先覆盖 HDF5 标签字段，采集领域已保留真实结果。结果框加载时采用列表中的记录值，再以 GET 为准；自动保存后更新同目录列表、当前 episode 与结果缓存，保留备注／训练许可和版本校验，不改已提交 Replay。

本批同时发现 Stage1 清单已退回 USB；RL 清单恢复 /home/agilex/jiaan/data/rlt/plug_insertion/reference_4999，stage1_root 与在线 model_root 分开。没有重新复制／移动模型资产。

离线：前端 69 passed；web 完整后端 734 passed、6 skipped；末次新增目录／训练指标及相关加载／恢复回归 60 passed、1 skipped；采集领域 78 passed；RL 执行与分支 24 passed；shell 语法通过。全部 CPU／临时数据，不运行生产 Episode、GPU 模型、真实在线更新或动作。真实 50 Hz 持续执行与在线分支真机验收仍待现场。发布与只读现场回执归 outputs/catalog-results-20261001/。

现场发布事实：2026-10-01 代码已 push 并逐文件 SHA 同步；web 209 个运行文件、RL 6／dagger 2 个本批运行文件一致。仅重载 8015，网页 PID 970937；模型 offline、recorder idle、无 active writer／lease，9 个采样硬件／模型 PID 和 start_ticks 不变。实际 CPU preflight 使用 NVMe Stage1 路径且归一化 SHA 与发布清单一致；两个旧目录入口的 8 条历史标签 GET 通过，现场目录和标签响应的 DOM 结果通过。固定 5000 文件与原历史标签 SHA 不变。未运行现场在线分支、真实动作或连续 50 Hz；真实浏览器视觉验收未完成。回执：cobot-web/outputs/catalog-results-20261001/。

## 2026-10-01：选中即显示两行权重路径

来源：用户要求所选权重路径和 Base model 路径一起显示在权重路径处。采集与部署共用选择器立即显示第一行权重路径、第二行 Base model；无 base 的模型只显示实际权重路径，清空选择会清空旧路径。Base model 从折叠详情移至此处，长路径可复制并随容器宽度换行。仅调整静态展示和缓存版本。

69 项既有前端回归通过；实际现场目录 15 个可选模型的 DOM 核验通过，包含立即更新、两页同步、清空旧值。已提交 push 并全运行文件 SHA 同步，正式 8015 的 JS／CSS／HTML 响应 SHA 一致。网页 PID 970937 与 9 个采样硬件／模型进程身份保持，无网页重启、模型加载、录制或动作。真实浏览器视觉未验收。证据：outputs/selected-paths-20261001/。


## 2026-10-06：单路掉线时其余相机继续预览

用户现场确认右相机接线松动，插紧后恢复。本批把网页预览改为每路独立的 JPEG 缓存、更新版本和帧率；一台缺失、过期、冻结或编码失败时，其余正常相机继续更新，异常画面隐藏并显示等待重连，恢复后自动重连。网页显示实际每路帧率，三路有时间偏差时仍可独立预览并如实提示。

`SynchronizedCameraPreview.state/image` 的完整同步三路合同保留，评测首尾留图仍使用原合同；普通录制、Replay、模型输入与动作控制未修改。预览不把旧帧或缺失画面当作有效模型输入。

A6000 独立 worktree 验证：743 Python 测试通过、6 既有跳过；69 前端测试通过。实际隔离 HTTP/MJPEG 合成输入验证右相机掉线后左/顶部各持续输出 8 张不同 JPEG，右路返回不可用并在恢复后就绪；同步证据检查在掉线时仍为 stale。Chromium 验证只隐藏异常画面、正常两路可见并显示掉线提示。未通过拔线或机器人动作制造故障。

验证与发布回执：`/data/LFT-W02_data/jiaan/jiaan/scratch/cobot-web/independent-camera-preview/validation/`、项目 `outputs/deployments/`；现场启停结果以实际发布回执为准。


## 2026-10-07：模型选择明确冻结、Online 与版本身份

用户无法从纯数字步数识别模式。选择器原先按同方法同一步数合并冻结与 Online，且隐藏登记标签；本次保留不同模式／权重的条目，仅继续合并同权重同模式执行 preset。RLT 选项显示步数、冻结／Online 和登记 Actor；详情显示 ID、模式及已报告的 Learner 开关。两页已加载状态另显示实际模型身份、Learner／最近推理 Actor；没有实际版本观测时明确标记登记值，不冒充运行证据。进入采集页不隐式改变已加载模型的学习模式。

72 项完整前端回归通过；使用实际现场目录的 DOM 检查确认新 7k 冻结与 Online 两个 ID 均可选，加载冻结时仍明确 Learner 关闭。只改变静态显示与缓存版本，不改模型资产、算法、HTTP 启停或运动协议。现场同步回执、实际目录快照与测试日志位于 outputs/model-mode-identity-20261007/；源码同步不等于 Online 更新或真机验收通过。


### 2026-10-07：释放后历史 PID 误锁采集加载按钮

用户释放后选择同一个7000 Online，采集页加载按钮仍灰。实际状态offline、process_started/model_ready=false、无Session/操作/active_mode，录制stopped/committed，但登记保留历史PID2054513。UnifiedCollection错误地用PID字段存在作为仍在运行的依据。现用实际process_started与phase判断，明确已释放时不被历史PID阻塞；状态过期或真正仍运行时继续锁定。未删除历史进程信息，不改变启动协议、权重或Replay。

新增回归在修复前复现失败，修复后完整前端74 passed；实际现场目录/状态DOM核对7000 Online选择保留且加载按钮可用。仅同步unified_collection.js及HTML缓存版本，不重启网页/模型、不发启动POST或运动。证据与静态发布回执：outputs/model-mode-identity-20261007/load-disabled/。

## 2026-10-08：π0.5 部署导入冲突已修复；DAgger 3000 权重异常待恢复

来源：用户报告及现场日志。本轮所查两次启动失败 model-20261008T145833.log、model-20261008T150342.log 为相同导入根因：网页 standalone wrapper 的 cobot_console 目录位于 sys.path 前部；VLA 共享目录已在 PYTHONPATH，旧条件没有把它前移，裸 execution_options 导入命中网页同名文件，触发 attempted relative import with no known parent package。不能据此认定更早的所有 50 Hz 故障都相同。

VLA 共用运行模块及 π0.5 baseline／DAgger、Flux、G05、XR1 客户端改用 integrations.cobot 明确包名；保留旧辅助模块路径，更新两份 RTC overlay checksum。代码提交 83a49c9c059da661df34f152c617b7d54d3424a5 已 push、同步；12 个运行文件 SHA 与完整两份 overlay 清单核验通过。算法、默认频率／滤波常数、权重、归一化和动作映射未修改。

真实网页 wrapper／RTC 模块加合成硬件／策略依赖的测试在修复前复现，修复后 baseline／DAgger 各自默认、20 Hz 无 RTC／滤波、50 Hz＋RTC＋滤波、50 Hz 无 RTC＋滤波共 8 个组合到达 ready and PAUSED。CPU 回归共 144 项通过：共用执行／导入／时钟 37、G05 30、XR1 52、web 暂停／执行配置合同 25；Flux 导入冒烟通过。工控机真实 client Python／ROS／OpenPI 环境的 8 个导入／选项解析组合通过。这些不是实际权重推理或持续 50 Hz 发布验收。

现场仅提交一次 DAgger 3000 的 50 Hz＋RTC＋滤波显式加载，保持 manual_pause，不请求 start／resume／home。权重恢复完成，真实相机／关节 observation 同步完成；首个 prefix 为空的普通 baseline 预热请求返回非有限动作，RTCProtocolError: actions_robot must contain finite values。故障发生在 guided RTC 重规划和发布端滤波之前；模型没有 ready。启动器已退出并收尾自己启动的 policy server。未继续提交已知坏权重的原 20 Hz 加载，不声称原配置恢复或两配置真机通过。

只读 CPU 参数恢复扫描（原 float32，不做 GPU／bf16 转换）确认 51 个参数叶、3,353,433,872 个参数中，4 个张量共 96 个非有限值：input_embedding 9、mlp/gating_einsum 27、mlp/linear 58、mlp_1/gating_einsum 2。与 2026-09-28 Getea 迁移 copied-files.jsonl 的 19 文件 SHA 对账，15 个一致，4 个参数数据文件不一致：1bf70e1ab729c2317a20f411e3169a69、29a49413c8812dd549aeeeea3c2debb1、7434943d4187ec08f841850611be2528、a37ff27b36ec269fd140299c14eba633。可确认内容与迁移记录不同，具体改变原因尚未确定。归一化文件 SHA 与登记原始结果一致。

已登记 Cobot 旧 step_3000 是当前 Getea 权重的符号链接，不是独立备份；A6000 2026-09-08 审计记载当时完整权重备份未完成。训练原始产物来源为 trainer 的 task5_hil_realworld_rl/checkpoints/task5_pi05_masked_in_the_pot_round_001/dagger_round001_step2000_3000/2999（旧 Task5 已归档到 legacy-assets/cobot-platform-pre-framework-202608）。本轮按已登记 124.174.13.117 的 25791、65279 两入口尝试，均连接超时。等待同版本可信原件／备份入口，核对资产身份及有限性后才能恢复；未改坏权重、未将 NaN 清零、未切换其他模型冒充恢复。

最终网页 PID 63917 与 12 个采样硬件／模型身份保持；部署 phase=error、model_ready=false、process_started=false，日志所记录 launcher PID 3402035 已不运行，active／operation／writer_token 均为空。页面仍显示本次 50 Hz＋RTC＋滤波选择。没有网页／硬件重启、没有运动、没有生产 Episode 或 Replay 修改。现场拥有权已释放，后续加载前重新核对现场所有权与权重健康。

完整证据：cobot-web/outputs/pi05-import-20261008/ 的 source-release.json、field-imports.json、checkpoint-finite-scan.json、checkpoint-migration-hashes.json、after-failure.json 与 final-release.json；现场 runtime/verification/pi05-import-20261008/。客户端失败日志 runtime/deployment/model-20261008T152221.log；policy server 日志 runtime/deployment/pi05/logs/rtc_policy_server_20261008_152222.log。本批 VLA 旧源文件备份在 vla-platform/runtime/incidents/pi05-import-20261008/before/。部署修复尚未完成，剩余阻碍为同版本健康权重。

### 2026-10-08 追加核验：迁移后曾成功；不能把物理权重损坏当作定论

用户补充旧训练机已经不用，迁移后本机曾成功部署，并指定 /home/agilex/cobot_magic/task5/jiaan/hil_realworld_rl/deployments/in_the_pot/pi05_dagger_round001。只读检查确认该目录仍有旧部署代码，checkpoints/step_3000 是当前 Getea dagger_2000plus3000 的链接，不是第二份权重，也没有误指向 baseline。真实本次 policy server 日志明确从 dagger_2000plus3000/params 恢复，norm stats 也来自该 DAgger 目录；网页 Base model 的 baseline_2000 仅指训练初始化来源。

已找到迁移后 model-20260929T150832.log 与 model-20260929T162836.log，两次均 ready and PAUSED；对应 policy server 日志均明确恢复当前 Getea dagger_2000plus3000 路径。用户关于迁移后曾成功部署的陈述有现场日志支持。

普通重复读取的 4 个异常文件 SHA 与第一次一致；改用 dd iflag=direct 后，一个文件 1bf70e1ab729c2317a20f411e3169a69 的 SHA 与原始迁移记录完全一致，其他三个产生不同于缓存读取、且仍不匹配迁移记录的 SHA。仅针对本 checkpoint 的只读文件描述符使用 POSIX_FADV_DONTNEED 后，第一个文件普通读取也恢复原 SHA，但其余三个仍不一致。随后 CPU 恢复后，第一个文件普通读取 SHA 又变化。未全局 drop_caches、未 sync／卸载外接盘、未重启机器或改权重。

缓存处理后再次只读 CPU 参数恢复，非有限值变成 127 个：input_embedding 24、mlp/gating_einsum 61、mlp/linear 42。TensorStore／Orbax 版本 0.1.74／0.11.13；隔离进程将 file_io_concurrency 与 data_copy_concurrency 均设为 1，仅串行读取 embedding，仍发现 26 个非有限值。不能据此认定仅为并发加载器故障。未改生产依赖或 TensorStore 默认并发配置。

在 NVMe 独立诊断目录保留不可信的文件读取样本，对 29a49413c8812dd549aeeeea3c2debb1 两个不同读取样本比较，共 395 个字节不同，分布在 3 个 512-byte 扇区。多个读取样本的逐位多数结果不匹配原始完整 SHA，拒绝用于部署；只接受精确匹配迁移 SHA 的候选，不通过清零 NaN、改浮点参数或更换模型伪装修复。冗余相同样本与失败多数副本已删除，保留两份有差异的诊断样本及小型回执；原权重文件不写入、不改名、不替换。

因此更正上一节的初步归因：已确认当前加载取得了非有限参数，且读取结果有缓存／读取路径差异，但尚未确认持久磁盘文件本身损坏，更不能把重新找旧训练机权重当作唯一恢复方法。剩余需要区分底层数据、文件系统／缓存、内存与读取路径问题。agilex 对 /dev/sda2 没有读权限且 sudo -n 不可用，已请求用户在现场终端执行只读 sudo ntfscluster -f -I 358606 /dev/sda2，取得异常文件底层映射信息；不请求用户发送密码。

15:39 结束最初 π0.5 加载验证并释放现场拥有权后，15:41 网页已启动 plug-v3-supported-online，后续只读 HTTP 状态为 ready、无录制 writer；本对话未切换、卸载或停止该 RLT 模型，也未继续占用 GPU 运行 π0.5。后续 π0.5 真权重加载验证前须重新协调现场拥有权。代码导入修复已完成，实际 DAgger 部署恢复仍未完成。

新增现场证据（均在本任务 runtime/verification 或 runtime/incidents 中）：historical-dagger-logs.json、checkpoint-repeat-hashes.json、checkpoint-direct-hashes.json、checkpoint-after-advice-hashes.json、checkpoint-after-advice-finite-scan.json、serial-embedding-scan.json、local-checkpoint-search.json、copy-hashes.json、recovery.json、read-difference-pattern.json、read-difference-ranges.json。汇总归档到 cobot-web/outputs/pi05-import-20261008/；final-release.json 已追加最新诊断，保留之前快照的时点，部署恢复标志仍为 false。

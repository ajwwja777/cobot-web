# Cobot Web 迁移记录

日期：2026-09-27。当前批次：**网页源码、开发环境和正式 8015 已迁入新目录；旧文件因仍有硬件／RLT 依赖保留。**

用户已明确停止当前任务并授权切换。核对部署、录制和原 RLT 状态后，正常停止残留的故障 Session／学习进程，再切换网页；没有重新启动机械臂、相机或发送运动指令。

## 位置与版本

| 内容 | 实际位置 |
|---|---|
| A6000 主代码和 Git | `/data/LFT-W02_data/jiaan/jiaan/projects/cobot-web` |
| Cobot 运行副本 | `/home/agilex/jiaan/project/cobot-web` |
| Cobot uv | `/home/agilex/jiaan/project/cobot-ops/tools/uv` |
| 日志、PID、uv 缓存 | `/home/agilex/jiaan/project/cobot-ops/runtime/` |
| 只读预览运行记录 | `/home/agilex/jiaan/project/cobot-ops/runtime/web-preview/` |
| 新数据根 | `/home/agilex/jiaan/data` |
| 正式 8015 当前来源 | `/home/agilex/jiaan/project/cobot-web/app/backend` |

仓库：https://github.com/ajwwja777/cobot-web，main。源码提交 `1cf4797f4942f2f27d3815943f7e23cab79452df`；启动依赖隔离修复 `117fe141aea17e9081a1db535e557a155fa136f3`。均已 push 并核验远端一致。

A6000 的 `scripts/sync_cobot.py` 仅同步已提交运行文件，不重启进程。当前 Cobot `.release.json` 记录收尾接口修复版本 `14b4484`，273 个运行文件 SHA-256 一致。开发测试、Git、模型、数据不随源码同步。文档后续提交可能领先运行副本，运行版本以 .release.json 为准。

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

目前可清理旧项目的清单为空，旧代码、数据、模型和运行目录全部保留。新目录中仅移除了本批草稿复制产生的重复 `app/backend/pyproject.toml`，使用根目录的唯一依赖声明。

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

当前继续完成两页共用模型加载、模型实际路径展示、独立选择数据目录。下一批迁移 RLT 与模型资产前，应先核对原路径依赖；尚不能删除仍被机械臂／相机进程引用的旧网页硬件代码。共享模型功能完成后更新本记录并同步运行版本。

早期入口初始化提交：`7d81a477adb6e89625bb9454d6c3d465cc237966`；初始化记录提交：`d08e656a6e8f16b3bd4ab64269c270dd9e52a5b8`。本记录更新的是实际迁移进展，不撤销已保留的原成果。

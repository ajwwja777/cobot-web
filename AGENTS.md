# Cobot Web 项目入口

先读统一框架 `/data/LFT-W02_data/jiaan/jiaan/agent-guide/AGENTS.md`，再读本项目 [README.md](README.md) 和 [迁移记录](docs/MIGRATION.md)。

A6000 是主代码、Git 与开发工作区；Cobot 仅部署运行副本。2026-09-27 用户确认现场任务已停止并授权切换，正式 8015 已切换到新目录。2026-09-28正式8015改用新rl-platform/control/data路径，共享加载与数据读取通过；旧资产归档和清理以迁移记录为准。ops 本地目录已验收删除；不为网页调试启动真机运动或训练。

主要代码已改用 `cobot_console`、`capture_core`、`segmented_capture`。历史 ROS／HTTP／数据协议保留兼容，不因清理命名修改线上接口。硬件实现及legacy_control已归同级cobot-control；当前工作方式以本项目 README 和迁移记录为准。

检查现有修改，保留其他对话成果。代码和验证结果先在 A6000 完成，提交／push 并核验后同步 Cobot；设备运动、采集／HIL、RLT 或模型问题按项目归属交接，不能为网页显示改变真实控制状态机。

2026-09-27 用户取消独立 ops 维护项目：网页使用／任务管理／恢复工具归本项目，先读 [命令行手册](docs/COMMAND_LINE.md) 与 [故障手册](docs/WEB_RECOVERY.md)。runtime 与现场 uv 归本项目；实体迁移／旧目录清理结果以 docs/MIGRATION.md 为准。不能在活进程写入时直接删除／迁走。硬件、数据、算法问题直接交对应项目，避免额外运维层。

## 2026-09-30 按项目接管与并行对话

这是长期项目入口，不再处于“仅初始化”阶段。先读最新记录并核对代码/运行状态；历史旧路径、PID 和未完成描述不能当作当前事实。

- 负责：页面、HTTP、统一模型/任务调用、进程身份/日志、录制 HTTP 编排、状态/错误/恢复按钮与终端兜底。
- 深入阅读：README.md、docs/ARCHITECTURE.md、docs/COMMAND_LINE.md、docs/WEB_RECOVERY.md、docs/MIGRATION.md。
- 实现入口：app/backend/cobot_console/、app/backend/capture_core/api.py、app/backend/segmented_frontend/、configs/、scripts/console.py。
- 当前事实：正式 8015 已在新项目。2026-09-30 增加根因、Stage1 保留状态、针对性收尾恢复与常驻 RLT 通用重启；实际活动录制收尾并保留模型重建通过。
- 下一步：每种 loading/ready/paused/error/stale/operation 都有明确状态与可操作建议；超时结果待确认，不重复提交。持续回归普通采集/部署/RLT 恢复及轮询；文件存在、进程启动、模型就绪与真机通过分开。
- 边界：硬件交 control，采集领域交 dagger，模型能力交 VLA/RL。页面不复制算法，不以杀进程伪装暂停；ui_down/up 只重载页面服务。

用户会在同一项目开多个终端/对话并 fork。fork 不隔离工作树、GPU、端口、模型、Replay 或机器人。

1. 接管先读 git status/diff、git worktree list、实际进程/录制状态及 /data/LFT-W02_data/jiaan/jiaan/scratch/cobot-web/coordination/ 下已有任务说明（存在时）。先说明范围和共享资源。仅要求“读取目录 MD，了解项目”时先汇报，不自动训练/重启或执行所有旧待办。
2. 并行任务各在上述 coordination 下维护一个可读主题名 MD，登记负责人/对话标识、时间、分支/worktree、基线提交、计划文件、GPU/端口/输出和状态；自己的说明自己更新，结束标记完成。临时协调信息不入业务 Git，有用结果写正式文档，不替别的任务认领/完成工作。
3. 调研默认只读；分析用独立输出/只读快照，不改生产 Replay/权重。并行代码改动用独立分支/worktree，放 scratch/cobot-web/<可读主题>/，先核对依赖根/环境，不为 worktree 改生产路径。不在别人工作树切分支、reset、clean、stash 或全量提交。
4. 同文件/接口交叉先明确归属，独立开发后审核合并和调用方；合并、push、发布串行。共享 main、环境和机器配置不是并行试验区。只提交本任务改动，不 force push，不静默覆盖；合并前重查远端及未提交变化。
5. Cobot 同时只有一个启停/部署负责人，跨项目共用 /data/LFT-W02_data/jiaan/jiaan/scratch/cobot-web/coordination/cobot-live.md 说明（存在时先读）。未明确接管时仅只读/离线工作，不因模型“暂停”就抢 GPU、重启服务或切数据目录。进程锁只提供互斥，不是运动授权；硬件重启/运动前核对现场条件。
6. 新算法、采样、RTC/异步/EMA/频率使用可选配置、独立实验输出及明确回退。研究可并行；生产模型默认/参数/Replay 修改与运行负责人协调。
7. A6000 开发验证、所属仓库提交 push 后按清单校验同步 Cobot；区分源码已同步与运行已切换。结果写所属项目及 guide/projects/cobot-web/README.md 事实摘要；不改 guide 治理、不提交/推送 guide Git。

主仓库 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web；现场副本 /home/agilex/jiaan/project/cobot-web。数据/权重通常引用 Getea1/jiaan/{data,model}，实际登记配置优先（如 NVMe Stage1 路径）；不擅自搬资产。

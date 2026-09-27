# Cobot 操作网页

保留现有操作台、统一采集、训练和部署页面，以及相机／输出面板、模型与任务状态交互。

## 入口与位置

- 先读统一框架：`/data/LFT-W02_data/jiaan/jiaan/agent-guide/AGENTS.md`。
- A6000 主工作区：`/data/LFT-W02_data/jiaan/jiaan/projects/cobot-web`。
- 笔记本对话入口：`D:\Code\jiaan_workspace\cobot-web`。
- 自有独立仓库：`https://github.com/ajwwja777/cobot-web`（目标分支 `main`）。
- Cobot 目标部署位置：`/home/agilex/jiaan/project/cobot-web`，本轮尚未部署。
- 当前阶段：入口与仓库初始化；旧业务代码、环境、模型和数据尚未迁移，现有服务入口未切换。

## 负责什么

前端界面、网页 API 编排、状态展示、任务提交与输出查看。保留已确认的交互、快捷键和中英文行为。

设备与运动逻辑由 cobot-control 实现，采集由 cobot-dagger 提供，模型和 RL 调用对应平台，进程启动、PID、日志和健康管理与 cobot-ops 对齐；不能复制多套业务状态机。

## 机器与资产

A6000 负责主代码、Git、维护文档、主要开发验证环境、数据处理和离线评测；训练按资源需要在 A6000／已授权训练机进行。Cobot 只部署本项目现场实际需要的硬件、采集、推理、网页或维护组件，不复制仿真资产和完整训练环境。

Cobot 采集及评测数据统一规划在 `/home/agilex/jiaan/data/`。模型放所属项目的 `models/`（上游已有 `checkpoints/` 等目录时保留其源码布局，由配置明确实际权重位置）；同一资产跨项目引用，避免重复复制。现场服务日志、PID 和状态交由 `cobot-ops/runtime/` 管理；训练 checkpoint、配置和指标保留在所属项目 `outputs/<实验>/`。环境、模型、大数据与 runtime 不入 Git。

## 项目协作

页面和展示问题由本项目负责；业务状态错误交对应控制／采集／模型／RL 项目；进程、存储和日志异常交 cobot-ops。

先读本次任务涉及的依赖项目入口和接口说明，再修改相关边界；接口变更要记录受影响调用方与验证方式。常用项目：`cobot-control`、`cobot-dagger`、`vla-platform`、`rl-platform`、`cobot-web`、`cobot-ops`，主工作区均在 `/data/LFT-W02_data/jiaan/jiaan/projects/`。需要专题对话时仍共享所属项目，不因此重复建立业务仓库。

## 下一步

先制作当前页面与 API 的版本快照，迁移一个可隔离的静态资源／只读页面范围，以独立验证入口核对，不占用或替换现有 8015 服务。

旧位置、验收条件和切换／清理规则见迁移记录。

来源：2026-09-27 用户确认的项目划分、机器职责与逐批迁移方案；本轮范围仅初始化。

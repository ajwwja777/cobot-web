# Cobot Web

```text
cobot-web/
├── app/backend/cobot_console/       # HTTP、任务、模型运行与结果记录
├── app/backend/segmented_frontend/  # 交互、翻译、布局、输出
├── app/backend/capture_core/api.py  # 录制HTTP适配
├── app/backend/segmented_capture/api.py
├── configs/                        # 示例与现场local.json
├── scripts/                        # 网页/CLI、兼容转发与发布
└── docs/
```

本批交付与验收：[HANDOFF_20260929](docs/HANDOFF_20260929.md)。

项目结构：[ARCHITECTURE](docs/ARCHITECTURE.md)。换机部署：[DEPLOYMENT](docs/DEPLOYMENT.md)。

Cobot 的操作网页：设备状态、普通／模型辅助采集、训练状态、部署评测、相机与任务输出。

**A6000 是代码与 Git 的主工作区。Cobot 运行同步副本。** 正式 8015 已于 2026-09-27 切换到新目录，当前运行与同步记录见 `docs/MIGRATION.md` 和现场 `.release.json`。2026-09-28又完成新RLT／硬件／数据路径切换与共享加载验证；被动硬件验收后已停止，现场使用前按手册启动。网页接口验收不等于真机运动或模型成功率验收。

## 终端使用与故障恢复

- [完整命令行流程](docs/COMMAND_LINE.md)：从 CAN／ROS／相机启动，到普通／模型采集、共享模型、Session、评测、归位、输出与退出；包括底层脚本和完整 API/schema 入口。
- [网页故障与终端恢复](docs/WEB_RECOVERY.md)：HTTP 失败、结果不确定、进程树、显存、UI 重启和磁盘故障。
- Cobot 常用入口：`python3 scripts/console.py --help`。不依赖网页排障：`python3 scripts/console.py recovery status`。
- 网页输出栏“诊断”展示错误请求和处理建议，并可手动检查实际设备／模型／录制／存储状态。

2026-09-27 用户决定不再单独维护 cobot-ops 项目。网页使用、任务管理和恢复文档／工具归本项目；硬件和算法实现继续归对应领域。ops 的 runtime、uv 与恢复证据归本项目；旧目录按迁移记录验收后删除，Git 历史留档。不需要独立 ops 对话或服务。

## 从哪里读

| 位置 | 负责内容 |
|---|---|
| `app/backend/cobot_console/` | 网页 API、健康展示、任务输出、共享模型生命周期 |
| `app/backend/capture_core/` | HTTP 适配和兼容入口；领域库位于同级 cobot-dagger/src/capture_core |
| `app/backend/segmented_capture/` | HTTP 适配和兼容入口；领域库位于同级 cobot-dagger/src/segmented_capture |
| `app/backend/segmented_frontend/` | 当前操作网页，原生 JavaScript／CSS |
| `app/backend/frontend/` | 保留的独立回放／审核页面 |
| `app/shared/schemas/` | 持久化数据格式 |
| `scripts/` | 网页启停、同步、现场命令封装与只读诊断 |
| `configs/hosts/` | 按机器登记依赖位置；`configs/local.json` 是本机选择，不入 Git |
| `app/backend/tests/` | 不依赖机器人运行的回归测试 |
| `docs/MIGRATION.md` | 验收证据、当前限制与切换状态 |

新的 Python 包不用任务编号命名。旧 ROS 话题、节点名、数据格式和模型配置中的编号属于兼容协议；这一批不改它们，避免破坏正在使用的真机／模型链路。来源与映射见 [SOURCE_PROVENANCE.json](docs/SOURCE_PROVENANCE.json)。

## 工作区与环境

| 机器 | 路径 |
|---|---|
| A6000 主工作区 | `/data/LFT-W02_data/jiaan/jiaan/projects/cobot-web` |
| Cobot 运行副本 | `/home/agilex/jiaan/project/cobot-web` |
| Cobot 日志／PID／任务状态 | `/home/agilex/jiaan/project/cobot-web/runtime` |
| 新的采集／评测数据根 | `/media/agilex/Getea1/jiaan/data` |

仓库：[ajwwja777/cobot-web](https://github.com/ajwwja777/cobot-web)，`main`。

笔记本入口为 `D:\Code\jiaan_workspace\cobot-web\AGENTS.md`，项目根层只保留该入口。上传材料放 `uploads/`；确需本地浏览器验证的工具与输出放同名子目录 `cobot-web/`，没有需求不创建空目录。2026-10-09 已核验归档并清理本地历史 scratch 和两批网页迁移/恢复材料，当前仅保留入口；实际位置与逐文件证据见 [迁移记录](docs/MIGRATION.md#2026-10-09笔记本网页项目归档与整理)。

Python 环境是项目内的 `.venv/`，依赖只在根目录的 `pyproject.toml` 声明，版本由 `uv.lock` 固定。保留 Cobot ROS Noetic 对 Python 3.8 的兼容。`.env` 如有使用只负责配置，不是 Python 环境。现有硬件 SDK 使用已安装的 `aloha` conda 环境；它不是本次网页环境，未改名或重建。

在 A6000 的项目根目录：

```bash
uv sync --locked
uv run pytest -q
npm ci
npm test
COBOT_DATA_UI_PORT=18015 ./scripts/preview.sh
```

Node 仅用于前端开发测试，Cobot 不需要安装 npm 或开发依赖。网页没有打包步骤，API 直接提供版本控制内的静态文件。当前可用的 uv 位于 `/home/LFT-W02/.local/bin/uv`；未加入 PATH 时使用完整路径。

## 同步与运行

`python3 scripts/sync_cobot.py` 从 A6000 同步已提交的运行文件，核对逐文件 SHA-256，并写 `.release.json`。它不复制 Git、模型、数据、开发依赖、测试缓存，也不重启任何服务。

Cobot 使用 `configs/hosts/cobot.json` 作为 `configs/local.json`。现场 uv 位于 `/home/agilex/jiaan/project/cobot-web/tools/uv`：

```bash
cd /home/agilex/jiaan/project/cobot-web
UV_CACHE_DIR=/home/agilex/jiaan/project/cobot-web/runtime/cache/uv /home/agilex/jiaan/project/cobot-web/tools/uv sync --frozen --no-dev --python /usr/bin/python3
./scripts/preview.sh
```

只读预览默认在 **8018**：拒绝操作请求，不启动 ROS 录制订阅，不启动／停止模型或硬件节点。`./scripts/preview.sh ui-down` 只关闭该预览。正常网页入口是 `scripts/ui_up.sh`、`scripts/ui_down.sh`、`scripts/ui_status.sh`；正式 8015 已使用新入口运行。

`scripts/verify_device_health.py` 是只读诊断：在配置好的 ROS 环境中运行，短暂订阅关节／示教反馈并监听 CAN，不发布运动指令，不调用控制服务。

## 依赖与边界

硬件实现、位姿和 launch 已由同级 cobot-control 接管，web 仅保留脚本转发、API 编排和展示。采集领域库已迁入同级 cobot-dagger，web 保留同一个录制 HTTP 提供者。

当前数据、Replay 与部署权重使用 Getea1 的 data/model 分类目录；算法环境和代码留在系统盘。`configs/hosts/cobot.json` 明确登记其当前真实位置，两个 π0.5 入口已由 vla-platform/integrations/cobot/pi05 接管，继续引用登记的既有 Python/ROS 环境。浏览器旧目录偏好按前缀迁到新根，数据根显式加入允许列表，其他目录及符号链接逃逸仍被拒绝。ROS/Piper/Astra 驱动继续共用登记的现场安装；来源快照、机器配置及换机安装入口由 cobot-control 管理。对应问题先保留命令、版本、日志与复现条件，交所属项目处理。

部署和采集共用同一份模型目录、进程与加载状态；模型选择展示实际权重路径。加载与准备 Session 不启动推理。RLT 每轮开始时固定采集／评测用途及目录，结束后才能切换用途；纯评测不写 recorder 或 replay。在线更新入口保留原 online_rl.yaml 和 learner；固定模型评测使用对应冻结入口。目录可以在加载前选择，RLT 录制中的新选择用于下一轮。

日期：2026-09-28。完整进度与未完成项见迁移记录。

## 数据和模型存放

2026-09-28 最终确认：Cobot 数据和 checkpoint 实体放 /media/agilex/Getea1/jiaan/{data,model}；数据按场景分，模型按项目/模型/场景/版本分。本轮不新增 A6000 资产备份，既有 A6000 历史资产另行保留。当前路径、占用、验收与清理状态见[存放清单](docs/STORAGE.md)。

## 训练与诊断分析

训练页显示当前 Learner、已发布 Actor、Replay、待更新预算、登记参数和核心损失。诊断分析页提供按实际 Actor/轮次类型的自主与接管结果、真实 batch 分布、Q/目标项、Replay 状态聚类和代表动作。历史 Warmup 图保留在折叠归档，不再与在线曲线混排。

详细口径与只读命令见 [命令行手册](docs/COMMAND_LINE.md#训练与诊断分析)，算法侧见同级 rl-platform/docs/ANALYSIS.md。新页面目前覆盖 RLT plug_insertion；不把其他模型的旧训练记录冒充在线指标。

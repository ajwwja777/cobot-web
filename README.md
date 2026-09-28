# Cobot Web

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
| `app/backend/cobot_console/` | 网页 API、设备健康、任务输出、共享模型生命周期／RLT 适配 |
| `app/backend/capture_core/` | 反馈缓存、采样、HDF5 录制、标签、预览 |
| `app/backend/segmented_capture/` | 分段采集、节点、暂停／继续与 HIL 协调 |
| `app/backend/segmented_frontend/` | 当前操作网页，原生 JavaScript／CSS |
| `app/backend/frontend/` | 保留的独立回放／审核页面 |
| `app/shared/schemas/` | 持久化数据格式 |
| `robot/` | 现有硬件适配；当前作为迁移兼容边界保留 |
| `integrations/legacy_control/` | 现有控制辅助代码快照，等待 cobot-control 后续验收接管 |
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
| 新的采集／评测数据根 | `/home/agilex/jiaan/data` |

仓库：[ajwwja777/cobot-web](https://github.com/ajwwja777/cobot-web)，`main`。笔记本只保留对话入口。

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

本次保留采集和硬件适配的唯一迁移副本，没有把同一状态机复制到六个项目。后续由 cobot-control 接管硬件、cobot-dagger 接管采集，web 保留 API 编排和展示；切分必须带接口与回归验证。

模型权重和 RLT 算法运行环境尚未整体迁移；已有部署评测数据已逐文件核验复制到新数据根。`configs/hosts/cobot.json` 明确登记其当前真实位置，仍可识别旧模型；不会伪造新路径为已部署。历史数据根显式加入允许列表，其他目录及符号链接逃逸仍被拒绝。ROS/Piper/Astra 驱动仍使用现场已安装工作区，后续归 cobot-control。对应问题先保留命令、版本、日志与复现条件，交所属项目处理。

部署和采集共用同一份模型目录、进程与加载状态；模型选择展示实际权重路径。加载与准备 Session 不启动推理。RLT 每轮开始时固定采集／评测用途及目录，结束后才能切换用途；纯评测不写 recorder 或 replay。在线更新入口保留原 online_rl.yaml 和 learner；固定模型评测使用对应冻结入口。目录可以在加载前选择，RLT 录制中的新选择用于下一轮。

日期：2026-09-27。完整进度与未完成项见迁移记录。

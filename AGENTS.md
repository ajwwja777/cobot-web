# Cobot Web 项目入口

先读统一框架 `/data/LFT-W02_data/jiaan/jiaan/agent-guide/AGENTS.md`，再读本项目 [README.md](README.md) 和 [迁移记录](docs/MIGRATION.md)。

A6000 是主代码、Git 与开发工作区；Cobot 仅部署运行副本。当前处于迁移验收阶段：新目录只读预览已启动，8015 仍是旧服务，用户正在评测 Reference／准备在线 RL。未确认 Session 与学习任务结束前，不切换 8015，不停止硬件／模型进程，不删除旧目录。

主要代码已改用 `cobot_console`、`capture_core`、`segmented_capture`。历史 ROS／HTTP／数据协议保留兼容，不因清理命名修改线上接口。`integrations/legacy_control` 与原有子目录文档是历史兼容来源，当前工作方式以本项目 README 和迁移记录为准。

检查现有修改，保留其他对话成果。代码和验证结果先在 A6000 完成，提交／push 并核验后同步 Cobot；设备运动、采集／HIL、RLT 或模型问题按项目归属交接，不能为网页显示改变真实控制状态机。

# 项目结构与调用方向

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

web → control：CAN/ROS、健康、示教/夹爪、归位与硬件进程所有权。
web → dagger：录制、历史、HIL与mask，兼容包引用dagger/src，不保留另一份领域实现。
web → VLA/RL：读取项目登记，调用薄适配；通用进程/日志、API、操作记录归web。
RL → recorder HTTP：共享采集/评测环境归rl-platform/integrations/cobot_runtime，不再导入web Python。当前HTTP录制端仍由web的同一个recorder提供；关闭网页前先结束录制Session。control硬件管理不依赖网页在线。

Flux保持原生框架，历史部署归integrations/cobot；RLT保留methods与固定third_party；EXPO-FT保留独立仓库、环境与训练循环。

登记由各项目维护：rl-platform/configs/deployment_models.json；vla-platform/configs/{pi05_models,cobot_models,external_models}.json。external_models为可选现场登记。web只聚合清单。

文件存在、进程启动、模型就绪、推理验证、真机验收分别记录。没有就绪协议的外部进程显示process_running；不支持的暂停明确禁用，不通过杀进程伪造。RLT分别记录Stage1索引、Learner步数、Actor版本。actor_snapshot是推理快照，续训还需要优化器、learner checkpoint与Replay。

仅同架构、预处理/归一化、相机和动作定义兼容时，换权重可只登记配置。不同家族仍需模型代码和动作适配。

## 场景/模型登记与训练扩展（2026-09-29）

Cobot 物理能力归 cobot-control，采集/HIL/mask 归 cobot-dagger。
相机顺序、动作维度、归一化、夹爪映射与 RTC 协议归所属模型适配器：
vla-platform/integrations/cobot、rl-platform/integrations/cobot_runtime
及 methods/openpi_rlt。web 读取登记并调用，不维护第二套算法或硬件规则。

同一实现兼容的新权重可补登记。外部 sh 需明确环境、ready/stop 协议、
PID 归属与能力；文件存在不等于支持暂停、HIL 或已通过真机验证。
模型记录可增加 data_directories.collection/evaluation；现场默认映射在
cobot-web/configs/model_directories.json，按稳定 model ID 关联。
模型记录优先，{DATA} 来自本机配置，不从权重名猜目录，不搬迁资产。

后续训练选择器应登记场景输入、cobot-dagger 转换入口、原生配置/环境、
输出及能力。Flux 继续使用 configs/<family>/，RL 使用 configs/methods.json
与各方法配置。任意模型一键转换/训练尚未实现；部署登记不能冒充训练支持。

# Cobot RLT 控制台与在线学习恢复设计

日期：2026-09-22

## 已确认根因

- 当前 release 14135 在旧 warmup 留出上为正，但在真实 online autonomous 留出上的 Q 排序为负。
- rtc_upstream_train.evaluate 只把 warmup policy episode 纳入 value gate，在线发布门看不到真实 online autonomous 退化。
- 逐父版本相对门允许小幅退化累积；训练日志每 100 步采样也常漏掉 actor update。
- delta_weight=300 是历史抖动后的安全校准，不是当前失败的首要根因；只能通过固定 replay/seed 消融选择。
- 三路独立 MJPEG 在断流后会留下静止画面，也无法保证三帧来自同一时间窗口。

## 约束

- 8015 保持唯一公开入口；诊断、预览和训练都不得进入 30 Hz 控制链。
- 三相机必须由同一 cache snapshot 组成 triplet；过期、冻结或 skew 超限时明确告警并自动重试。
- 网页不接受任意 shell；设备操作全部是固定枚举和固定脚本 allowlist。
- Home/Recover 需要二次确认；实现和自动测试不实际调用机器人动作。
- loss、Q、AUC 和离线门限不得称为真机成功率。
- autonomous、HIL、demonstration 分开统计；actor batch 的 HIL 目标为 20%，critic 按 outcome/source 分层。
- 先 critic-only burn-in，再 actor+critic；发布必须经过固定 autonomous-online 门。

## 相机数据面

- cache 为每路流维护单调 sequence。
- SynchronizedCameraPreview 一次读取三路 snapshot，要求 fresh，默认 source skew 不超过 120ms，预览上限 8Hz。
- source timestamp 不可比较时退化为 arrival timestamp；相同 triplet sequence 不重复编码。
- 状态包含 generation、sequence、age、FPS、skew、last advance age，以及 ready/stale/desynced/frozen/unavailable。
- 浏览器只在 generation 变化时原子替换三张 JPEG；冻结时保留最后画面并显示时长与重连状态。
- 旧 MJPEG endpoint 保留兼容，但统一页面不再默认使用。

## 可观测性

- 聚合完整 release lineage，不只读取当前 release 最后三行。
- 展示 actor/critic loss、actor raw/weighted BC-Q-delta、Q1/Q2/target/TD error、batch source/outcome/HIL。
- 展示 autonomous success/failure/HIL 的 episode-position Q median/IQR/count/AUC/separation。
- 展示推理 decision chunk 的 Q(actor/reference/executed) 以及 actor 对 reference 的 raw/projected/conditioned 修改。
- 展示 load、replay build、critic burn-in、joint train、evaluation、publish 的阶段进度。
- 图表提供横纵轴、刻度、单位、图例和 hover；移除旧关节抖动曲线。
- 在线 critic telemetry 使用低优先级异步 worker，控制请求不等待它。

## 设备控制面

- 灰=未运行，黄=启动或恢复中，绿=ready，红=fault。
- 固定操作包括 CAN 检查/配置、ROS master、arms、cameras、Home、Recover、RLT reference/frozen/online/stop/down。
- 每个 job 返回 id、phase、pid、日志尾部、退出码与持续时间；PID 所有权和重启恢复必须校验。
- 进程存在不等于硬件 ready；ready 合并端口、ROS 注册、话题 freshness 和 lifecycle。

## 训练与发布门

1. 按 episode UUID 与时间/场景组固定 train/validation，禁止 transition 泄漏。
2. critic-only burn-in 必须 finite，并在 online autonomous 留出达到正 separation 与 AUC >= 0.60。
3. actor+critic 使用约 20% HIL actor 样本，并报告实际比例。
4. 对 delta 10/30/100/300 做同 replay、同 seed、同预算消融。
5. all-online、autonomous、policy-terminal、HIL 分别报告；HIL 不得替代 autonomous gate。
6. human fit、conditioned smoothness、Piper limits、动作方向与固定基线都必须通过。
7. 只原子发布通过全部固定门的最佳候选，否则保留稳定 frozen actor。

## 现场验收

- 先 frozen 同场景 10 次，不探索、不 HIL，记录自主成功率与终点偏差。
- 安全和轨迹通过后开启 online；每 5 条新有效 episode 触发候选训练。
- 建议每 5 条约 1 条 HIL，并在明显偏离但仍可恢复时介入。
- 网页必须解释为何训练、为何未训练、为何发布、为何拒绝及 actor 版本变化。

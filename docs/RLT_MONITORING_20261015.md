# RLT Online 采集页监控（2026-10-15）

数据采集页底部沿用 EXPO 四卡，选中已登记的 RLT Online 后读取其独立运行目录。GET `/api/collection/rl-monitoring` 使用后台有界日志读取，与模型是否加载无关；Frozen/Reference 不伪装为在线训练。前端脚本版本更新为 `20261015-rlt-online`。

曲线为平均 Q1/实际训练 target、原生双 Critic loss、近20轮自主成功率，以及本轮固定已保存 Critic 的动作块 Q1。Warmup、评测、缺标签和接管口径与 RL 方法层一致。MC 混合 target 按实际训练目标命名。单轮 Q 在 CPU 后台计算，显示 Actor/Critic 各自版本；旧回合缺失值不补造。

方法与测试证据：rl-platform/docs/online/tasks/2026-10-15-rlt-monitoring/TASK.md。保留 EXPO 独立远程 Learner 监控；未改模型参数、Replay、权重、机器人状态机或注册模型的训练授权。当前 realtime-expo RLT 入口被另一任务暂改为 Reference/Frozen，未在本任务恢复 Online。

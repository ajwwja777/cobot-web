# RLT Online 采集页监控（2026-10-15）

数据采集页底部沿用 EXPO 四卡，选中已登记的 RLT Online 后读取其独立运行目录。GET `/api/collection/rl-monitoring` 使用后台有界日志读取，与模型是否加载无关；Frozen/Reference 不伪装为在线训练。前端脚本版本更新为 `20261015-rlt-online`。

曲线为平均 Q1/实际训练 target、原生双 Critic loss、近20轮自主成功率，以及本轮固定已保存 Critic 的动作块 Q1。Warmup、评测、缺标签和接管口径与 RL 方法层一致。MC 混合 target 按实际训练目标命名。单轮 Q 在 CPU 后台计算，显示 Actor/Critic 各自版本；旧回合缺失值不补造。

方法与测试证据：rl-platform/docs/online/tasks/2026-10-15-rlt-monitoring/TASK.md。保留 EXPO 独立远程 Learner 监控；未改模型参数、Replay、权重、机器人状态机或注册模型的训练授权。当前 realtime-expo RLT 入口被另一任务暂改为 Reference/Frozen，未在本任务恢复 Online。

交付：RL e607859 / Web ed2320c；81 RL CPU、7 Web Python、5 DOM通过。Cobot10文件SHA同步并保留Reference440116，独立读取真实历史成功。Web后端重载暂等用户真机操作暂停窗口；不能声称已激活。证据 runtime/verification/rlt-charts-20261015/{install,cpu-score}.json。

2026-10-15 后续激活完成：用户明确确认立即重载。正式 Web426526→466226，正常 ui_down/up；采集 idle、模型 offline，14个模型/ROS/相机/控制进程PID及启动身份保持，三相机各144帧确认，临时订阅进程已退出。实际GET接口在Actor未加载时成功读取teacher5 107点、credit_mc30 480点、原Online600点。生产JS与实际API的Chromium组件验收：4卡、2张真实历史曲线、2个缺数据提示，零JS异常；脚本版本20261015-rlt-online。未加载模型、启动训练或发送机器人指令。现场证据 runtime/verification/rlt-charts-20261015/{reload,activation-api}.json（cobot-web）；浏览器证据 scratch/cobot-web/rlt-charts-20261015/activation-browser.{json,png}（LFT2）。

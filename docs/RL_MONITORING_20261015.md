# EXPO 数据采集四图（2026-10-15）

用户指定：数据采集现有数据/控制区域下方，2×2：Q/TD target、Critic loss、自主成功率、单轮选中动作Q。窄屏单列。固定SFT/预览模式显示不在线学习；不伪造历史Q。

实现：只读 `/api/collection/rl-monitoring`，只解析已登记模型的在线目录及合法d/Edit组合；前端2秒轮询、迟到响应隔离、无数据空态和断连历史保留。数据与算法语义归rl-platform的monitoring.py。Web缓存仅保存回合HIL摘要，不改原始文件。模型/录制状态机保持。

实际口径：训练每次完整native update call取原生q/target_q_mean/critic_loss（原生最后inner critic统计），横轴实际critic step；最近600记录持久保存。成功率取最近20个有success/failure、非空且HIL身份可核验的完整尝试，接管成功留在分母但不计自主成功；放弃/未知/缺失HIL排除并显示数量。横轴为时间排序后的完成记录编号。单轮Q是现有候选选择的target-Q分数，非成功概率，也不代表动作的每一帧都已执行；最多1500点，保留最近记录回合，时间含暂停。

验证：214 EXPO CPU通过，4 GPU布局测试跳过；105完整前端通过，最终坐标格式/空态追加后3监控用例通过；本次监控+EXPO Web后端9通过，采集生命周期另8通过。旧test_real_saved_episode_labels_and_history_summary依赖的fake缺recorder，主分支也失败，本次未改该历史标签实现。Chromium桌面1280/窄屏500四图验证，无横向溢出或页面异常，布局样本为明确标注的合成数据，未写生产。

训练签名文件online_learner.py及原生source未改。独立observed_learner入口记录指标，Actor只读取已计算的qs[idx]，不增加Q前向或随机采样。当前进程不热注入；正式运行记录将在下次EXPO Online加载后产生。历史Q/loss不能追补，不把CPU/DOM测试称作真实在线收益或GPU延迟验收。

证据工作区：`/data/LFT-W02_data/jiaan/jiaan/scratch/cobot-web/rl-charts-20261015/`。现场安装与激活事实见后续交付记录。

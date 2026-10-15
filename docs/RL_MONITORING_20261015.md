# EXPO 数据采集四图（2026-10-15）

用户指定：数据采集现有数据/控制区域下方，2×2：Q/TD target、Critic loss、自主成功率、单轮选中动作Q。窄屏单列。固定SFT/预览模式显示不在线学习；不伪造历史Q。

实现：只读 `/api/collection/rl-monitoring`，只解析已登记模型的在线目录及合法d/Edit组合；前端2秒轮询、迟到响应隔离、无数据空态和断连历史保留。数据与算法语义归rl-platform的monitoring.py。Web缓存仅保存回合HIL摘要，不改原始文件。模型/录制状态机保持。

实际口径：训练每次完整native update call取原生q/target_q_mean/critic_loss（原生最后inner critic统计），横轴实际critic step；最近600记录持久保存。成功率取最近20个有success/failure、非空且HIL身份可核验的完整尝试，接管成功留在分母但不计自主成功；放弃/未知/缺失HIL排除并显示数量。横轴为时间排序后的完成记录编号。单轮Q是现有候选选择的target-Q分数，非成功概率，也不代表动作的每一帧都已执行；最多1500点，保留最近记录回合，时间含暂停。

验证：218 EXPO CPU通过（合入04d11d7夹爪更新后），4 GPU布局测试跳过；105完整前端通过，最终坐标格式/空态追加后3监控用例通过；本次监控+EXPO Web后端9通过，采集生命周期另8通过。旧test_real_saved_episode_labels_and_history_summary依赖的fake缺recorder，主分支也失败，本次未改该历史标签实现。Chromium桌面1280/窄屏500四图验证，无横向溢出或页面异常，布局样本为明确标注的合成数据，未写生产。

训练签名文件online_learner.py及原生source未改。独立observed_learner入口记录指标，Actor只读取已计算的qs[idx]，不增加Q前向或随机采样。当前进程不热注入；正式运行记录将在下次EXPO Online加载后产生。历史Q/loss不能追补，不把CPU/DOM测试称作真实在线收益或GPU延迟验收。

证据工作区：`/data/LFT-W02_data/jiaan/jiaan/scratch/cobot-web/rl-charts-20261015/`。现场安装与激活事实见后续交付记录。

## 当前交付状态

2026-10-15 网络恢复后已交付到 `agilex@10.12.166.118`，网页地址 `http://10.12.166.118:8015/`。现有 SSH 公钥可用，无认证配置变更。Web 源码4d8b02e、RL源码5ec409f及c731655，保留现场夹爪修正和其他任务的新地址启动脚本f7feb99。

安装前重新核验现场空闲、旧模型PID不存在和14个文件旧SHA；在模型操作锁内备份、安装12个运行文件及2个说明文件。仅重载Web，14文件及HTTP静态资源SHA校验通过。未启动模型、Learner、Session、相机、ROS或机器人运动，未改权重、Replay与学习配置。现场重启后保存的旧模型状态仍为error，采集idle。

实际网页只读核验通过：数据采集原有区域下方四卡片，页面无JavaScript异常，真实历史成功率曲线可见。当前batch5/d5/Edit0.01目录共6条完成记录，排除1条后5个有效尝试、2次自主成功（40%）；此为该目录历史统计，不代表其他任务或整个训练的成功率。Q、loss及单轮Q尚无历史遥测，正确显示等待；从下次EXPO Online加载后开始记录，真实GPU在线流尚待运行验证。

现场回执：`/home/agilex/jiaan/project/cobot-web/runtime/verification/rl-charts-20261015/{install,activation}.json`；LFT2证据：上述scratch根的`live-browser.json`、`live-panel.png`。浏览器验证拦截所有非GET控制请求，未触发机器人动作。交付后的说明更新另行校验，不覆盖其他任务文件。

2026-10-15 缓存修正：09846d1将index.html中unified_collection.js的查询版本从20261014-expo-start-history更新为20261015-rl-charts。已在Cobot备份旧入口并按SHA原子替换；实际HTTP200、HTML与新脚本内容校验通过。未重启后端或操作模型/硬件；其他电脑刷新后请求新脚本。证据runtime/verification/rl-charts-20261015/cache-version.json。

## 2026-10-15 独立 Learner 指标同步

原回传依赖Actor的SSH通道，释放模型后Web只保留第820步。新增受限monitor命令，只读已登记profile和d/Edit隔离目录内的status/telemetry；不获取owner锁、不启动Learner、不探测GPU、不改权重/Replay、不创建训练目录。Cobot Web后台独立SSH拉取、有超时与缓存；离线/失败保留历史，重新连接按远端实际历史恢复。浏览器分开显示Learner运行、训练停止和指标连接中断，不用Actor加载状态判断Learner存活。参数与model身份核验，旧PID复用不视为正在学习。

源码RL1daa10e、Web84e4d73；115项RL检查、5项Web后端、4项前端通过。现场5文件逐SHA安装、Python3.8编译、真实受限SSH及独立MonitoringReader通过：Actor offline时读取Critic1100/15points，Learner55，未启动模型或机器人。保留并行RLT发布改动和既有EXPO权重回传worker。证据：cobot-web/runtime/verification/rl-charts-20261015/independent/{install,ssh-snapshot,reader-acceptance}.json。

激活完成：等待并行RLT于19:19释放窗口后，仅重载Web106306→162962；保留已加载realtime-expo-rlt-online supervisor119227及Stage1/learner。22个模型/ROS/相机相关进程身份一致，Session始终disarmed/policy_paused，emitted_commands=0；临时三相机只读订阅正常退出。未启动或释放任何模型、未修改训练预算/Replay/权重。

正式HTTP接口在EXPO Actor未加载时connected=true/running=true，追到Critic1260/23points；随后实际生产JS+正式API的独立浏览器组件读取到1300/25points，显示“Learner运行中 · Actor未加载”，四图存在且无JS异常。这是同一真实Learner持续更新的记录，不是合成曲线。完整主页面目前选中并行任务的RLT模型，按既有模型过滤不显示EXPO图；组件验收使用实际catalogue和API，未改生产模型选择。现场回执independent/{reload,http-acceptance}.json；LFT2证据independent-component-browser.json与independent-component.png。模型释放后可持续查看EXPO学习指标；没有遗留测试进程或自动重启任务。

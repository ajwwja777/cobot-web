# EXPO Online 参数控件（2026-10-15）

用户要求在数据采集模型卡增加 d 与 Edit scale，并同步到真实在线学习。

已实现：LFT2 Learner / Cobot Actor 的现有 edit001 Online 入口增加加载参数。d 为 1–8 个 10Hz 逻辑步（100–800ms），默认5；Edit scale 为0.01–0.10、步长0.01，默认0.01。模型加载期间锁定两个控件。其他适配器保持原行为。

Web 校验并传入 COBOT_EXECUTION_OPTIONS.expo；RL 同一验证模块控制 Cobot Runtime、Actor 参数、受限 SSH 命令和 Learner 参数。远端配置须显式允许，固定目的地、端口及路径基准不变。Actor 与 Learner 的 d/scale 不一致会拒绝就绪。

默认参数使用原训练目录；非默认组合使用确定的 --dN-editNNN 后缀，分别保存训练状态、本地 journal 与 Actor 快照。原 checkpoint/Replay 不改写；同一组合恢复其自己的状态。未修改 native_online、online_learner、sampling_config 或其训练恢复源码签名，原默认配置恢复契约保持。

验证：136项RL CPU回归、10项Web后端、101项前端通过；80种组合均进入真实 native_online.configuration，N与n_edit_samples保持32。Chromium隔离模型卡渲染、选择与数值payload通过。未执行GPU训练、模型切换或机器人动作。

部署状态：待合并及现场SHA、HTTP核验。当前模型原有 Remote learner connection lost 故障不属于本次参数控件修复。

工作树：scratch/cobot-web/expo-online-options-20261015/{cobot-web,rl-platform}。详细测试/截图与现场回执位于同任务目录。

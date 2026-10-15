# EXPO Online 参数控件（2026-10-15）

用户要求在数据采集模型卡增加 d 与 Edit scale，并同步到真实在线学习。

已实现：LFT2 Learner / Cobot Actor 的现有 edit001 Online 入口增加加载参数。d 为 1–8 个 10Hz 逻辑步（100–800ms），默认5；Edit scale 为0.01–0.10、步长0.01，默认0.01。模型加载期间锁定两个控件。其他适配器保持原行为。

Web 校验并传入 COBOT_EXECUTION_OPTIONS.expo；RL 同一验证模块控制 Cobot Runtime、Actor 参数、受限 SSH 命令和 Learner 参数。远端配置须显式允许，固定目的地、端口及路径基准不变。Actor 与 Learner 的 d/scale 不一致会拒绝就绪。

默认参数使用原训练目录；非默认组合使用确定的 --dN-editNNN 后缀，分别保存训练状态、本地 journal 与 Actor 快照。原 checkpoint/Replay 不改写；同一组合恢复其自己的状态。未修改 native_online、online_learner、sampling_config 或其训练恢复源码签名，原默认配置恢复契约保持。

验证：136项RL CPU回归、10项Web后端、101项前端通过；80种组合均进入真实 native_online.configuration，N与n_edit_samples保持32。Chromium隔离模型卡渲染、选择与数值payload通过。未执行GPU训练、模型切换或机器人动作。

部署状态：Web ff2b41b、RL17c7a12 已合并/push；Cobot 11个源码/文档逐SHA校验及3个正式HTTP静态资源校验通过。两端profile已允许有界参数；现场独立导入新版后端与Learner启动命令构造通过。默认checkpoint训练源码签名保持一致。

网页后端尚未重载：模型475551暂停、末轮committed/finalized，但采集Session仍开启，等待用户确认结束空闲Session后仅重载网页。控件通过后端能力字段控制显示，旧后端期间不提交新字段。Actor及原训练数据保持。lft2 Learner1916528和SSH隧道已退出（原因未定），已提交记录12回合、Critic220/Actor11；这是本次修改前的独立故障，未自动重启训练。

补充：发布过渡保护回归12项通过（包含新增门禁测试）；此前完整前端101项通过。现场回执：cobot-web/runtime/verification/expo-online-options-20261015/install.json；lft2 task目录field-install.json、validation.json。

工作树：scratch/cobot-web/expo-online-options-20261015/{cobot-web,rl-platform}。详细测试/截图与现场回执位于同任务目录。

## 2026-10-15 后端已激活

用户明确授权重载后端。预检查末轮 committed/finalized、没有活动录制；结束空闲采集Session，再执行网页ui-down/up。Web492808→1155862，Runtime475551及8个采样模型/硬件进程身份保持，Sessioninactive。临时三相机订阅已退出，正式API三路相机ready。新版catalogue返回d5/edit0.01控件能力，刷新页面可见；模型加载期间参数锁定，释放后可选择。未卸载/加载Actor、启动Learner、训练或机器人运动。原有Learner断连故障保留，不算本次恢复。

续训只读核验：原默认参数checkpoint2共713文件、7,945,185,368bytes，逐SHA256全部通过；committed记录Actor11/Critic220/12回合/1924在线步。保持d5/edit0.01时原恢复契约和目录不变。此次未执行实际GPU恢复加载。

现场证据：cobot-web/runtime/verification/expo-online-options-20261015/{activation.json,reload-before.json,cameras-after.json,install.json}。

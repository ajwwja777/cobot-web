# Cobot 统一控制台可观测性设计

**日期：** 2026-09-20
**目标：** 将 8015 建成单一、清晰、实时的数据采集与 RLT 操作控制台，同时补齐回放定位、目录选择和在线学习可解释性。

## 约束

- 保持 FastAPI、原生 JavaScript、CSS 和本地静态资源，不引入 CDN、构建工具或前端框架。
- 页面和诊断读取不得进入 30 Hz 控制链，不发布 ROS/CAN 命令。
- 8015 仍是唯一公开入口；RLT 后端仍经 loopback 8016 代理。
- 普通采集与 RLT 共用视觉语言和路径选择器，但保持各自的动作权限和状态机。
- 后端失联、文件缺失、指标损坏时诊断应降级显示，不影响已有控制按钮。
- 页面必须明确：loss、Q 和离线门限不是实机成功率。

## 信息架构

页面采用固定顶栏和四个主视图：

1. **实时操作**：模式、状态摘要、三相机、当前 Session、主操作按钮、当前告警。
2. **Episode 回看**：历史选择、视频、可拖动时间轴、节点标记、关键帧和审核。
3. **在线学习**：距离下次更新的进度、最近候选结果、未更新原因、actor/critic/Q/采样指标曲线。
4. **系统状态**：ROS、相机、录制器、RLT lifecycle、模型 release、数据路径及原始错误。

顶栏持续显示连接状态、模式、Session phase、actor version 和数据目录；关键操作不因切换视图丢失上下文。

## 视频时间轴

- 使用浏览器原生 video 作为播放器，另加自定义 range 时间轴。
- 节点依据 episode 的 frame_index 和 source_fps 映射为秒。
- 节点在轨道上以可点击标记显示；悬停显示节点编号、时间和触发来源。
- 拖动滑块时，若指针落在节点前后 0.35 秒内，吸附到节点时间。
- 点击节点同步 video.currentTime，并更新三相机关键帧。
- 当前播放时间最近的节点获得高亮；键盘左右键细调，Shift+左右键跳至相邻节点。
- preview metadata 补充 source frame count 和 fps，若 metadata 不可用则以 video.duration 与 frame index 比例降级。
- 回放生成失败只影响回放卡片，不清空所选 episode 或节点。

## 目录选择

普通采集和 RLT 使用同一 PathPicker 组件：

- 输入焦点打开候选面板；输入完整或部分路径后调用现有受限目录 API。
- 候选分成“最近使用”和“匹配目录”；最近记录存在 localStorage，最多 12 条。
- 输入目录的一部分名称时列出同级前缀匹配；输入以斜杠结尾时列出子目录。
- 选中候选后自动补斜杠并立即查询下一层，形成连续浏览。
- 支持 ArrowUp/ArrowDown、Enter、Escape。
- 当前路径不存在时明确显示“将创建”，但只有用户点击检查/使用按钮才执行创建。
- RLT 路径选择继续受后端 allowlist 和 Session phase 限制。

## 在线学习可解释性

新增只读 `GET /api/console/diagnostics` 聚合以下证据：

- `online_cycle.json`：phase、pending、quarantined、episodes_per_update、最近批次 UUID。
- `operation.json`：preparing/training/accepted/rejected、更新数、transition 数、reasons。
- 当前 release manifest：actor/global step、发布门限、运行参数、parent release。
- release 的 `metrics.jsonl`：最近最多 240 个点，字段白名单化。
- ROS cache：双前臂关节位置/速度、policy command、freshness；不返回相机数组。
- 当前 Session：phase、paused、chunk count、inference latency、recoveries/fault（可用时）。

后端生成结构化 `update_explanation`：

- waiting + pending < batch：还需 N 条从未尝试且已验证的 episode。
- preparing/training：显示阶段、transition 和计划 updates。
- accepted：显示 actor 从 parent 到 current 的版本变化和通过的门限。
- rejected：列出每个门限原因；这些 UUID 会保留审计并从自动重试隔离。
- quarantined > 0：解释其含义，不把它们算作待更新样本。
- 后端不可用或文件缺失：标注 unavailable/stale，不伪造零值。

## 实时可视化

- 每 1 秒读取 diagnostics，500 毫秒读取采集状态；请求重叠时跳过，不堆积。
- actor/critic 图：critic loss、actor loss；零 actor loss 与 did_actor_update=0 联合解释为该步未更新 actor。
- Q 图：q1、q2、target Q。
- 数据构成图：human intervention、recent online、success、source base/RL/human。
- 运动图：右前臂六关节速度，及前端根据最近样本计算的速度 RMS、加速度 RMS、峰值变化，标明它们是观测诊断。
- 进度卡：pending / episodes_per_update、当前 update phase、最新 actor、最近 candidate。
- 页面保留最近 120 秒的机器人数据；训练历史来自持久化 metrics 文件。
- 图表采用本地 SVG，无第三方依赖，空数据呈现明确占位。

## 错误处理与安全

- diagnostics 的文件读取限制在固定 RLT run root；release 指向的 metrics 必须位于该 root。
- JSON/JSONL 逐项容错，过滤非有限数值，限制返回点数和 payload。
- 路径候选不能越过 allowed_data_root、不能跟随符号链接。
- 控制按钮仍完全由现有 rltButtons/buttonsFor 决定；诊断接口不改变权限。
- 页面请求异常以卡片级状态呈现；不使用全页错误遮挡现场操作。

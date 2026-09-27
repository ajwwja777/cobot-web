# Task5 Segmented Teach v1

这是从历史 Task5 v1 只读派生的独立 overlay。历史目录保持不变；本目录只负责插拔任务分段专家数据采集。

## 启动

前提是 Task2、三相机和示教按钮节点已由操作员按现场 SOP 启动。本服务不会启动 ROS launch、CAN、policy 或机械臂运动。

```bash
cd /media/agilex/Getea1/jiaan/projects/cobot-realworld-vla/task5/segmented-teach-v1/code
bash scripts/start_segmented_capture_v1.sh
```

网页：`http://10.7.165.64:8015/`

页面点击“开始 episode”后：

- 若任一后臂已在示教模式，立即开始写训练帧并创建 node1；否则创建 node1 后保持暂停。
- 任一已同步手臂退出示教会暂停采集并创建/合并节点。
- 任一手臂进入示教会恢复采集并创建/合并节点。
- 网页暂停/继续与示教按钮使用同一状态机；同目标事件合并。
- “只打节点”仅在录制态可用，不暂停采集。
- 结束总会创建 nodeN，原子提交 HDF5 与 sidecar。

节点时间轴和三相机快照直接显示在同一网页。暂停区间不写入 HDF5 训练帧。

episode 完成后，从“历史 episode”选择本条并点击“查看节点”。页面会列出所有可训练的
`节点 i → 节点 i+1` 区间；默认全部不选。勾选并保存后生成版本化
`segment-review.json` 及不可变 revision，原始 HDF5 和 capture sidecar 不会被改写。
marker 会真正切断训练区间，后续 action chunk/RLT transition 不得跨过去。

## 检查与停止

```bash
bash scripts/check_segmented_capture_v1.sh
bash scripts/stop_segmented_capture_v1.sh
```

停止脚本只处理 PID 文件中且命令行匹配本 app 的进程，不做宽泛 kill。

## 数据位置

- 快速 spool：`/home/agilex/cobot_magic/task3/jiaan/data/task5-segmented-teach/plug-cycle-v1/spool`
- 节点 sidecar：spool 下 `.segments/<task>/<model>/<round>/<episode_uuid>/`
- 大盘归档目标：`/media/agilex/Getea1/jiaan/projects/cobot-realworld-vla/datasets/raw/plug-cycle-v1`

当前不会自动删除 spool。达到容量门或批次结束后，必须先完成大盘 copy+checksum，再由独立清理授权释放 spool。

## 训练视图

`segmented_capture.converter.selection.load_selected_training_view()` 只读取人工审核勾选的
节点区间，并验证 HDF5 UUID、14D action 和帧数。`windows(horizon=...)` 会丢弃不足 horizon
的尾部，不跨 marker、暂停区间或 episode。该视图是 π0.5/RLT adapter 的输入门，不会把节点
自动解释成 reward。

专家示教节点只提供离线时间边界，不能直接识别新 rollout 当前处于哪个语义阶段。RLT 若要只
介入选定阶段，首版应使用独立、显式的操作员 phase gate；未来只有在阶段检测器完成真机验证后
才能自动化。phase gate 外执行冻结的 VLA reference，且不写 RLT transition。

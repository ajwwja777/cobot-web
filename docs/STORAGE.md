# 数据与 checkpoint 存放清单

2026-09-28实测快照。范围：Cobot新根、旧task3/task5/jiaan、旧外置盘jiaan；A6000的/data/LFT-W02_data/jiaan。未访问HPC，不把其他使用者的共享数据算作本项目资产。本轮完成盘点和路径登记，尚未执行新的去重删除或场景目录改名；上次迁移验收不等于已满足最新的单份存放要求。

## 用户确认的归属

- 原始采集数据、现场评测记录、当前部署checkpoint：长期单份放Cobot。
- 训练中间checkpoint、停止部署的历史模型：长期单份放A6000，同一资产不跨机器重复长期保存。
- 同一场景的数据可以供多个模型训练；数据集顶层按场景命名，模型、预处理版本和数据子集关系由配置/manifest记录。
- 固定warmup对照与可继续学习的online状态用途不同；原始HDF5与格式转换产物内容/格式也不同，不仅凭共同来源将其判为重复。迁移副本及归档内相同部署权重仍需收尾。
- 代码/Git、文档及路径索引按既有跨机同步约定维护；本规则针对数据和模型实体。项目MD注明机器、绝对路径、用途与版本。

## 磁盘空间

这是整个文件系统，不是本项目单独占用。avail取df可用空间，排除系统保留块。1 GiB=2^30字节，1 TiB=2^40字节。

| 机器 | 挂载点 | 总量 TiB | 已用 TiB | 可用 GiB | 使用率 |
|---|---|---:|---:|---:|---|
| A6000 | `/data` | 57.976 | 53.781 | 1315.47 | 98% |
| A6000 | `/` | 1.627 | 1.232 | 319.57 | 80% |
| Cobot | `/` | 1.804 | 1.637 | 77.62 | 96% |
| Cobot | `/media/agilex/Getea1` | 12.733 | 3.249 | 9712.29 | 26% |

## Cobot 数据

按du实际分配块统计，不跟随链接；父目录合计包含子行，不能重复相加。

| 内容 | 实际路径 | 占用 GiB | 说明 |
|---|---|---:|---|
| 插孔 v3 示范 | `/home/agilex/jiaan/data/rlt/plug_v3_yyshadow/demonstrations` | 52.822 | 实际路径，场景目录尚未改名 |
| 插孔 v3 warmup rollout | `/home/agilex/jiaan/data/rlt/plug_v3_yyshadow/warmup` | 81.492 | 实际路径，场景目录尚未改名 |
| 插孔 v3 online rollout | `/home/agilex/jiaan/data/rlt/plug_v3_yyshadow/online` | 0.312 | 实际路径，场景目录尚未改名 |
| 插孔 v2 历史数据 | `/home/agilex/jiaan/data/rlt/plug_v2` | 8.951 | 实际路径，场景目录尚未改名 |
| 插孔早期数据 | `/home/agilex/jiaan/data/rlt/plug` | 0.757 | 实际路径，场景目录尚未改名 |
| 旧 test 录制 | `/home/agilex/jiaan/data/test` | 1.944 | 实际路径，场景目录尚未改名 |
| 旧 record 录制 | `/home/agilex/jiaan/data/record` | 0.916 | 实际路径，场景目录尚未改名 |
| 旧平台录制 | `/home/agilex/jiaan/data/cobot-platform` | 0.021 | 实际路径，场景目录尚未改名 |
| 评测记录 | `/home/agilex/jiaan/data/evaluations` | 0.013 | 实际路径，场景目录尚未改名 |
| 新数据根合计 | `/home/agilex/jiaan/data` | 147.229 | 包含上面各项，不重复相加 |
| in_the_pot 数据及 LeRobot | `/home/agilex/cobot_magic/task3/jiaan/datasets/in_the_pot` | 0.404 | 旧位置，尚未归入新场景根 |
| in_the_pot 旧 RL 数据 | `/home/agilex/cobot_magic/task3/jiaan/realworld_rl/data/task5-rlt-r1/in_the_pot` | 1.162 | 旧位置，尚未归入新场景根 |
| in_the_pot DAgger 原始 rollout | `/home/agilex/cobot_magic/task5/jiaan/hil_realworld_rl/data/raw_rollouts/in_the_pot` | 6.070 | 旧位置，尚未归入新场景根 |
| in_the_pot DAgger LeRobot | `/home/agilex/cobot_magic/task5/jiaan/hil_realworld_rl/data/lerobot/in_the_pot` | 0.466 | 旧位置，尚未归入新场景根 |
| 旧失败录制排障材料 | `/home/agilex/cobot_magic/task5/jiaan/hil_realworld_rl/v1/runtime/failed_recordings` | 0.461 | 不可直接当作可训练数据 |

## Cobot checkpoint 与基础模型

| 内容 | 实际路径 | 占用 GiB | 说明 |
|---|---|---:|---|
| RLT Stage 1 Reference | `/home/agilex/jiaan/project/rl-platform/models/rlt/plug_v3_yyshadow/stage1/4999` | 14.369 | 当前现场部署；冻结对照与在线工作状态是不同资产 |
| RLT 固定 warmup 5000 | `/home/agilex/jiaan/project/rl-platform/models/rlt/plug_v3_yyshadow/warmup-5000` | 0.037 | 当前现场部署；冻结对照与在线工作状态是不同资产 |
| RLT 可更新在线权重 | `/home/agilex/jiaan/project/rl-platform/models/rlt/plug_v3_yyshadow/online` | 0.040 | 当前现场部署；冻结对照与在线工作状态是不同资产 |
| deployments/in_the_pot/galaxea_g0_5/checkpoints/step_4000 | `/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/galaxea_g0_5/checkpoints/step_4000` | 11.149 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| deployments/in_the_pot/galaxea_g0_5_dagger_round001/checkpoints/step_4000 | `/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/galaxea_g0_5_dagger_round001/checkpoints/step_4000` | 10.655 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| deployments/in_the_pot/lingbot_v2/checkpoints/step_2000 | `/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/lingbot_v2/checkpoints/step_2000` | 23.767 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| deployments/in_the_pot/pi05/checkpoints/step_2000 | `/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05/checkpoints/step_2000` | 11.587 | 当前网页使用 |
| deployments/in_the_pot/xiaomi_robotics_0/checkpoints/step_4000 | `/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/xiaomi_robotics_0/checkpoints/step_4000` | 10.234 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| deployments/in_the_pot/xiaomi_robotics_1/checkpoints/step_4000 | `/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/xiaomi_robotics_1/checkpoints/step_4000` | 10.249 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| deployments/lift_book/pi05/checkpoints/step_2000 | `/home/agilex/cobot_magic/task3/jiaan/deployments/lift_book/pi05/checkpoints/step_2000` | 11.587 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| deployments/put_two_fruits/pi05/checkpoints/step_2000 | `/home/agilex/cobot_magic/task3/jiaan/deployments/put_two_fruits/pi05/checkpoints/step_2000` | 11.587 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| task3/jiaan/lingbot_v2/checkpoints/global_step_4000 | `/home/agilex/cobot_magic/task3/jiaan/lingbot_v2/checkpoints/global_step_4000` | 23.767 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| task3/jiaan/pi05/checkpoints/step_4999 | `/home/agilex/cobot_magic/task3/jiaan/pi05/checkpoints/step_4999` | 11.587 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| deployments/in_the_pot/pi05_dagger_round001/checkpoints/step_3000 | `/home/agilex/cobot_magic/task5/jiaan/hil_realworld_rl/deployments/in_the_pot/pi05_dagger_round001/checkpoints/step_3000` | 11.587 | 当前网页使用 |
| projects/fluxvla-cobot-platform/pi05/checkpoints/step_5000 | `/media/agilex/Getea1/jiaan/projects/fluxvla-cobot-platform/pi05/checkpoints/step_5000` | 13.474 | 旧模型；待核定是否仍需现场常驻，不按大小直接判重 |
| Lingbot 依赖 Qwen3-VL-4B | `/home/agilex/cobot_magic/task3/jiaan/lingbot_v2/runtime/pretrained/Qwen3-VL-4B-Instruct` | 8.277 | 独立于微调checkpoint |

put_two_fruits/pi05/checkpoints/step_4999是到旧jiaan/pi05/checkpoints/step_4999的软链接，不另算一份。空step占位目录未当作有效checkpoint列入。

旧Cobot Xiaomi DAgger step_4000/last.ckpt仍指向已退休平台路径，是失效的历史入口；权重实体已保全于下面A6000 vla-platform/models/history，索引configs/assets/legacy_cobot_models.json。它不属于当前网页六模型入口，再次现场使用前需要按新位置接入。

## A6000 checkpoint、数据与实验历史

| 内容 | 实际路径 | 占用 GiB | 说明 |
|---|---|---:|---|
| 当前Stage1异机副本 | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/models/rlt/plug_v3_yyshadow/stage1/4999` | 14.369 | 当前仍存在，部署副本待按单份规则收尾 |
| warmup部署迁移副本 | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/models/rlt/plug_v3_yyshadow/warmup-5000` | 0.071 | 当前仍存在，部署副本待按单份规则收尾 |
| 历史legacy40 Stage1 | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/models/history/stage1-legacy40/4999` | 14.369 | 当前仍存在，部署副本待按单份规则收尾 |
| 历史e78 Stage1 2000 | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/models/history/stage1-plug-e78/step_2000` | 14.369 | 当前仍存在，部署副本待按单份规则收尾 |
| 历史e78 Stage1 4000 | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/models/history/stage1-plug-e78/step_4000` | 14.369 | 当前仍存在，部署副本待按单份规则收尾 |
| 历史plug v2 Stage1 | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/models/history/stage1-plug-v2/3999` | 6.062 | 当前仍存在，部署副本待按单份规则收尾 |
| 历史DM0-5 step4000 | `/data/LFT-W02_data/jiaan/jiaan/projects/vla-platform/models/history/dm0-5/step_4000` | 10.889 | 已归入所属项目 |
| 历史Xiaomi DAgger step4000 | `/data/LFT-W02_data/jiaan/jiaan/projects/vla-platform/models/history/xiaomi-robotics-1-dagger-round001/step_4000` | 10.249 | 已归入所属项目 |
| RLT历史转换数据 | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/data/history/legacy-rlt` | 0.568 | 重复关系需按内容/格式核验 |
| 旧RLT示范转换数据 | `/data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/data` | 1.126 | 重复关系需按内容/格式核验 |
| 旧RLT样例数据 | `/data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/fixtures` | 0.393 | 重复关系需按内容/格式核验 |
| 旧插孔原始数据副本 | `/data/LFT-W02_data/jiaan/projects/proj-20260829-cobot-realworld-vla/scratch/plug-stage1-prep-20260909/cobot-raw` | 34.588 | 重复关系需按内容/格式核验 |
| 旧A6000 Stage1工作副本 | `/data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/checkpoints/plug_v3_yyshadow/stage1-faithful-s42/4999` | 14.369 | 待去重；不按同名/大小单独判定 |
| 旧插孔Stage1 2000转移副本 | `/data/LFT-W02_data/jiaan/projects/proj-20260829-cobot-realworld-vla/scratch/plug-rlt-stage1-deployment-20260909/step_2000` | 14.369 | 待去重；不按同名/大小单独判定 |
| 旧插孔Stage1 4000转移副本 | `/data/LFT-W02_data/jiaan/projects/proj-20260829-cobot-realworld-vla/scratch/plug-rlt-stage1-deployment-20260909/step_4000` | 14.369 | 待去重；不按同名/大小单独判定 |
| 旧XR1 DAgger转移副本 | `/data/LFT-W02_data/jiaan/projects/proj-20260829-cobot-realworld-vla/scratch/xr1-dagger-hpc-587328/final-transfer` | 10.249 | 待去重；不按同名/大小单独判定 |
| 完整历史RLT实验runs | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/outputs/migrations/20260927-rlt/legacy-rlt-source/runs` | 45.690 | 包含checkpoint/Replay/指标，不是纯权重大小 |
| 新位置warmup对比历史 | `/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/outputs/rlt/plug_v3_yyshadow/history` | 1.957 | 包含checkpoint/Replay/指标，不是纯权重大小 |
| 旧warmup对比历史 | `/data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/runs/plug_v3_yyshadow` | 1.957 | 包含checkpoint/Replay/指标，不是纯权重大小 |
| 旧real-batch smoke | `/data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/runs/real-batch-smoke` | 11.725 | 包含checkpoint/Replay/指标，不是纯权重大小 |
| 旧Stage1 production preflight | `/data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/runs/stage1-production-preflight` | 20.773 | 包含checkpoint/Replay/指标，不是纯权重大小 |
| 旧Stage1调试实验 | `/data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/runs/stage1` | 4.184 | 包含checkpoint/Replay/指标，不是纯权重大小 |
| π0.5 基础模型 | `/data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/assets/pi05_base` | 11.587 | 旧训练依赖，归A6000 |

## 重复与待核验项

- 新A6000 RL Stage1/4999与Cobot同模型已有跨机SHA一致证据，约14.37GiB异机副本待收尾；旧A6000 scratch另有同名同体积Stage1，需确认具体版本和引用。
- 新RL outputs/rlt/plug_v3_yyshadow/history承接旧warmup对比历史，此前1,731文件已核验；旧scratch仍有约1.96GiB对应实验树。
- 旧XR1 DAgger final-transfer的大权重与新VLA历史模型已有一致SHA证据，旧转移副本约10.25GiB待收尾。
- 历史Stage1 2000/4000、Lingbot两份权重及原始采集副本仍需按内容和用途比对；同名或同大小不单独证明相同。
- 历史RLT runs约45.69GiB，包含checkpoint/Actor、Replay和指标，不能整棵当作缓存。归档里的相同部署快照按资产来源和哈希索引逐项归并。

## 场景命名目标（尚未切换）

场景根建议为Cobot /home/agilex/jiaan/data/in_the_pot/、/home/agilex/jiaan/data/plug_insertion/。场景下按原始采集批次、转换格式/版本及评测分开；相机/动作定义不兼容的批次保留版本边界，不混并。

当前实际路径仍以上表为准，rlt/plug_v3_yyshadow、rlt/plug_v2及旧task3/task5目录尚未改名。模型通过manifest选择episode、标签与比例，不为每个模型复制同场景数据。

## 来源

[路径与字节CSV](storage-inventory-20260928.csv)包含本清单及建议归属。完整du/链接盘点在A6000 rl-platform/outputs/storage/20260928-inventory/。迁移校验证据沿rl-platform/docs/MIGRATION.md、vla-platform/docs/MIGRATION.md和outputs/migrations查阅。

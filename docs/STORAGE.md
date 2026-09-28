# Cobot 数据与模型存储

2026-09-28 用户确认：Cobot 的数据与 checkpoint 全部放 Getea1；代码、环境、日志和 PID 留在系统盘项目目录。A6000 维护主代码、Git、文档；本轮不新增数据或权重备份。此前已存在的 A6000 历史资产仍保留，不能把本轮迁移说成全机器已经去重。

**状态：主体资产已迁移并验收清理；Getea1 随后 USB 掉线，FluxVLA 补充迁移和最终清理未完成。网页已停止。恢复连接后需重新校验，详见本页末尾。**

## 两个入口

- 数据：/media/agilex/Getea1/jiaan/data/
- 模型：/media/agilex/Getea1/jiaan/model/
- 现场代码与环境：/home/agilex/jiaan/project/<项目>/
- A6000 主代码：/data/LFT-W02_data/jiaan/jiaan/projects/<项目>/

Getea1/jiaan 顶层只保留 data、model。模型不混进 data，环境不混进 model。datasets 表示“按场景管理的数据集”，既包含自采数据，也可包含以后下载的数据；不是“下载数据专用目录”。

## 数据：场景 → 用途 → 项目或方法 → 批次

    data/
      datasets/
        plug_insertion/
          recordings/
            demonstrations/three_camera_v3/
            demonstrations/manual/
            demonstrations/legacy_v1/
            demonstrations/legacy_v2/
            demonstrations/legacy_record/
            demonstrations/legacy_test/
            rl-platform/rlt/warmup/three_camera_v3/
            rl-platform/rlt/online/three_camera_v3/
          lerobot/
          derived/rl-platform/rlt/
            replay_clean_v1/
            traces/
        in_the_pot/
          recordings/
            demonstrations/legacy/
            cobot-dagger/round_001/
            rl-platform/rlt/legacy/
          lerobot/
          derived/vla-platform/fluxvla/fixtures/
      evaluations/
        plug_insertion/rl-platform/rlt/<reference_4999或warmup_5000等>/<日期>/<eval-id>/
        in_the_pot/vla-platform/pi05/<模型版本>/<日期>/<eval-id>/
      motion/
        poses/home_poses.yaml
        replays/

共享 demonstrations 不按模型复制。RLT 专用 rollout 放 rl-platform/rlt；后续其他算法放自己的方法目录。LeRobot 是格式转换产物，derived 是 Replay、诊断输入等派生产物，均不能因为源数据相同就当作重复删掉。旧相机或动作定义不同的批次不拼在一起。

motion/replays 是可回放的机械臂动作；derived/.../replay_clean_v1 是 RL 训练经验池，两者不是同一功能。位姿唯一现场读写源是 motion/poses/home_poses.yaml；cobot-control/configs/home_poses.example.yaml 仅是 Git 中的初始参考。

历史 trial 的 result.json、HDF5、标签和训练 provenance 保留原内容；读取端按实际所在目录生成媒体链接。新增已登记模型的评测会自动按场景、项目、版本和日期落盘，网页仍可查看汇总。

## 模型：项目 → 模型或算法 → 场景 → 版本

    model/
      rl-platform/rlt/
        base/openpi/
        plug_insertion/
          reference_4999/
          warmup_5000/
          online/
          history/
      vla-platform/
        pi05/
          in_the_pot/baseline_2000/
          in_the_pot/dagger_2000plus3000/
          put_two_fruits/
          lift_book/
        fluxvla_pi05/
          base/pi05_base/
          in_the_pot/step_5000/
        galaxea_g0_5/
        lingbot_v2/
        xiaomi_robotics_0/
        xiaomi_robotics_1/

π0.5 DAgger 为原 step 2000 初始化、再训练 3000 步。固定 warmup_5000 与持续更新的 online 分开；不能用后续在线结果冒充固定 warmup 的成绩。一个基础模型只保留一份实体，通过配置引用。

## 配置与命令

现场主机配置：/home/agilex/jiaan/project/cobot-web/configs/local.json；其版本化模板为 configs/hosts/cobot.json。RLT 的配置和发布 manifest 位于 rl-platform/configs/rlt/plug_v3_yyshadow/。路径变更不改变 warmup、奖励、动作、归一化或更新比例。

普通采集示例目录：/media/agilex/Getea1/jiaan/data/datasets/plug_insertion/recordings/demonstrations/manual。
在线 RLT：/media/agilex/Getea1/jiaan/data/datasets/plug_insertion/recordings/rl-platform/rlt/online/three_camera_v3。
评测保存入口：/media/agilex/Getea1/jiaan/data/evaluations。

完整命令见 [COMMAND_LINE.md](COMMAND_LINE.md)，网页故障恢复见 [WEB_RECOVERY.md](WEB_RECOVERY.md)，RLT 操作见相邻 rl-platform/docs/RUNBOOK.md。旧网页保存的已登记路径通过配置映射到新位置，不创建第二份数据。

## 硬盘不可用时

挂载点 /media/agilex/Getea1，登记 UUID 为 3A0A7A0E0A79C801。采集、模型加载、归位配置写入和回放录制会检查实际挂载；缺盘、错盘或只读时停止该操作，不退回系统盘创建同名目录。

只读检查：

    findmnt -T /media/agilex/Getea1 -o TARGET,SOURCE,UUID,OPTIONS
    df -h / /media/agilex/Getea1
    python3 /home/agilex/jiaan/project/cobot-control/robot/asset_storage.py /media/agilex/Getea1/jiaan/data --write

网页 UI 重启不能修复磁盘 I/O 故障。先停止受影响的采集/推理任务，保留终端报错，再处理连接与挂载；不要在任务仍写盘时直接拔盘。

## 迁移证据

Cobot：/home/agilex/jiaan/project/rl-platform/outputs/migrations/20260928-getea-storage/。
A6000：/data/LFT-W02_data/jiaan/jiaan/projects/rl-platform/outputs/migrations/20260928-getea-storage/。
plan.json 是旧根到新根的映射，copied-files.jsonl 保存逐文件 SHA-256；extras 保存 FluxVLA 和 tokenizer 等补充迁移。

本批之前的两机路径、体积和历史重复项保留在 [storage-inventory-20260928.csv](storage-inventory-20260928.csv)，它是迁移前快照，不是当前路径表。A6000 原有历史权重和训练资料未因本轮自动删除；没有新增权重备份。

## 2026-09-28 20:00：迁移中遇到 Getea1 USB 掉线

已完成主体 12,059 条目、351,844,176,546 字节及 6 个恢复验证资产、117,047,594 字节的迁移、SHA 校验、运行验收和对应源文件清理。Warmup 与在线模型在新路径加载/释放通过；在线状态 5000/2500/2567，正式权重和 Replay 的 SHA 不变，未启动 Episode 或真机运动。历史读取、92 条有效评测和媒体通过；主副本清理后再次读通。系统盘当时剩余约 404 GiB。

剩余 FluxVLA 环境复制到 libcublasLt.so.12 时出现 I/O error。内核在 19:59:53 将 sda 下线，随后 USB 设备枚举失败；20:00 检查已无 Getea1 块设备和挂载。不能把它归因于单个 Python 包或仅网页错误，也不能仅凭这些日志判定是线缆、供电、硬盘盒或盘本体。

所有迁移进程已退出；正式网页 PID 366090 正常停止，无 GPU 模型进程，临时 ROS master 已停止。本轮未做运动。尚未验收的 FluxVLA 旧目录、暂存副本未清理，**Getea1/jiaan 仅保留 data/model 的目标尚未完成**。已验证结果仅代表掉线前状态，恢复连接后仍须核对文件系统并按迁移收据重新校验新资产，不能直接继续删除或开始在线训练。

证据：相邻 rl-platform/outputs/migrations/20260928-getea-storage/cobot/，现场同目录不带 cobot/。包括 retirement.json、validation/retirement.json、cutover-verification.json、extras/copy-status.json、disk-disconnect.json 和 disk-disconnect-kernel.log。源码和证据位于系统盘/A6000，本轮没有新增 A6000 数据/权重备份。

# 安装与验收

结构见ARCHITECTURE.md。A6000主仓库 /data/LFT-W02_data/jiaan/jiaan/projects/cobot-web；现场 /home/agilex/jiaan/project/cobot-web；GitHub https://github.com/ajwwja777/cobot-web。

五个仓库放相邻目录。web安装自己的Python3.8环境，不混装Flux/RLT的GPU环境。机器模板填写为configs/local.json，替换项目根、ROS setup、存储位置、UUID与模型路径，不复制另一机器的runtime PID或活动episode状态。

```bash
cd /home/agilex/jiaan/project/cobot-web
./scripts/install.sh
./scripts/ui_up.sh
./scripts/ui_status.sh
.venv/bin/python scripts/models.py list
.venv/bin/python scripts/models.py check plug-v3-warmup-5k
.venv/bin/python scripts/models.py status
.venv/bin/python scripts/models.py logs
```

models.py的list/check/status/logs只读；load只加载，不自动开始Episode；start/resume会允许模型动作，须由现场操作者手动执行。网页和CLI使用同一个ManagedRuntime，文件锁防止重复启动，PID+start_ticks核对身份。unload只终止所属进程组。外部脚本须前台运行，子进程不能脱离组。

```bash
# 确认现场状态后手动加载与释放：
.venv/bin/python scripts/models.py load plug-v3-warmup-5k
.venv/bin/python scripts/models.py unload
./scripts/ui_down.sh
cd /home/agilex/jiaan/project/cobot-control
/usr/bin/python3 scripts/control.py status
```

ui_down仅关闭API，不关闭独立硬件任务。HTTP超时先看状态和日志，避免重复提交结果；刷新只重连，不能修复磁盘/ROS/USB。正在录制先核对状态、保存或放弃，结束Session后再重启网页。完整按钮/终端流程见COMMAND_LINE.md。

数据与权重不移动：/media/agilex/Getea1/jiaan/{data,model}。主代码先测试、commit/push，再scripts/sync_cobot.py核验同步，脚本不重启设备。

验证：后端与DOM回归、无模型历史HTTP与JPEG读取；本批没有可连接的浏览器，未完成截图验收；真实HIL、成功率及新一轮在线更新待现场验收。

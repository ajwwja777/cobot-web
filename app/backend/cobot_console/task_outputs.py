"""Read-only command/log inventory and identity-checked task interruption."""
import os
import shlex
import time
import re
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from .deployment import PLATFORM, RUNTIME, RUN, DeploymentError, process_identity, read_json, tail
from .device_control import DeviceControlError


class StopTaskRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    component: str
    pid: int
    start_ticks: int
    model_pid: Optional[int] = None
    model_start_ticks: Optional[int] = None


def command_text(argv):
    return ' '.join(shlex.quote(str(part)) for part in (argv or []))


def process_details(row):
    pid = int(row.get('pid') or -1)
    ticks = process_identity(pid) if pid > 0 else None
    result = dict(pid=pid if pid > 0 else None, start_ticks=ticks, process_command='', cwd=str(PLATFORM), pgid=None)
    if ticks is not None:
        try:
            raw = (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
            result['process_command'] = command_text([x.decode(errors='replace') for x in raw if x])
            result['cwd'] = os.readlink('/proc/%s/cwd' % pid)
            result['pgid'] = os.getpgid(pid)
        except OSError:
            pass
    result['command_text'] = command_text(row.get('command'))
    result['stop_command'] = 'kill -INT -- -%s' % pid if ticks is not None and result['pgid'] == pid else ''
    return result


def common_commands():
    scripts = str(PLATFORM / 'scripts')
    cli = 'python3 ' + shlex.quote(str(PLATFORM / 'scripts/console.py')) + ' '
    ros = 'source /home/agilex/cobot_magic/Piper_ros_private-ros-noetic/devel/setup.bash\n'
    def can_command(action):
        return "read -rsp 'sudo 密码: ' cobot_password\nprintf '\\n'\nprintf '%s\\n' \"$cobot_password\" | "+scripts+"/can_web.sh "+action+"\nunset cobot_password"
    return [dict(label=label, command=command, group=group) for group, label, command in [
        ('机械臂','启动五臂节点', scripts+'/arms_up.sh'),
        ('机械臂','查看节点和 PID', ros+'rosnode list\nps -eo pid,pgid,stat,args | grep -E "[r]oslaunch.*arms.launch|[p]iper_start_ms_node|[p]iper_rear_teach"'),
        ('机械臂','查看发布者和订阅者', ros+'rostopic list'),
        ('相机','启动三相机', scripts+'/cameras_up.sh'),
        ('相机','左相机帧率', ros+'rostopic hz /camera_l/color/image_raw'),
        ('相机','顶部相机帧率', ros+'rostopic hz /camera_f/color/image_raw'),
        ('相机','右相机帧率', ros+'rostopic hz /camera_r/color/image_raw'),
        ('相机','查看进程与设备', 'ps -eo pid,pgid,stat,args | grep -E "[m]ulti_camera_shuai|[a]stra_camera_node"\nlsusb'),
        ('CAN','配置五臂 CAN', can_command('configure')),
        ('CAN','重置 CAN · 1 Mbps', can_command('reset')),
        ('CAN','查看链路与错误计数', 'ip -details -statistics link show'),
        ('CAN','查看 USB / CAN 内核日志', 'sudo dmesg --ctime | tail -n 80'),
        ('ROS','启动 ROS', scripts+'/roscore_up.sh'),
        ('ROS','查看节点', ros+'rosnode list'),
        ('ROS','查看服务', ros+'rosservice list'),
        ('归位','前后四臂 · plug2', scripts+'/home.sh all --pose plug2'),
        ('归位','五臂 · plug2', scripts+'/home.sh selected --targets front-left,front-right,rear-left,rear-right,mid --pose plug2'),
        ('归位','前双臂 · plug2', scripts+'/home.sh front --pose plug2'),
        ('归位','后双臂 · plug2', scripts+'/home.sh rear --pose plug2'),
        ('归位','中臂 · plug2', scripts+'/home.sh mid --pose plug2'),
        ('归位','四臂 · origin', scripts+'/home.sh all --pose origin'),
        ('归位','夹爪复位', scripts+'/home.sh gripper --pose reinit'),
        ('位姿','查看前双臂位姿', scripts+'/home.sh show --arm front --pose plug2'),
        ('位姿','查看中臂位姿', scripts+'/home.sh show --arm mid --pose plug2'),
        ('位姿','记录前双臂 · new_pose', scripts+'/home.sh capture --arm front --pose new_pose'),
        ('位姿','记录中臂 · new_pose', scripts+'/home.sh capture --arm mid --pose new_pose'),
        ('恢复','前双臂恢复', scripts+'/recover.sh front-pair'),
        ('恢复','右前臂恢复', scripts+'/recover.sh front-right'),
        ('恢复','左前臂恢复', scripts+'/recover.sh front-left'),
        ('恢复','中臂恢复', scripts+'/recover.sh mid'),
        ('恢复','左夹爪恢复', scripts+'/recover.sh gripper-left'),
        ('恢复','右夹爪恢复', scripts+'/recover.sh gripper-right'),
        ('部署','加载 Reference（共享模型）', cli+'model load --id plug-v3-reference'),
        ('部署','加载固定 Actor（共享模型）', cli+'model load --id plug_v3-frozen-latest'),
        ('部署','加载在线学习模型', cli+'model load --id plug_v3-online-latest'),
        ('部署','查看可用模型及路径', cli+'model list'),
        ('部署','等待模型加载完成', cli+'model wait --seconds 600'),
        ('部署','开始采集 Session', cli+'model session-start'),
        ('部署','结束采集 Session', cli+'model session-stop'),
        ('部署','查看模型 / Session / 版本', scripts+'/rlt_v3_status.sh'),
        ('部署','查看部署状态', "curl --noproxy '*' -fsS http://127.0.0.1:8015/api/deployment/status | python3 -m json.tool"),
        ('部署','暂停策略', cli+'recovery pause'),
        ('部署','释放共享模型', cli+'model unload'),
        ('数采','当前采集状态', cli+'state capture'),
        ('数采','暂停并打节点', cli+'capture pause'),
        ('数采','继续并打节点', cli+'capture resume'),
        ('数采','只打节点', cli+'capture marker'),
        ('数采','结束保存（未标注）', cli+'--timeout 120 capture save'),
        ('数采','结束放弃（删除本轮）', cli+'capture discard'),
        ('网页','启动网页', scripts+'/ui_up.sh'),
        ('网页','关闭网页', scripts+'/ui_down.sh'),
        ('网页','网页状态', scripts+'/ui_status.sh'),
        ('诊断','进程、身份与服务状态', cli+'recovery status'),
        ('诊断','保存故障现场', cli+'recovery snapshot'),
        ('诊断','预览模型中断范围（不执行）', cli+'recovery interrupt model'),
        ('诊断','命令行完整手册', 'less '+shlex.quote(str(PLATFORM/'docs/COMMAND_LINE.md'))),
        ('诊断','查看 GPU', 'nvidia-smi'),
        ('诊断','磁盘与内存', 'df -h / /home/agilex/jiaan\nfree -h'),
        ('诊断','查看端口占用', 'ss -ltnp'),
    ]]


def important_output(component, phase, raw, systems):
    """Keep faults and transitions visible; full raw output remains selectable."""
    if component not in ("arms", "cameras", "roscore"):
        return None
    ready = systems.get(component, {}).get("phase") == "ready"
    titles = {"arms": ("机械臂", "Arms"), "cameras": ("相机", "Cameras"), "roscore": ("ROS", "ROS")}
    zh, en = titles[component]
    status_zh = "节点已就绪" if ready else "节点未就绪，请检查输出"
    status_en = "nodes ready" if ready else "nodes not ready; inspect output"
    relevant = []
    repeated = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # ROS Noetic's non-atomic latest symlink race is fixed in our launcher.
        # It is startup bookkeeping, not an arm launch error. Raw view retains it.
        if (ready and line.startswith("INFO: cannot create a symlink to latest log directory")
                and re.search(r"\[Errno (2|17)\]", line)):
            continue
        if re.search(r"error|fatal|traceback|exception|failed|failure|cannot|permission denied|no space|couldn.t|corrupt|not a jpeg|\bwarn(?:ing)?\b|故障|失败|错误|就绪|启动|takeover|示教|恢复|home|homed", line, re.I):
            if re.fullmatch(r"\[?WARN(?:ING)?\]?[:\s\d.,-]*", line):
                continue
            if line in repeated:
                repeated[line] += 1
            else:
                repeated[line] = 1
                relevant.append(line)
    lines = [line + (" [x%d]" % repeated[line] if repeated[line] > 1 else "") for line in relevant[-40:]]
    return {"zh": zh + "：" + status_zh + "\n" + "\n".join(lines),
            "en": en + ": " + status_en + "\n" + "\n".join(lines)}


class TaskOutputs:
    def __init__(self, manager, devices):
        self.manager, self.devices = manager, devices

    def snapshot(self, history=False):
        rows = []
        devices = self.devices.status()
        for component, job in devices['jobs'].items():
            row = {'id':job.get('job_id', component), 'component':component, **job}
            if not row.get('log_path'):
                logs = sorted(self.devices.runtime.glob(component+'-*.log'), key=lambda p:p.stat().st_mtime, reverse=True)
                if logs:
                    row.update(log_path=str(logs[0]), log_tail=tail(logs[0]))
                    if row.get('phase') == 'invalid':
                        row['phase'] = 'previous'
                elif row.get('phase') == 'invalid':
                    continue
            row.update(process_details(row))
            row['can_stop'] = bool(row.get('owned') and row.get('phase') in ('running','stopping')
                and row['start_ticks'] is not None and row['pgid'] == row['pid']
                and (job.get('start_ticks') is None or job['start_ticks'] == row['start_ticks']))
            row['stop_kind'] = 'task'
            row['important_output'] = important_output(component, row.get('phase'), row.get('log_tail', ''), devices.get('systems', {}))
            rows.append(row)
        state = self.manager.status()
        if state.get('log_path'):
            row = {'id':'deployment', 'component':'deployment', **state}
            row.pop('models',None)
            row['command'] = [str(PLATFORM/'scripts/deployment_run.sh'),state['model']['id']]
            if state['model']['kind']=='rlt':
                row['command'] += [state['model']['checkpoint'], str(RUNTIME/'evaluation.yaml')]
            row.update(process_details(row))
            row.update(can_stop=row['start_ticks'] is not None and row['start_ticks']==state.get('start_ticks') and state['phase']!='offline',
                       stop_kind='model',model_pid=state['pid'],model_start_ticks=state['start_ticks'])
            rows.append(row)
        model = read_json(RUN/'model-server/process.json')
        if model.get('log'):
            row = {'id':'stage1-loader','component':'stage1',**model,'log_path':model['log'],'log_tail':tail(model['log'])}
            row.update(process_details(row))
            row['phase'] = 'running' if row['start_ticks'] is not None else 'stopped'
            row.update(can_stop=bool(state.get('pid') and state.get('phase')!='offline' and row['start_ticks'] is not None
                                    and self.manager.runtime._alive(state)),
                       stop_kind='model', model_pid=state.get('pid'), model_start_ticks=state.get('start_ticks'))
            rows.append(row)
        for log in sorted((RUNTIME/'pi05/logs').glob('rtc_policy_server_*.log'), reverse=True)[:1]:
            rows.append(dict(id='pi05-loader',component='pi05',phase='model',log_path=str(log),log_tail=tail(log),can_stop=False))
        known={row.get('log_path') for row in rows}
        for log in (sorted(self.devices.runtime.glob('*.log'), key=lambda p:p.stat().st_mtime, reverse=True)[:30] if history else []):
            if str(log) not in known:
                rows.append(dict(id='history-'+log.stem,component=log.stem.rsplit('-',1)[0],phase='archived',started_at=log.stat().st_mtime,
                                 log_path=str(log),log_tail=tail(log),can_stop=False))
        rows.sort(key=lambda row:float(row.get('started_at') or 0), reverse=True)
        commands=common_commands()
        group_names={'arms':'机械臂','cameras':'相机','can':'CAN','roscore':'ROS','home':'归位','pose':'位姿','recover':'恢复','rlt':'部署','deployment':'部署','stage1':'部署'}
        for row in rows:
            if row.get('can_stop') and row.get('stop_kind')=='task' and row.get('stop_command'):
                group=group_names.get(row['component'],'诊断')
                commands.append(dict(group=group,label='停止当前任务 · PID '+str(row['pid']),command='# 核对当前进程后，向同组发送 Ctrl+C\nps -p '+str(row['pid'])+' -o pid,lstart,args\n'+row['stop_command']))
        return dict(tasks=rows,commands=commands,updated_at=time.time())

    def stop(self, request):
        if request.id in ('deployment','stage1-loader'):
            state=self.manager.status()
            if state.get('pid')!=request.model_pid or state.get('start_ticks')!=request.model_start_ticks:
                raise DeviceControlError('模型任务已变化，请刷新后重试')
            expected=state if request.id=='deployment' else read_json(RUN/'model-server/process.json')
            if int(expected.get('pid',-1))!=request.pid or process_identity(request.pid)!=request.start_ticks:
                raise DeviceControlError('模型进程已变化，请刷新后重试')
            return self.manager.submit('unload')
        return self.devices.stop_job(request.component, request.id, request.pid, request.start_ticks)


def install_output_routes(app, manager, devices):
    outputs = TaskOutputs(manager, devices)
    @app.get('/api/console/outputs')
    def snapshot(history: bool=False):
        return outputs.snapshot(history)
    @app.post('/api/console/outputs/stop')
    def stop(request: StopTaskRequest):
        try:
            return outputs.stop(request)
        except (DeviceControlError, DeploymentError) as error:
            raise HTTPException(409, str(error)) from error

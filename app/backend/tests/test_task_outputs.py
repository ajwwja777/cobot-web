import json
import shlex
import os
import subprocess
import time
from types import SimpleNamespace as NS

import pytest

from cobot_console import deployment
from cobot_console.device_control import DeviceController, DeviceControlError
from cobot_console.task_outputs import TaskOutputs, StopTaskRequest, common_commands


def test_only_exact_job_and_process_identity_can_be_stopped(tmp_path, monkeypatch):
    stopped=[]
    controller=DeviceController(tmp_path,pid_probe=lambda pid,marker:True,process_finder=lambda marker:[],
                                stopper=lambda pid,marker:stopped.append((pid,marker)),system_probe=lambda:{})
    entry=dict(job_id='arms-1',component='arms',pid=23456,phase='running',owned=True,
               stop_marker='cobot-platform/robot/arms/arms.launch',command=['arms_up.sh'])
    controller._write('arms',entry)
    monkeypatch.setattr('cobot_control.processes.process_identity',lambda pid:42)
    for values in [('arms','arms-old',23456,42),('arms','arms-1',23457,42),('arms','arms-1',23456,99),('../other','arms-1',23456,42)]:
        with pytest.raises(DeviceControlError):controller.stop_job(*values)
    assert not stopped
    result=controller.stop_job('arms','arms-1',23456,42)
    assert stopped==[(23456,entry['stop_marker'])]
    assert result['phase']=='stopping'


def test_ros_stop_preserves_dependency_check(tmp_path,monkeypatch):
    stopped=[]
    controller=DeviceController(tmp_path,pid_probe=lambda pid,marker:True,process_finder=lambda marker:[1],
                                stopper=lambda *args:stopped.append(args),system_probe=lambda:{})
    controller._write('roscore',dict(job_id='roscore-1',component='roscore',pid=12345,phase='running',command=['roscore'],stop_marker='roscore'))
    monkeypatch.setattr('cobot_control.processes.process_identity',lambda pid:77)
    with pytest.raises(DeviceControlError,match='先停止机械臂和相机'):controller.stop_job('roscore','roscore-1',12345,77)
    assert not stopped


def test_model_stop_rejects_old_model_and_delegates_lifecycle(monkeypatch,tmp_path):
    calls=[]
    manager=NS(status=lambda:dict(pid=123,start_ticks=33),submit=lambda action:calls.append(action) or {'operation':action})
    service=TaskOutputs(manager,NS())
    monkeypatch.setattr('cobot_console.task_outputs.process_identity',lambda pid:33)
    payload=dict(id='deployment',component='deployment',pid=123,start_ticks=33,model_pid=122,model_start_ticks=33)
    with pytest.raises(DeviceControlError):service.stop(StopTaskRequest(**payload))
    assert not calls
    payload['model_pid']=123
    assert service.stop(StopTaskRequest(**payload))=={'operation':'unload'}
    assert calls==['unload']


def test_interrupts_only_registered_disposable_process(tmp_path):
    # Real SIGINT coverage on a disposable sleep, never a hardware process.
    process=subprocess.Popen(['sleep','20'],start_new_session=True)
    controller=DeviceController(tmp_path,system_probe=lambda:{})
    controller._write('pose',dict(job_id='pose-test',component='pose',pid=process.pid,phase='running',owned=True,command=['sleep','20']))
    try:
        result=controller.stop_job('pose','pose-test',process.pid,deployment.process_identity(process.pid))
        assert result['phase']=='stopping'
        process.wait(timeout=3)
        assert process.returncode==-2
        with pytest.raises(DeviceControlError):controller.stop_job('pose','pose-test',process.pid,1)
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=3)


def test_snapshot_has_actual_command_pid_and_history_cannot_stop(tmp_path,monkeypatch):
    from cobot_console import task_outputs
    monkeypatch.setattr(task_outputs,'RUN',tmp_path/'rlt')
    monkeypatch.setattr(task_outputs,'RUNTIME',tmp_path/'deployment')
    process=subprocess.Popen(['sleep','20'],start_new_session=True)
    log=tmp_path/'pose-1.log';log.write_text('hello\n')
    old=tmp_path/'pose-old.log';old.write_text('old\n')
    job=dict(job_id='pose-1',component='pose',pid=process.pid,phase='running',owned=True,command=['sleep','20'],log_path=str(log),log_tail='hello\n')
    service=TaskOutputs(NS(status=lambda:{'phase':'offline'}),NS(runtime=tmp_path,status=lambda:{'jobs':{'pose':job}}))
    try:
        rows=service.snapshot(True)['tasks']
        live=next(r for r in rows if r['id']=='pose-1')
        assert live['pid']==process.pid and live['can_stop']
        assert live['process_command']=='sleep 20' and live['command_text']=='sleep 20'
        assert str(process.pid) in live['stop_command']
        assert not next(r for r in rows if r['phase']=='archived')['can_stop']
    finally:
        process.terminate();process.wait(timeout=3)


def test_common_commands_no_password_or_arbitrary_shell_endpoint():
    commands=common_commands()
    assert len(commands)>=15
    assert all('read -rsp' not in c['command'] and 'cobot_password' not in c['command'] for c in commands)
    configure = next(c for c in commands if c['label'] == '配置五臂 CAN')
    assert configure['command'].endswith('\n./scripts/can_up.sh')
    assert 'cobot-control' in configure['command'] and 'can_config_cobot.sh' in configure['implementation']['zh']
    from pydantic import ValidationError
    with pytest.raises(ValidationError):StopTaskRequest(id='x',component='arms',pid=1,start_ticks=2,command='rm -rf anything')


def test_common_commands_use_shared_cli_and_token_aware_pause():
    from cobot_console.task_outputs import common_commands
    rows = common_commands()
    commands = "\n".join(row["command"] for row in rows)
    assert "console.py recovery pause" in commands
    assert "console.py model unload" in commands
    assert "console.py recovery interrupt model" in commands
    assert "-d '{}'" not in commands
    assert "rlt_v3_down.sh" not in commands


def test_terminal_can_recipe_preserves_original_command_and_has_no_password_pipe():
    from cobot_console.task_outputs import process_details
    row = dict(component='can', pid=-1, command=['/site/web/scripts/can_web.sh', 'configure'])
    details = process_details(row)
    assert details['command_text'] == '/site/web/scripts/can_web.sh configure'
    assert details['terminal_command'].endswith('\n./scripts/can_up.sh')
    assert 'can_web.sh' not in details['terminal_command']
    assert 'cobot_password' not in details['terminal_command']
    assert details['process_command'] == ''


def test_terminal_home_recipe_quotes_arguments_and_all_recipes_are_valid_bash():
    from cobot_console.terminal_commands import task_terminal_details
    details = task_terminal_details(dict(command=['/web/scripts/home.sh', 'capture', '--pose', 'literal $(false)']))
    assert shlex.split(details['terminal_command'].splitlines()[1])[-1] == 'literal $(false)'
    assert "'literal $(false)'" in details['terminal_command']
    for row in common_commands():
        subprocess.run(['bash', '-n'], input=row['command'], text=True, check=True)

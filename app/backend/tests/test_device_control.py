import json
import struct
from pathlib import Path
import pytest
import yaml

from cobot_console.device_control import DeviceControlError, DeviceController
import cobot_console.device_control as device_control

class Process:
    def __init__(self,pid=123): self.pid=pid; self.code=None
    def poll(self): return self.code

def fixed_home_poses(tmp_path, monkeypatch):
    config = tmp_path / 'test_home_poses.yaml'
    config.write_text(yaml.safe_dump({'poses': {
        'plug': {'front_left': [0], 'front_right': [0]},
        'origin': {'front_left': [0], 'front_right': [0], 'mid': [0]},
        'plug2': {'front_left': [0], 'front_right': [0], 'mid': [0]},
        'camera': {'mid': [0]}, 'camara': {'mid': [0]}, 'shuai': {'mid': [0]},
    }}))
    monkeypatch.setattr(device_control, 'POSE_CONFIG', config)

def test_unknown_operation_and_arbitrary_arguments_are_rejected(tmp_path):
    control=DeviceController(tmp_path,launcher=lambda *a,**k:Process(),clock=lambda:10)
    with pytest.raises(DeviceControlError,match='unsupported'): control.confirm({'component':'shell','action':'run','target':'rm'})
    with pytest.raises(DeviceControlError,match='not available'): control.confirm({'component':'home','action':'run','target':'all','pose':'origin; rm -rf /'})
    with pytest.raises(DeviceControlError,match='not available'): control.confirm({'component':'home','action':'run','target':'mid','pose':'plug'})
    with pytest.raises(DeviceControlError,match='not available'): control.confirm({'component':'home','action':'run','target':'gripper','pose':'origin'})

def test_confirmation_is_exact_short_lived_and_one_use(tmp_path, monkeypatch):
    fixed_home_poses(tmp_path, monkeypatch)
    now=[10.0]; launched=[]
    control=DeviceController(tmp_path,launcher=lambda command,log: launched.append(command) or Process(),clock=lambda:now[0])
    spec={'component':'home','action':'run','target':'all','pose':'plug'}
    token=control.confirm(spec)['confirmation_token']
    with pytest.raises(DeviceControlError,match='does not match'): control.start({**spec,'pose':'origin'},token)
    assert control.start(spec,token)['phase']=='running'
    assert Path(launched[0][-5]).name=='home.sh' and launched[0][-4:]==['all','--pose','plug','--yes']
    with pytest.raises(DeviceControlError,match='confirmation'): control.start(spec,token)
    expired=control.confirm(spec)['confirmation_token']; now[0]=100
    with pytest.raises(DeviceControlError,match='expired'): control.start(spec,expired)

def test_restart_never_claims_unowned_or_reused_pid(tmp_path):
    metadata=tmp_path/'home.json'
    metadata.write_text(json.dumps({'job_id':'old','pid':55,'phase':'running','command':['/fixed/home.sh','all'],'started_at':1}))
    control=DeviceController(tmp_path,pid_probe=lambda pid,marker:False,clock=lambda:10)
    status=control.status()['jobs']['home']
    assert status['phase']=='stale'
    assert status['owned'] is False

def test_log_tail_is_bounded_and_process_exit_is_reported(tmp_path):
    process=Process(); control=DeviceController(tmp_path,launcher=lambda command,log:process,clock=lambda:10)
    spec={'component':'recover','action':'run','target':'rear-left'}
    job=control.start(spec,control.confirm(spec)['confirmation_token'])
    log=Path(job['log_path']); log.write_text('\n'.join('line-'+str(i) for i in range(200)))
    process.code=7
    state=control.status()['jobs']['recover']
    assert state['phase']=='failed' and state['exit_code']==7
    assert len(state['log_tail'].splitlines())<=80
    assert 'line-199' in state['log_tail'] and 'line-0' not in state['log_tail']
    assert state['finished_at'] == 10


def test_duplicate_home_is_rejected_until_previous_process_exits(tmp_path, monkeypatch):
    fixed_home_poses(tmp_path, monkeypatch)
    running = [313]
    control = DeviceController(
        tmp_path,
        launcher=lambda command, log: Process(313),
        process_finder=lambda marker: running if marker.endswith('home.py') else [],
        clock=lambda: 10,
    )
    spec = {'component':'home','action':'run','target':'all','pose':'plug'}
    with pytest.raises(DeviceControlError, match='already running'):
        control.start(spec, control.confirm(spec)['confirmation_token'])


def test_duplicate_rlt_start_is_rejected_before_port_owners_multiply(tmp_path):
    control = DeviceController(
        tmp_path,
        process_finder=lambda marker: [313]
        if marker == 'methods.openpi_rlt.scripts.online_role' else [],
        clock=lambda: 10,
    )
    spec = {'component':'rlt','action':'start','target':'reference'}
    with pytest.raises(DeviceControlError, match='already running'):
        control.start(spec, control.confirm(spec)['confirmation_token'])

@pytest.mark.parametrize('spec,tail',[
 ({'component':'can','action':'configure','target':'task2'},['can_web.sh','configure']),
 ({'component':'can','action':'reset','target':'task2'},['can_web.sh','reset']),
 ({'component':'roscore','action':'start'},['roscore_up.sh']),
 ({'component':'arms','action':'start'},['arms_up.sh']),
 ({'component':'cameras','action':'start'},['cameras_up.sh']),
 ({'component':'pose','action':'capture','target':'all','pose':'onsite_v2'},['home.sh','capture','--arm','all','--pose','onsite_v2']),
 ({'component':'pose','action':'delete','target':'all','pose':'origin'},['home.sh','delete','--pose','origin','--yes']),
 ({'component':'recover','action':'run','target':'front-right'},['recover.sh','front-right','--supported']),
 ({'component':'recover','action':'run','target':'gripper-right'},['recover.sh','gripper-right','--supported']),
 ({'component':'recover','action':'run','target':'sync'},['recover.sh','sync','--supported']),
 ({'component':'rlt','action':'start','target':'online'},['rlt_up.sh']),
 ({'component':'rlt','action':'start','target':'frozen'},['rlt_demo.sh']),
 ({'component':'rlt','action':'start','target':'reference'},['rlt_up.sh','--reference','--no-record']),
 ({'component':'rlt','action':'stop'},['rlt_stop.sh']),
 ({'component':'rlt','action':'down'},['rlt_down.sh']),
 ({'component':'console','action':'stop'},['ui_shutdown_after_response.sh']),
])
def test_fixed_registry_builds_only_expected_commands(tmp_path,spec,tail):
    launched=[]
    fake=lambda command,log:launched.append(command) or Process()
    secret_fake=lambda command,log,secret:launched.append(command) or Process()
    control=DeviceController(tmp_path,launcher=fake,secret_launcher=secret_fake,process_finder=lambda marker:[])
    needs_secret=spec['component']=='can'
    action={**spec,'sudo_password':'one-shot'} if needs_secret else spec
    control.start(action,control.confirm(spec)['confirmation_token'])
    actual=launched[0][-len(tail):]
    assert Path(actual[0]).name==tail[0]
    assert actual[1:]==tail[1:]

from fastapi.testclient import TestClient
from cobot_console.api import create_app
from tests.test_console_api import FakeBackend,FakeBridge,FakeRecorder,FakeRegistry,FakeSegmentedService,_fresh_cache

class FakeDevices:
    def status(self): return {'jobs':{},'operations':['home']}
    def confirm(self,spec): return {'confirmation_token':'abc','operation':spec,'expires_at':99}
    def start(self,spec,token):
        assert token=='abc'; return {'phase':'running','component':spec['component']}

def test_console_device_routes_use_structured_payloads(tmp_path):
    app=create_app(cache=_fresh_cache(),bridge=FakeBridge(),recorder=FakeRecorder(),segmented_service=FakeSegmentedService(),
        backend_client=FakeBackend(),lifecycle_registry=FakeRegistry('ready_disarmed'),allowed_data_root=tmp_path,rlt_data_root=tmp_path,
        monotonic=lambda:10.0,device_controller=FakeDevices())
    with TestClient(app) as client:
        assert client.get('/api/console/devices').json()['operations']==['home']
        prepared=client.post('/api/console/devices/confirm',json={'component':'home','action':'run','target':'all','pose':'plug'})
        started=client.post('/api/console/devices/action',json={'operation':{'component':'home','action':'run','target':'all','pose':'plug'},'confirmation_token':'abc'})
    assert prepared.status_code==200 and prepared.json()['confirmation_token']=='abc'
    assert started.status_code==200 and started.json()['phase']=='running'


def test_status_includes_read_only_system_probes(tmp_path):
    expected={'can':{'phase':'ready','detail':'6/6 up'},'arms':{'phase':'offline','detail':'ROS nodes absent'}}
    control=DeviceController(tmp_path,system_probe=lambda:expected)
    assert control.status()['systems']==expected

def test_managed_long_running_job_can_receive_ctrl_c_and_reports_stopped(tmp_path):
    process=Process(); stopped=[]
    control=DeviceController(tmp_path,launcher=lambda command,log:process,
        pid_probe=lambda pid,marker:process.code is None,
        process_finder=lambda marker:[],
        stopper=lambda pid,marker:stopped.append((pid,marker)),
        waiter=lambda pids,marker,timeout:False,clock=lambda:10)
    start={'component':'arms','action':'start'}
    control.start(start,control.confirm(start)['confirmation_token'])
    stop={'component':'arms','action':'stop'}
    result=control.start(stop,control.confirm(stop)['confirmation_token'])
    assert result['phase']=='stopping'
    assert stopped==[(123,'cobot-platform/robot/arms/arms.launch')]
    process.code=-2
    assert control.status()['jobs']['arms']['phase']=='stopped'

def test_status_exposes_only_compatible_home_poses(tmp_path, monkeypatch):
    fixed_home_poses(tmp_path, monkeypatch)
    payload=DeviceController(tmp_path,system_probe=lambda:{}).status()
    mid=payload['home_poses']['mid']
    assert mid==sorted(mid)
    assert {'camara','camera','origin','plug2','shuai'} <= set(mid)
    assert payload['home_poses']['gripper']==['reinit']
    assert 'plug' in payload['home_poses']['all']


def test_can_password_is_one_shot_and_never_persisted(tmp_path):
    received=[]
    def launch(command,log,secret):
        received.append((command,secret))
        return Process(777)
    control=DeviceController(tmp_path,secret_launcher=launch,clock=lambda:10)
    spec={'component':'can','action':'configure','target':'task2'}
    token=control.confirm(spec)['confirmation_token']
    result=control.start({**spec,'sudo_password':'transient-secret'},token)
    assert received==[([str(Path(__file__).resolve().parents[3] / 'scripts/can_web.sh'),'configure'],'transient-secret')]
    serialized=json.dumps(result)+((tmp_path/'can.json').read_text())
    assert 'transient-secret' not in serialized
    assert 'sudo_password' not in serialized


def test_recover_uses_passwordless_fixed_helper_contract(tmp_path):
    launched=[]
    control=DeviceController(tmp_path,launcher=lambda command,log:launched.append(command) or Process(778),clock=lambda:10)
    spec={'component':'recover','action':'run','target':'front-right'}
    token=control.confirm(spec)['confirmation_token']
    result=control.start(spec,token)
    assert launched[0][-2:]==['front-right','--supported']
    assert 'sudo_password' not in json.dumps(result)+(tmp_path/'recover.json').read_text()


def test_all_recover_targets_start_without_web_secret(tmp_path):
    control=DeviceController(tmp_path,launcher=lambda *a:Process(),secret_launcher=lambda *a:Process())
    for target in ('front-left','front-right','gripper-left','gripper-right','mid','rear-left'):
        spec={'component':'recover','action':'run','target':target}
        assert control.start(spec,control.confirm(spec)['confirmation_token'])['phase']=='running'


def test_dynamic_pose_inventory_tracks_yaml_without_restart(tmp_path,monkeypatch):
    config=tmp_path/'poses.yaml'
    config.write_text('poses:\n  front_only:\n    front_left: [0,0,0,0,0,0,0]\n    front_right: [0,0,0,0,0,0,0]\n  middle_only:\n    mid: [0,0,0,0,0,0,0]\n')
    monkeypatch.setattr(device_control,'POSE_CONFIG',config)
    payload=DeviceController(tmp_path/'runtime',system_probe=lambda:{}).status()['home_poses']
    assert payload['front']==['front_only']
    assert payload['rear']==['front_only']
    assert payload['all']==['front_only']
    assert payload['mid']==['middle_only']
    assert payload['gripper']==['reinit']


def test_pose_name_and_target_are_strictly_structured(tmp_path):
    control=DeviceController(tmp_path)
    with pytest.raises(DeviceControlError,match='invalid pose name'):
        control.confirm({'component':'pose','action':'capture','target':'front','pose':'bad pose; rm'})
    with pytest.raises(DeviceControlError,match='target'):
        control.confirm({'component':'pose','action':'capture','target':'gripper','pose':'safe'})


def test_stop_discovers_and_stops_all_matching_process_groups(tmp_path):
    stopped=[]
    control=DeviceController(
        tmp_path,
        process_finder=lambda marker:[101,202],
        pid_probe=lambda pid,marker:pid in (101,202),
        stopper=lambda pid,marker:stopped.append((pid,marker)),
        waiter=lambda pids,marker,timeout:True,
        clock=lambda:10,
    )
    spec={'component':'cameras','action':'stop'}
    result=control.start(spec,control.confirm(spec)['confirmation_token'])
    assert result['phase']=='stopped'
    assert result['pids']==[101,202]
    assert [pid for pid,_ in stopped]==[101,202]
    assert 'stopped 2 process group(s)' in result['detail']


def test_duplicate_healthy_start_is_adopted_but_incomplete_launch_is_rejected(tmp_path):
    process_finder=lambda marker:[313]
    ready=DeviceController(
        tmp_path/'ready',
        process_finder=process_finder,
        system_probe=lambda:{'arms':{'phase':'ready','detail':'3/3 arm coordinators'}},
        clock=lambda:10,
    )
    spec={'component':'arms','action':'start'}
    result=ready.start(spec,ready.confirm(spec)['confirmation_token'])
    assert result['adopted'] is True and result['pids']==[313]

    partial=DeviceController(
        tmp_path/'partial',
        process_finder=process_finder,
        system_probe=lambda:{'arms':{'phase':'error','detail':'1/3 arm coordinators'}},
        clock=lambda:10,
    )
    with pytest.raises(DeviceControlError,match='incomplete'):
        partial.start(spec,partial.confirm(spec)['confirmation_token'])


def test_roscore_stop_refuses_while_dependent_launches_exist(tmp_path):
    markers={
        '/opt/ros/noetic/bin/roscore':[10],
        'cobot-platform/robot/arms/arms.launch':[20],
        'multi_camera_shuai.launch':[],
    }
    control=DeviceController(
        tmp_path,
        process_finder=lambda marker:markers.get(marker,[]),
        clock=lambda:10,
    )
    spec={'component':'roscore','action':'stop'}
    with pytest.raises(DeviceControlError,match='stop arms'):
        control.start(spec,control.confirm(spec)['confirmation_token'])


def test_passive_arm_feedback_distinguishes_joint_and_gripper_faults(monkeypatch):
    class FakeSocket:
        def __init__(self, frames):
            self.frames = iter(frames)
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def setsockopt(self, *_args): pass
        def bind(self, address): assert address == ('can_right',)
        def settimeout(self, _seconds): pass
        def recv(self, _size): return next(self.frames)

    def report(motor_enabled=True, gripper_status=0x40):
        frames = []
        for can_id in [0x2a1, 0x2a8, *range(0x261, 0x267)]:
            data = bytearray(8)
            if can_id == 0x2a1: data[0] = 1
            elif can_id == 0x2a8: data[6] = gripper_status
            else: data[5] = 0x40 if motor_enabled or can_id != 0x261 else 0
            frames.append(struct.pack('=IB3x8s', can_id, 8, data))
        monkeypatch.setattr(device_control.socket, 'socket', lambda *_args: FakeSocket(frames))
        return device_control._passive_arm_feedback('can_right')

    assert report()['phase'] == 'ready'
    assert report(motor_enabled=False)['phase'] == 'disabled'
    gripper_fault = report(gripper_status=0x70)
    assert gripper_fault['phase'] == 'ready'
    assert gripper_fault['gripper']['phase'] == 'error'

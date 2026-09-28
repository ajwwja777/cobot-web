"""Fixed allowlist job control for the onsite console; never accepts shell text."""
from __future__ import annotations

import json
import http.client
import os
import re
import secrets
import signal
import socket
import struct
import subprocess
import time
import threading
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict
import yaml

from .paths import PROJECT as PLATFORM, RUNTIME_ROOT, SETTINGS, CONTROL
SCRIPTS = PLATFORM / 'scripts'
_TARGETS = {
    'home': {'front','rear','all','mid','gripper','selection'},
    'recover': {'front-left','front-right','front-pair','rear-left','rear-right','gripper-left','gripper-right','mid','sync'},
    'rlt': {'online','frozen','reference','warmup'},
    'rlt_model': {'plug_v3-stage1-reference','plug_v3-frozen-latest','plug_v3-online-latest'},
}
from .paths import POSE_CONFIG
POSE_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
SELECTABLE_ARMS = ('front-left','front-right','mid','rear-left','rear-right')


def _selection(spec):
    raw = spec.get('arms')
    if not isinstance(raw, list) or not raw or len(raw) > len(SELECTABLE_ARMS):
        raise DeviceControlError('select one or more arms')
    if any(not isinstance(item, str) or item not in SELECTABLE_ARMS for item in raw):
        raise DeviceControlError('unknown arm in selection')
    if len(set(raw)) != len(raw):
        raise DeviceControlError('duplicate arm in selection')
    return [item for item in SELECTABLE_ARMS if item in raw]


def _home_poses():
    try:
        payload = yaml.safe_load(POSE_CONFIG.read_text()) or {}
    except (OSError, yaml.YAMLError):
        payload = {}
    poses = payload.get('poses') if isinstance(payload, dict) else {}
    if not isinstance(poses, dict):
        poses = {}
    result = {'front':set(),'rear':set(),'all':set(),'mid':set(),'gripper':{'reinit'}}
    result.update({arm:set() for arm in SELECTABLE_ARMS})
    for name, entry in poses.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        front = all('front_'+side in entry for side in ('left','right'))
        rear = all('rear_'+side in entry for side in ('left','right'))
        if front:
            result['front'].add(name)
            result['all'].add(name)
        for side in ('left','right'):
            if 'front_'+side in entry:
                result['front-'+side].add(name)
            if 'rear_'+side in entry or 'front_'+side in entry:
                result['rear-'+side].add(name)
        if rear or front:
            result['rear'].add(name)
        if 'mid' in entry:
            result['mid'].add(name)
    return result
_STOP_MARKERS = {
    'roscore': '/opt/ros/noetic/bin/roscore',
    'arms': str(CONTROL / 'robot/arms/arms.launch'),
    'cameras': 'multi_camera_shuai.launch',
    'home': str(CONTROL / 'robot/home.py'),
    'recover': str(CONTROL / 'robot/recover.py'),
    'rlt': 'methods.openpi_rlt.scripts.online_role',
}


class DeviceControlError(ValueError):
    pass


def _open_process(command, log_path, stdin):
    stream = open(log_path, 'ab', buffering=0)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PLATFORM),
            stdin=stdin,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        stream.close()
        raise
    process._console_log_stream = stream
    return process


def _default_launcher(command, log_path):
    return _open_process(command, log_path, subprocess.DEVNULL)


def _default_secret_launcher(command, log_path, secret):
    process = _open_process(command, log_path, subprocess.PIPE)
    try:
        process.stdin.write((secret + '\n').encode('utf-8'))
        process.stdin.flush()
        process.stdin.close()
    except Exception:
        process.terminate()
        raise
    return process


def _process_command(pid: int) -> str:
    try:
        return (Path('/proc') / str(pid) / 'cmdline').read_bytes().replace(
            b'\0', b' '
        ).decode(errors='replace')
    except OSError:
        return ''


def _matches_process_marker(command: str, marker: str) -> bool:
    """Match complete argv tokens; accept only the registered old project root."""
    candidates = {marker}
    roots = {str(PLATFORM), str(CONTROL)}
    legacy = str(SETTINGS.get("legacy_platform_root", "")).rstrip("/")
    if legacy:
        roots.add(legacy)
    relative = None
    for root in roots:
        if marker.startswith(root + "/"):
            relative = marker[len(root) + 1:]
            break
    if marker.startswith("cobot-platform/"):
        relative = marker[len("cobot-platform/"):]
        if relative in {"robot/arms/home.py", "robot/arms/recover.py"}:
            relative = relative.replace("robot/arms/", "robot/")
    if relative:
        candidates.update(root + "/" + relative for root in roots)
    tokens = command.split()
    if marker == "multi_camera_shuai.launch":
        return any(Path(token).name == marker for token in tokens)
    return any(candidate in tokens for candidate in candidates)


def _default_pid_probe(pid: int, marker: str) -> bool:
    return _matches_process_marker(_process_command(pid), marker)


def _default_process_finder(marker: str):
    """Return exact session leaders whose command line contains a fixed marker."""
    groups = set()
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            pid = int(path.parent.name)
            raw = path.read_bytes().replace(b'\0', b' ').decode(errors='replace')
            if not _matches_process_marker(raw, marker):
                continue
            pgid = os.getpgid(pid)
            if pgid == pid:
                groups.add(pid)
        except (OSError, ValueError):
            continue
    return sorted(groups)


def _default_stopper(pid: int, marker: str) -> None:
    if not _default_pid_probe(pid, marker):
        raise DeviceControlError('managed process is no longer present')
    try:
        group = os.getpgid(pid)
    except OSError as exc:
        raise DeviceControlError('managed process is no longer present') from exc
    if group != pid:
        raise DeviceControlError('refusing to stop a process outside its own managed group')
    os.killpg(group, signal.SIGINT)


def _default_waiter(pids, marker: str, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not any(_default_pid_probe(int(pid), marker) for pid in pids):
            return True
        time.sleep(0.1)
    return not any(_default_pid_probe(int(pid), marker) for pid in pids)


def _run_readonly(command):
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # On the Cobot workstation a cold ROS Python import regularly
            # takes 3-4 seconds.  A two-second probe falsely marked a healthy
            # master and its nodes offline, which then blocked the UI flow.
            timeout=6,
            check=False,
        )
        return result.returncode, result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


class _TimedRosTransport(xmlrpc.client.Transport):
    def make_connection(self, host):
        return http.client.HTTPConnection(host, timeout=1.5)


def _ros_nodes():
    """Read ROS master registration without a multi-second rosnode CLI import."""
    try:
        master = xmlrpc.client.ServerProxy(
            os.getenv('ROS_MASTER_URI', 'http://127.0.0.1:11311'),
            transport=_TimedRosTransport(), allow_none=True,
        )
        code, message, state = master.getSystemState('/cobot_console_status')
        if code != 1:
            return False, set(), str(message)
        nodes = {node for group in state for _, owners in group for node in owners}
        return True, nodes, 'ROS Master reachable'
    except (OSError, ValueError, xmlrpc.client.Error) as exc:
        return False, set(), str(exc)[:120]


_CAN_BUSES = {
    'front-left': 'can_left', 'front-right': 'can_right',
    'mid': 'can_mid', 'rear-left': 'can_rear_left',
    'rear-right': 'can_rear_right',
}


def _passive_arm_feedback(bus: str) -> Dict[str, Any]:
    """Listen to existing CAN feedback; never open a control publisher."""
    ids = {0x2a1, 0x2a8, *range(0x261, 0x267)}
    latest = {}
    with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as stream:
        stream.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                          b''.join(struct.pack('=II', number, 0x7ff) for number in sorted(ids)))
        stream.bind((bus,))
        stream.settimeout(.04)
        deadline = time.monotonic() + .7
        while time.monotonic() < deadline and len(latest) < len(ids):
            try:
                raw = stream.recv(16)
            except socket.timeout:
                continue
            number, length, data = struct.unpack('=IB3x8s', raw)
            if number in ids and length == 8:
                latest[number] = data
    if not ids.issubset(latest):
        return {'phase':'error', 'detail':'CAN feedback missing', 'fresh':False}
    return _classify_arm_feedback(bus, latest)


def _classify_arm_feedback(bus: str, latest: Dict[int, bytes]) -> Dict[str, Any]:
    arm = latest[0x2a1]
    enabled = [bool(latest[number][5] & 0x40) for number in range(0x261, 0x267)]
    protected = [number-0x260 for number in range(0x261, 0x267)
                 if latest[number][5] & 0xbf]
    error = int.from_bytes(arm[6:8], 'big')
    gripper_code = latest[0x2a8][6]
    teach_status = arm[3]
    rear = bus in ('can_rear_left', 'can_rear_right')
    if error or protected or arm[1]:
        phase = 'error'
    elif rear and arm[0] == 2 and teach_status == 2:
        phase = 'ready' if all(enabled) else 'error'
    elif rear and not any(enabled) and teach_status == 0:
        # Gravity/idle after releasing the rear teach button is intentional.
        phase = 'ready'
    elif rear and 0 < sum(enabled) < 6:
        phase = 'error'
    elif all(enabled) and arm[0] in (0, 1) and teach_status == 0:
        phase = 'ready'
    else:
        phase = 'disabled'
    if rear:
        rear_mode = ('teaching' if arm[0] == 2 and teach_status == 2 else
                     'idle_disabled' if not any(enabled) and teach_status == 0 else
                     'can_holding' if all(enabled) and arm[0] == 1 else 'unexpected')
    else:
        rear_mode = None
    if rear_mode == 'unexpected':
        phase = 'error'
    return {
        'phase':phase, 'fresh':True, 'ctrl_mode':arm[0], 'arm_status':arm[1],
        'teach_status':teach_status, 'rear_mode':rear_mode,
        'error_code':error, 'enabled_joints':sum(enabled), 'protected_joints':protected,
        'detail':('CAN '+str(sum(enabled))+'/6 enabled; mode '+str(arm[0])+
                  '; teach '+str(teach_status)+'; error '+hex(error)),
        'gripper':{
            'phase':'error' if gripper_code & 0x3f else ('ready' if gripper_code & 0x40 else 'disabled'),
            'enabled':bool(gripper_code & 0x40), 'status_hex':hex(gripper_code),
            'error_bits':gripper_code & 0x3f,
            'detail':'gripper status '+hex(gripper_code),
        },
    }


def _control_routes():
    """Registered publisher/subscriber ownership, read-only and timeout bounded."""
    try:
        master = xmlrpc.client.ServerProxy(os.environ.get("ROS_MASTER_URI", "http://localhost:11311"),
                                           transport=_TimedRosTransport())
        code, _, graph = master.getSystemState("/cobot_web_health")
        if code != 1:
            return {}
        pubs, subs, services = (dict(part) for part in graph)
        owner = services.get("/task2/teach_handover/home_front", [])
        return {side: {"ready": len(owner) == 1
                       and pubs.get("/master/joint_" + side) == owner
                       and bool(subs.get("/master/joint_" + side))
                       and owner[0] in subs.get("/task2/policy/joint_" + side, [])}
                for side in ("left", "right")}
    except (OSError, ValueError, xmlrpc.client.Error):
        return {}


def _default_system_probe():
    systems = {}
    expected = ('can_left','can_right','can_mid','can_rear_left','can_rear_right')
    present = []
    up = []
    for name in expected:
        device = Path('/sys/class/net') / name
        if device.exists():
            present.append(name)
            try:
                flags = int((device / 'flags').read_text().strip(), 16)
            except (OSError, ValueError):
                flags = 0
            if flags & 1:
                up.append(name)
    if len(present) == len(expected) and len(up) == len(expected):
        systems['can'] = {'phase':'ready','detail':'5/5 CAN up'}
    elif present:
        systems['can'] = {
            'phase':'error',
            'detail':str(len(up))+'/5 up; '+str(len(present))+'/5 present',
        }
    else:
        systems['can'] = {'phase':'offline','detail':'CAN interfaces absent'}

    connected, nodes, ros_detail = _ros_nodes()
    arms = {
        '/task2_teach_handover',
        '/piper_rear_left_teach_task2',
        '/piper_rear_right_teach_task2',
    }
    cameras = {'/camera_f/camera','/camera_l/camera','/camera_r/camera'}
    arm_count = len(nodes & arms)
    camera_count = len(nodes & cameras)
    systems['roscore'] = {
        'phase':'ready' if connected else 'offline',
        'detail':ros_detail if connected else 'ROS Master absent: '+ros_detail,
    }
    def has_prefix(prefix):
        return any(node.startswith(prefix) for node in nodes)
    systems['arm_nodes'] = {
        'front-left': '/task2_teach_handover' in nodes and has_prefix('/piper_left_agilex_'),
        'front-right': '/task2_teach_handover' in nodes and has_prefix('/piper_right_agilex_'),
        'mid': has_prefix('/piper_mid_agilex_'),
        'rear-left': '/piper_rear_left_teach_task2' in nodes,
        'rear-right': '/piper_rear_right_teach_task2' in nodes,
    }
    driver_count = sum(systems['arm_nodes'].values())
    systems['arms'] = {
        'phase': 'ready' if driver_count == 5 else ('error' if driver_count or arm_count else 'offline'),
        'detail': str(driver_count)+'/5 arm nodes; '+str(arm_count)+'/3 coordinators',
    }
    systems['cameras'] = {
        'phase':'ready' if camera_count == 3 else ('error' if camera_count else 'offline'),
        'detail':str(camera_count)+'/3 camera nodes',
    }
    systems['camera_nodes'] = {
        'camera_left':'/camera_l/camera' in nodes,
        'camera_high':'/camera_f/camera' in nodes,
        'camera_right':'/camera_r/camera' in nodes,
    }

    matches = []
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            raw = path.read_bytes().replace(b'\0', b' ').decode(errors='ignore')
        except OSError:
            continue
        if 'methods.openpi_rlt.plug_v2' in raw or 'cobot-rlt-backend' in raw or 'methods.openpi_rlt.scripts.online_role' in raw:
            matches.append(path.parent.name)
    systems['rlt'] = {
        'phase':'ready' if matches else 'offline',
        'detail':('RLT processes '+','.join(matches[:4])) if matches else 'RLT backend absent',
    }
    # A node being registered does not prove that an arm or gripper is enabled.
    # Probe all five feedback buses concurrently so a stale arm is visible
    # without issuing a single motor command or delaying the HTTP request.
    with ThreadPoolExecutor(max_workers=5) as pool:
        reads = {name:pool.submit(_passive_arm_feedback, bus)
                 for name, bus in _CAN_BUSES.items()}
        feedback = {}
        for name, task in reads.items():
            try:
                feedback[name] = task.result()
            except (OSError, ValueError) as exc:
                feedback[name] = {'phase':'error', 'fresh':False, 'detail':str(exc)[:120]}
    systems['can_interfaces'] = {name:bus in up for name,bus in _CAN_BUSES.items()}
    systems['control_routes'] = _control_routes() if connected else {}
    systems['arms_feedback'] = feedback
    return systems


class DeviceController:
    def __init__(
        self,
        runtime: Path,
        *,
        launcher: Callable = _default_launcher,
        secret_launcher: Callable = _default_secret_launcher,
        pid_probe: Callable[[int,str],bool] = _default_pid_probe,
        process_finder: Callable[[str],list] = _default_process_finder,
        stopper: Callable[[int,str],None] = _default_stopper,
        waiter: Callable[[list,str,float],bool] = _default_waiter,
        clock: Callable[[],float] = time.time,
        system_probe: Callable[[],Dict[str,Any]] = _default_system_probe,
    ) -> None:
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.launcher = launcher
        self.secret_launcher = secret_launcher
        self.pid_probe = pid_probe
        self.process_finder = process_finder
        self.stopper = stopper
        self.waiter = waiter
        self.clock = clock
        self.system_probe = system_probe
        self._confirmations: Dict[str,tuple] = {}
        self._processes: Dict[str,Any] = {}
        self._system_cache: Dict[str,Any] = {}
        self._system_cache_at = float("-inf")
        self._system_executor = ThreadPoolExecutor(max_workers=1)
        self._system_future = None
        self._job_lock = threading.RLock()

    def _normalize(self, spec: Dict[str,Any]) -> Dict[str,str]:
        if not isinstance(spec, dict):
            raise DeviceControlError('unsupported request')
        component = str(spec.get('component',''))
        action = str(spec.get('action',''))
        allowed_keys = {'component','action'}
        result = {'component':component,'action':action}
        if (
            component == 'can'
            and action in ('configure','reset')
            and spec.get('target') == 'task2'
        ):
            allowed_keys.add('target')
            result['target'] = 'task2'
        elif component in ('roscore','arms','cameras') and action in ('start','stop'):
            pass
        elif component == 'home' and action == 'run' and spec.get('target') in _TARGETS['home']:
            target = str(spec['target'])
            pose = str(spec.get('pose',''))
            arms = _selection(spec) if target == 'selection' else None
            available = _home_poses()
            if not POSE_NAME_RE.fullmatch(pose) or (arms is not None and any(pose not in available[arm] for arm in arms)) or (arms is None and pose not in available[target]):
                raise DeviceControlError('pose {!r} is not available for {}'.format(pose,target))
            allowed_keys.update(('target','pose'))
            result.update(target=target,pose=pose)
            if arms is not None:
                allowed_keys.add('arms')
                result['arms'] = arms
        elif component == 'home' and action == 'stop':
            pass
        elif component == 'pose' and action in ('capture','delete'):
            target = str(spec.get('target',''))
            pose = str(spec.get('pose',''))
            if target not in ('front','rear','all','mid','selection'):
                raise DeviceControlError('pose capture target is not available')
            if not POSE_NAME_RE.fullmatch(pose):
                raise DeviceControlError('invalid pose name')
            if action == 'delete' and pose not in set().union(*_home_poses().values()):
                raise DeviceControlError('pose {!r} does not exist'.format(pose))
            allowed_keys.update(('target','pose'))
            result.update(target=target,pose=pose)
            if target == 'selection':
                allowed_keys.add('arms')
                result['arms'] = _selection(spec)
        elif component == 'recover' and action == 'run' and spec.get('target') in _TARGETS['recover']:
            allowed_keys.add('target')
            result['target'] = str(spec['target'])
        elif component == 'recover' and action == 'stop':
            pass
        elif component == 'rlt' and action == 'start' and spec.get('target') in _TARGETS['rlt']:
            allowed_keys.add('target')
            result['target'] = str(spec['target'])
            if spec.get('model') is not None:
                model = str(spec['model'])
                if model not in _TARGETS['rlt_model']:
                    raise DeviceControlError('model is not registered')
                if model == 'plug_v3-stage1-reference' and result['target'] not in ('reference','warmup'):
                    raise DeviceControlError('Stage-1 reference must use reference or warmup mode')
                if model == 'plug_v3-frozen-latest' and result['target'] != 'frozen':
                    raise DeviceControlError('frozen actor must use frozen mode')
                if model == 'plug_v3-online-latest' and result['target'] != 'online':
                    raise DeviceControlError('online actor must use online mode')
                allowed_keys.add('model')
                result['model'] = model
        elif component == 'rlt' and action in ('stop','down'):
            if spec.get('model') is not None:
                model = str(spec['model'])
                if model not in _TARGETS['rlt_model']:
                    raise DeviceControlError('model is not registered')
                allowed_keys.add('model')
                result['model'] = model
        elif component == 'console' and action == 'stop':
            pass
        else:
            raise DeviceControlError('unsupported device operation')
        if set(spec) != allowed_keys:
            raise DeviceControlError('unsupported device arguments')
        return result

    def _marker(self, component):
        from .site_options import device_marker
        return device_marker(component, _STOP_MARKERS[component])

    def _command(self, spec: Dict[str,str]):
        component, action = spec['component'], spec['action']
        if component == 'can':
            return [str(SCRIPTS/'can_web.sh'), action]
        if component == 'roscore':
            return [str(SCRIPTS/'roscore_up.sh')]
        if component in ('arms', 'cameras'):
            from .site_options import device_command
            try:
                return device_command(component) or [str(SCRIPTS / (component + '_up.sh'))]
            except (OSError, ValueError) as error:
                raise DeviceControlError(str(error)) from error
        if component == 'home':
            if spec['target'] == 'selection':
                return [str(SCRIPTS/'home.sh'),'selected','--targets',','.join(spec['arms']),'--pose',spec['pose'],'--yes']
            return [
                str(SCRIPTS/'home.sh'),
                spec['target'],
                '--pose',
                spec['pose'],
                '--yes',
            ]
        if component == 'pose':
            if action == 'capture':
                if spec['target'] == 'selection':
                    return [str(SCRIPTS/'home.sh'),'capture','--targets',','.join(spec['arms']),'--pose',spec['pose']]
                return [str(SCRIPTS/'home.sh'),'capture','--arm',spec['target'],'--pose',spec['pose']]
            return [str(SCRIPTS/'home.sh'),'delete','--pose',spec['pose'],'--yes']
        if component == 'recover':
            command=[str(SCRIPTS/'recover.sh'),spec['target'],'--supported']
            return command
        if component == 'console':
            return [str(SCRIPTS/'ui_shutdown_after_response.sh')]
        model = spec.get('model','')
        if action == 'stop':
            return [str(SCRIPTS/'rlt_stop.sh')]
        if action == 'down':
            return [str(SCRIPTS/('rlt_v3_down.sh' if model.startswith('plug_v3-') else 'rlt_down.sh'))]
        if model.startswith('plug_v3-'):
            mode = spec['target']
            return [str(SCRIPTS/'rlt_v3_up.sh'),mode]
        if spec['target'] == 'frozen':
            return [str(SCRIPTS/'rlt_demo.sh')]
        if spec['target'] == 'reference':
            return [str(SCRIPTS/'rlt_up.sh'),'--reference','--no-record']
        return [str(SCRIPTS/'rlt_up.sh')]

    def confirm(self, spec: Dict[str,Any]) -> Dict[str,Any]:
        normalized = self._normalize(spec)
        token = secrets.token_urlsafe(24)
        expires = float(self.clock()) + 60.0
        self._confirmations[token] = (normalized, expires)
        return {
            'confirmation_token':token,
            'expires_at':expires,
            'operation':normalized,
        }

    def start(self, spec: Dict[str,Any], token: str) -> Dict[str,Any]:
        with self._job_lock:
            return self._start(spec, token)

    def _start(self, spec: Dict[str,Any], token: str) -> Dict[str,Any]:
        raw = dict(spec) if isinstance(spec, dict) else spec
        secret = None
        if isinstance(raw, dict):
            secret = raw.pop('sudo_password', None)
        normalized = self._normalize(raw)
        saved = self._confirmations.get(str(token))
        if saved is None:
            raise DeviceControlError('confirmation missing or already used')
        expected, expires = saved
        if float(self.clock()) > expires:
            raise DeviceControlError('confirmation expired')
        if expected != normalized:
            raise DeviceControlError('confirmation does not match operation')
        self._confirmations.pop(str(token), None)

        component = normalized['component']
        action = normalized['action']
        needs_secret = component == 'can'
        if needs_secret:
            if not isinstance(secret, str) or not secret:
                raise DeviceControlError('sudo password is required for CAN configuration')
        elif secret is not None:
            raise DeviceControlError('unsupported device arguments')

        if action == 'stop' and component in _STOP_MARKERS and (
            component != 'rlt' or self.process_finder(_STOP_MARKERS['rlt'])
        ):
            if component == 'roscore':
                blockers = []
                for dependent in ('arms','cameras'):
                    if self.process_finder(self._marker(dependent)):
                        blockers.append(dependent)
                if blockers:
                    raise DeviceControlError(
                        'stop '+', '.join(blockers)+' before stopping ROS Core'
                    )
            return self._stop_managed(component)

        if component in ('roscore','arms','cameras') and action == 'start':
            marker = self._marker(component)
            if marker != _STOP_MARKERS[component] and self.process_finder(_STOP_MARKERS[component]):
                raise DeviceControlError("Built-in launcher is still running; stop it before using the custom launch")
            existing = self.process_finder(marker)
            if existing:
                health = self.system_probe().get(component, {})
                detail = str(health.get('detail') or 'health unavailable')
                if health.get('phase') != 'ready':
                    raise DeviceControlError(
                        component + ' launch already exists but is incomplete: ' + detail
                        + '; use the red stop button, then start again'
                    )
                return self._adopt_running(component, existing, marker, detail)

        if component == 'rlt' and action == 'start':
            existing = self.process_finder(_STOP_MARKERS['rlt'])
            if existing:
                raise DeviceControlError(
                    'RLT backend already running; reuse it or stop it before starting another model'
                )

        if component in ('home','pose','recover'):
            conflicts = []
            for owner in ('home','recover'):
                if self.process_finder(_STOP_MARKERS[owner]):
                    conflicts.append(owner)
            if conflicts:
                raise DeviceControlError(
                    '/'.join(conflicts) + ' is already running; wait for completion or stop it first'
                )

        command = self._command(normalized)
        stamp = int(float(self.clock()) * 1000)
        job_id = component + '-' + str(stamp)
        log = self.runtime / (job_id + '.log')
        if needs_secret:
            process = self.secret_launcher(command, log, secret)
            secret = None
        else:
            process = self.launcher(command, log)
        value = {
            'job_id':job_id,
            'component':component,
            'pid':int(process.pid),
            'pids':[int(process.pid)],
            'phase':'running',
            'command':command,
            'started_at':float(self.clock()),
            'log_path':str(log),
            'owned':True,
        }
        from .deployment import process_identity
        value['start_ticks'] = process_identity(int(process.pid))
        if component in _STOP_MARKERS:
            value['stop_marker'] = self._marker(component)
        self._processes[component] = process
        self._write(component, value)
        return dict(value)

    def stop_job(self, component, job_id, pid, start_ticks):
        """Interrupt only the exact task shown in the output panel."""
        from .deployment import process_identity
        if component not in {'can','roscore','arms','cameras','home','pose','recover','rlt'}:
            raise DeviceControlError('该任务不支持从输出面板终止')
        with self._job_lock:
            path = self.runtime / (component + '.json')
            value = self._job(path)
            if value.get('job_id') != job_id or int(value.get('pid', -1)) != pid:
                raise DeviceControlError('任务已变化，请刷新输出后重试')
            if value.get('phase') not in ('running','stopping') or process_identity(pid) != start_ticks:
                raise DeviceControlError('进程已退出或 PID 已被复用')
            marker = value.get('stop_marker') or ((value.get('command') or [''])[0])
            if not marker:
                raise DeviceControlError('没有可核验的任务命令')
            if component == 'roscore' and any(self.process_finder(self._marker(k)) for k in ('arms','cameras')):
                raise DeviceControlError('请先停止机械臂和相机，再停止 ROS')
            self.stopper(pid, marker)
            value.update(phase='stopping', stopped_at=float(self.clock()), detail='已发送 Ctrl+C，正在等待退出')
            value.pop('log_tail', None)
            self._write(component, value)
            return dict(value)

    def _adopt_running(self, component, pids, marker, health_detail):
        stamp = int(float(self.clock()) * 1000)
        value = {
            'job_id':component+'-adopted-'+str(stamp),
            'component':component,
            'pid':int(pids[0]),
            'pids':[int(pid) for pid in pids],
            'phase':'running',
            'command':self._command({'component':component,'action':'start'}),
            'started_at':float(self.clock()),
            'owned':True,
            'adopted':True,
            'stop_marker':marker,
            'detail':'already running; '+health_detail,
        }
        self._write(component, value)
        return dict(value)

    def _stop_managed(self, component: str) -> Dict[str,Any]:
        marker = self._marker(component)
        pids = set(int(pid) for pid in self.process_finder(marker))
        path = self.runtime / (component + '.json')
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            previous = {}
        for pid in previous.get('pids') or [previous.get('pid', -1)]:
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                continue
            if self.pid_probe(pid, marker):
                pids.add(pid)

        stamp = int(float(self.clock()) * 1000)
        if not pids:
            value = {
                'job_id':component+'-stop-'+str(stamp),
                'component':component,
                'pid':-1,
                'pids':[],
                'phase':'stopped',
                'command':[],
                'started_at':float(self.clock()),
                'stopped_at':float(self.clock()),
                'owned':False,
                'stop_marker':marker,
                'detail':'already stopped; no matching process group',
            }
            self._processes.pop(component, None)
            self._write(component, value)
            return dict(value)

        stopped = []
        errors = []
        for pid in sorted(pids):
            try:
                self.stopper(pid, marker)
                stopped.append(pid)
            except DeviceControlError as exc:
                errors.append(str(pid)+': '+str(exc))
        if not stopped:
            raise DeviceControlError('; '.join(errors) or 'no matching process group could be stopped')

        exited = self.waiter(stopped, marker, 8.0)
        value = {
            'job_id':component+'-stop-'+str(stamp),
            'component':component,
            'pid':int(stopped[0]),
            'pids':stopped,
            'phase':'stopped' if exited else 'stopping',
            'command':[],
            'started_at':float(self.clock()),
            'stopped_at':float(self.clock()),
            'owned':not exited,
            'stop_marker':marker,
            'detail':(
                'stopped {} process group(s)'.format(len(stopped))
                if exited
                else 'Ctrl-C sent to {} process group(s); waiting for exit'.format(len(stopped))
            ),
        }
        if errors:
            value['detail'] += '; skipped ' + '; '.join(errors)
        self._processes.pop(component, None)
        self._write(component, value)
        return dict(value)

    def _write(self, component: str, value: Dict[str,Any]) -> None:
        path = self.runtime / (component + '.json')
        temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
        temporary.write_text(json.dumps(value, sort_keys=True))
        os.replace(str(temporary), str(path))

    @staticmethod
    def _tail(path: Path) -> str:
        try:
            with path.open('rb') as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 32768), os.SEEK_SET)
                text = stream.read().decode('utf-8', errors='replace')
            lines = text.splitlines()[-80:]
            text = '\n'.join(lines)
            return text[-16384:]
        except OSError:
            return ''

    def _job(self, path: Path) -> Dict[str,Any]:
        with self._job_lock:
            return self._read_job(path)

    def _read_job(self, path: Path) -> Dict[str,Any]:
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            return {'phase':'invalid','owned':False,'log_tail':''}
        component = str(value.get('component') or path.stem)
        if value.get('phase') == 'stopped':
            value['log_tail'] = self._tail(
                Path(value.get('log_path', self.runtime/(value.get('job_id','')+'.log')))
            )
            return value

        process = self._processes.get(component)
        if process is not None and int(value.get('pid',-1)) == int(process.pid):
            code = process.poll()
            if code is None:
                value.update(
                    phase='stopping' if value.get('phase') == 'stopping' else 'running',
                    owned=True,
                )
            else:
                was_stopping = value.get('phase') == 'stopping'
                stopped = was_stopping and int(code) in (0,130,-signal.SIGINT)
                value.update(
                    phase='stopped' if stopped else ('completed' if code == 0 else 'failed'),
                    exit_code=int(code),
                    finished_at=float(self.clock()),
                    owned=True,
                )
                self._write(component, value)
        elif value.get('phase') in ('running','stopping'):
            marker = str(
                value.get('stop_marker')
                or (Path((value.get('command') or [''])[0]).name)
            )
            pids = value.get('pids') or [value.get('pid',-1)]
            alive = [
                int(pid) for pid in pids
                if self.pid_probe(int(pid), marker)
            ]
            if alive:
                value.update(phase=value.get('phase'),owned=True,pids=alive,pid=alive[0])
            elif value.get('phase') == 'stopping':
                value.update(phase='stopped',owned=False,finished_at=float(self.clock()))
                self._write(component, value)
            else:
                value.update(phase='stale',owned=False,finished_at=float(self.clock()))
                self._write(component, value)
        value['log_tail'] = self._tail(
            Path(value.get('log_path', self.runtime/(value.get('job_id','')+'.log')))
        )
        return value

    def status(self) -> Dict[str,Any]:
        jobs = {
            path.stem:self._job(path)
            for path in self.runtime.glob('*.json')
        }
        now = float(self.clock())
        if self.system_probe is not _default_system_probe:
            if not self._system_cache or now - self._system_cache_at >= 1.5:
                self._system_cache = self.system_probe()
                self._system_cache_at = now
        else:
            if self._system_future is not None and self._system_future.done():
                try:
                    self._system_cache = self._system_future.result()
                    self._system_cache_at = now
                except Exception as exc:
                    self._system_cache = {'probe':{'phase':'error','detail':str(exc)[:120]}}
                    self._system_cache_at = now
                self._system_future = None
            if self._system_future is None and now - self._system_cache_at >= .5:
                self._system_future = self._system_executor.submit(self.system_probe)
        return {
            'jobs':jobs,
            'systems':self._system_cache,
            'systems_observed_at':self._system_cache_at if self._system_cache else None,
            'pose_config_path':str(POSE_CONFIG),
            'operations':['can','roscore','arms','cameras','home','pose','recover','rlt','console'],
            'home_poses':{
                key:sorted(value)
                for key,value in _home_poses().items()
            },
        }

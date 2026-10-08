import signal
import subprocess
import sys
import time

import pytest

from cobot_console.deployment import ManagedRuntime, DeploymentError, atomic_json, read_json, process_identity


def spawn(runtime):
    process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
    state = dict(pid=process.pid, start_ticks=process_identity(process.pid), phase='paused', ready_confirmed=True,
                 started_at=time.time(), model=dict(kind='pi05', capabilities=dict(pause=True)))
    atomic_json(runtime.registry, state)
    runtime.process = process
    return process, state


@pytest.mark.parametrize('error', [
    DeploymentError('timeout exceeded while waiting for service /task2/policy/set_paused'),
    subprocess.TimeoutExpired(['pause'], 10),
])
def test_missing_pause_endpoint_does_not_block_owned_release(tmp_path, monkeypatch, error):
    runtime = ManagedRuntime(tmp_path)
    process, state = spawn(runtime)
    def unavailable(action):
        assert action == 'pause'
        raise error
    monkeypatch.setattr(runtime, 'action', unavailable)
    try:
        reply = runtime.unload()
        assert process.wait(timeout=2) is not None
        assert not runtime._owned_members(state)
        assert reply['phase'] == runtime.status()['phase'] == 'offline'
        assert reply['release_warning'] == str(error)
        assert read_json(runtime.registry)['release_warning'] == str(error)
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=2)


def test_owner_exits_during_pause_request_without_signalling_stale_group(tmp_path, monkeypatch):
    runtime = ManagedRuntime(tmp_path)
    process, state = spawn(runtime)
    def exits(action):
        process.terminate();process.wait(timeout=2)
        raise DeploymentError('pause service exited')
    monkeypatch.setattr(runtime, 'action', exits)
    monkeypatch.setattr('cobot_console.deployment.os.killpg', lambda *args: pytest.fail('Exited group must not receive a signal'))
    assert runtime.unload()['phase'] == 'offline'
    assert not runtime._owned_members(state)


def test_release_refuses_to_claim_offline_if_owned_members_remain(tmp_path, monkeypatch):
    runtime = ManagedRuntime(tmp_path)
    atomic_json(runtime.registry, dict(pid=123, start_ticks=456, phase='paused', model=dict(kind='pi05')))
    monkeypatch.setattr(runtime, '_owned_members', lambda state: [123])
    monkeypatch.setattr(runtime, 'status', lambda: dict(phase='paused'))
    monkeypatch.setattr(runtime, 'action', lambda op: (_ for _ in ()).throw(DeploymentError('missing endpoint')))
    signals=[]
    monkeypatch.setattr('cobot_console.deployment.os.killpg', lambda pid, sig: signals.append(sig))
    counter=iter([0,100,100,200])
    monkeypatch.setattr('cobot_console.deployment.time.monotonic', lambda: next(counter))
    with pytest.raises(DeploymentError, match='仍在退出'):
        runtime.unload()
    assert signals == [signal.SIGINT, signal.SIGTERM]
    assert read_json(runtime.registry)['phase'] == 'paused'
    assert read_json(runtime.registry)['release_warning'] == 'missing endpoint'


def test_reused_pid_is_not_signalled(tmp_path, monkeypatch):
    runtime = ManagedRuntime(tmp_path)
    atomic_json(runtime.registry, dict(pid=123, start_ticks=456, phase='paused', model=dict(kind='pi05')))
    monkeypatch.setattr('cobot_console.deployment.process_identity', lambda pid: 999)
    monkeypatch.setattr('cobot_console.deployment.os.killpg', lambda *args: pytest.fail('Reused PID must not receive a signal'))
    monkeypatch.setattr(runtime, 'action', lambda op: pytest.fail('Unowned process must not receive a pause command'))
    assert runtime.unload()['phase'] == 'offline'

import json
import time

import pytest

from cobot_console.deployment import ManagedRuntime, atomic_json


def test_pi05_fault_keeps_ready_paused_and_exposes_original_cause(tmp_path, monkeypatch):
    runtime = ManagedRuntime(tmp_path)
    log = tmp_path / 'client.log'
    log.write_text('[pi05-rtc-task2] ready and PAUSED\nruntime fault: delay budget exceeded\n')
    atomic_json(runtime.registry, dict(model=dict(kind='pi05'), pid=42, start_ticks=1,
        phase='running', started_at=time.time(), log_path=str(log), ready_confirmed=True))
    atomic_json(tmp_path / 'pi05-gate.json', dict(paused=True, manual_pause=True,
        runtime_fault='original RTC delay budget exceeded'))
    monkeypatch.setattr(runtime, '_alive', lambda state: True)
    state = runtime.status()
    assert state['phase'] == 'paused' and state['process_started'] and state['model_ready']
    assert state['policy_fault'] == state['error'] == 'original RTC delay budget exceeded'
    atomic_json(tmp_path / 'pi05-gate.json', dict(paused=False, runtime_fault=None))
    state = runtime.status()
    assert state['phase'] == 'running' and state['policy_fault'] is None and state['error'] is None

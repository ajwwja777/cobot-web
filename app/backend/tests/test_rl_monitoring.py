import json
from pathlib import Path
import runpy
import time
import pytest
from cobot_console import rl_monitoring as module


@pytest.fixture
def api(monkeypatch):
    root=Path(__file__).resolve().parents[3].parent/'rl-platform/methods/expo_ft/cobot_adapter'
    functions=runpy.run_path(str(root/'monitoring.py'))
    monkeypatch.setattr(module,'contract',lambda:functions)
    from cobot_console import execution_options
    monkeypatch.setattr(execution_options,'expo_contract',lambda:runpy.run_path(str(root/'online_options.py')))
    return functions


def model(root):
    return dict(id='expo',runtime_adapter='expo_online',online_run_dir=str(root),
                expo_online_options=dict(defaults=dict(rtc_delay=5,edit_scale=.01)))


def test_run_parameter_isolation(api,tmp_path):
    assert module.run_directory(model(tmp_path),dict(rtc_delay=5,edit_scale=.01))==tmp_path
    assert str(module.run_directory(model(tmp_path),dict(rtc_delay=3,edit_scale=.07))).endswith('--d3-edit007')
    with pytest.raises(ValueError):module.run_directory(model(tmp_path),dict(rtc_delay=9,edit_scale=.01))


def test_history_survives_disconnect_and_new_reader(api,tmp_path):
    run=tmp_path/'run';cache=tmp_path/'cache'
    api['write_json'](run/'telemetry/learner.json',dict(points=[dict(critic_step=20,q=.1,target_q_mean=.2,critic_loss=.01)],updated_at=1))
    api['write_json'](run/'telemetry/rollout.json',dict(episode_uuid='old',points=[dict(seconds=1,q=.3)]))
    reader=module.MonitoringReader(cache)
    try:
        state=dict(model=model(run),phase='paused',online=dict(phase='ready',active_episode='new',actor_version=2))
        result=reader.read(model(run),state)
        assert result['live'] and result['rollout']['points']==[]
        state['online']=dict(phase='fault')
        result=reader.read(model(run),state)
        assert not result['live'] and result['training']['points'][0]['critic_step']==20
    finally:reader.close()
    second=module.MonitoringReader(cache)
    try:assert second.read(model(run),dict(phase='offline'))['training']['points']==result['training']['points']
    finally:second.close()


def test_history_read_has_no_control_calls_and_uses_cached_hil(api,tmp_path):
    import numpy as np
    run=tmp_path/'run';ep=run/'episodes/one';ep.mkdir(parents=True)
    api['write_json'](ep/'finished.json',dict(episode_uuid='one',count=1,outcome='success',finished_at=1))
    np.savez(ep/'000000.npz',is_hil=np.bool_(True))
    reader=module.MonitoringReader(tmp_path/'cache')
    try:
        result=reader.scan(run,'key')
        assert result['points'][0]['rate']==0 and result['points'][0]['assisted']==1
        api['episode_summary']=lambda _:pytest.fail('should use immutable finished cache')
        assert reader.scan(run,'key')==result
    finally:reader.close()


def test_http_is_read_only_and_accepts_only_registered_runs(api,tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from cobot_console import deployment
    chosen=model(tmp_path/'run')
    monkeypatch.setattr(deployment,'catalog',lambda:[chosen])
    monkeypatch.setattr(module,'RUNTIME_ROOT',tmp_path/'runtime')
    app=FastAPI();module.install_routes(app,SimpleNamespace(status=lambda:dict(phase='offline')))
    with TestClient(app) as client:
        result=client.get('/api/collection/rl-monitoring',params=dict(model_id='expo',rtc_delay=3,edit_scale=.07))
        assert result.status_code==200 and result.json()['options']['rtc_delay']==3
        assert client.get('/api/collection/rl-monitoring',params=dict(model_id='../../etc')).status_code==404
        assert client.get('/api/collection/rl-monitoring',params=dict(model_id='expo',rtc_delay=99,edit_scale=.01)).status_code==422
        assert client.post('/api/collection/rl-monitoring',json={}).status_code==405

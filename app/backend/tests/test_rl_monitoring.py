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


def test_independent_snapshot_survives_actor_unload_and_connection_loss(api,tmp_path,monkeypatch):
    from concurrent.futures import Future
    chosen=model(tmp_path/'run');cache=tmp_path/'cache';calls=[]
    class Pool:
        def submit(self, fn, *args):
            future=Future();calls.append(future);return future
        def shutdown(self,**kwargs):pass
    remote=dict(profile_for_model=lambda *a:dict(rtc_delay=5,edit_scale=.01),fetch=lambda *a:None)
    monkeypatch.setattr(module,'remote_contract',lambda:remote)
    reader=module.MonitoringReader(cache);reader.remote_pool.shutdown();reader.remote_pool=Pool()
    options=dict(rtc_delay=5,edit_scale=.01)
    snapshot=dict(model_id='expo',options=options,running=True,phase='updating',learner_version=49,sampled_at=10,
                  training=dict(points=[dict(critic_step=980,q=.2)],updated_at=10))
    try:
        result=reader.read(chosen,dict(phase='offline'),options)
        assert result['learner']['loading'] and not result['live']
        calls[0].set_result(snapshot)
        result=reader.read(chosen,dict(phase='offline'),options)
        assert result['live'] and not result['actor_loaded'] and result['training']==snapshot['training']
        assert result['learner']['learner_version']==49 and result['actor_version'] is None
        key=result['run_key'];reader.remote_jobs[key]['next_scan']=0
        reader.read(chosen,dict(phase='offline'),options)
        calls[1].set_exception(OSError('network unavailable'))
        result=reader.read(chosen,dict(phase='offline'),options)
        assert not result['live'] and not result['learner']['connected']
        assert result['training']==snapshot['training']
        # Changed parameter identity never inherits the preceding run's samples.
        other=reader.read(chosen,dict(phase='offline'),dict(rtc_delay=3,edit_scale=.07))
        assert other['run_key']!=key and not other['training']
    finally:reader.close()
    second=module.MonitoringReader(cache);second.remote_pool.shutdown();second.remote_pool=Pool()
    try:
        result=second.read(chosen,dict(phase='offline'),options)
        assert result['training']==snapshot['training'] and not result['live']
        # A reachable stopped Learner is distinct from a network failure.
        calls[-1].set_result({**snapshot,'running':False,'phase':'stopped'})
        result=second.read(chosen,dict(phase='offline'),options)
        assert result['learner']['connected'] and result['learner']['phase']=='stopped' and not result['live']
    finally:second.close()


def test_rlt_history_independent_of_loaded_model_and_run_isolation(tmp_path, monkeypatch):
    import runpy
    root=Path(__file__).resolve().parents[3].parent/'rl-platform/methods/openpi_rlt/cobot_adapter'
    monkeypatch.setattr(module,'rlt_contract',lambda:runpy.run_path(str(root/'monitoring.py')))
    chosen=dict(id='rlt-online',kind='rlt',mode='online',training_enabled=True,online_run_root=str(tmp_path/'one'))
    run=tmp_path/'one/online/metrics';run.mkdir(parents=True)
    (run/'learner_status.json').write_text(json.dumps(dict(global_step=101,warmup_required_updates=100,timestamp=time.time())))
    (run/'learner_metrics.jsonl').write_text(json.dumps(dict(global_step=101,q1_mean=.25,target_q_mean=.3,critic_loss=.02,timestamp=1))+'\n')
    reader=module.MonitoringReader(tmp_path/'cache')
    try:
        state=dict(model=dict(id='frozen'),phase='ready')
        result=reader.read(chosen,state)
        for _ in range(100):
            if result['training'].get('points'):break
            time.sleep(.01);result=reader.read(chosen,state)
        assert result['algorithm']=='rlt' and result['training']['points'][0]['q']==.25
        assert result['live'] and not result['actor_loaded']
        other=reader.read(dict(chosen,id='branch2',online_run_root=str(tmp_path/'two')),state)
        assert other['run_key']!=result['run_key'] and not other['training']
        assert not module.is_rlt_online(dict(chosen,mode='frozen'))
    finally:reader.close()


def test_supported_rlt_uses_candidate_run_not_original_replay(tmp_path):
    chosen=dict(id='teacher',kind='rlt',mode='online',training_enabled=True,
                supported_model_root=str(tmp_path),supported_runtime='candidates/teacher')
    assert module.run_directory(chosen)==tmp_path/'candidates/teacher/online'
    with pytest.raises(ValueError):module.run_directory(dict(chosen,supported_runtime='../other'))


def test_saved_rollout_selectable_after_next_round_and_without_actor(api,tmp_path):
    chosen=model(tmp_path/'run');root=Path(chosen['online_run_dir'])
    reader=module.MonitoringReader(tmp_path/'cache')
    try:
        api['write_json'](root/'telemetry/rollout.json',dict(episode_uuid='one',actor_version=1,points=[dict(seconds=1,timestamp=10,q=.1)]))
        first=reader.read_history_view(chosen,dict(phase='offline'))
        assert len(first['rollout_episodes'])==1
        api['write_json'](root/'telemetry/rollout.json',dict(episode_uuid='two',actor_version=2,points=[dict(seconds=2,timestamp=20,q=.2)]))
        old=reader.read_history_view(chosen,dict(phase='offline'),episode='one')
        assert old['rollout']['historical'] and not old['rollout']['active']
        assert old['rollout']['points'][0]['q']==.1 and len(old['rollout_episodes'])==2
        with pytest.raises(ValueError):reader.read_history_view(chosen,{},episode='../../etc/passwd')
    finally:reader.close()
    new=module.MonitoringReader(tmp_path/'cache')
    try:assert new.read_history_view(chosen,{},episode='one')['rollout']['actor_version']==1
    finally:new.close()


def test_background_archive_without_browser(tmp_path):
    import time
    from types import SimpleNamespace
    from cobot_console.rl_monitoring import MonitoringReader, archive_contract
    root = tmp_path / 'run'
    (root / 'telemetry').mkdir(parents=True)
    model = dict(id='background', runtime_adapter='expo_online', online_run_dir=str(root))
    reader = MonitoringReader(tmp_path / 'cache')
    reader.start_archiving(SimpleNamespace(status=lambda: {'model': model}), lambda: [model])
    try:
        for uid in ('one', 'two'):
            (root / 'telemetry/rollout.json').write_text(json.dumps(dict(episode_uuid=uid, points=[dict(seconds=1, timestamp=time.time(), q=.2)])))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                rows, item = archive_contract()['history'](root, uid)
                if item:
                    break
                time.sleep(.05)
            assert item and item['episode_uuid'] == uid
        assert len(rows) == 2
    finally:
        reader.close()

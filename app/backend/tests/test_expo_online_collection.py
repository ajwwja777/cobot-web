from fastapi.testclient import TestClient
import pytest
from cobot_console.collection_model import CollectionModel
from cobot_console.deployment import DeploymentError
from tests.test_collection_model import manager
from tests.test_segmented_capture_http import FakeService,FakeBridge,start_payload,version
from segmented_capture.api import create_app


def setup(tmp_path):
    m=manager();m.runtime.status=lambda:{'phase':m.runtime.phase,'model':{'id':'expo-online','kind':'vla','runtime_adapter':'expo_online'},'online':{'phase':'ready'}}
    service=FakeService(tmp_path);app=create_app(service=service,bridge=FakeBridge(),allowed_data_root=tmp_path)
    control=CollectionModel(m);app.state.collection_model=control;events=[]
    def request(path,body=None):
        if path=='/episode/end':assert getattr(service,'outcome',None)==(body or {}).get('outcome')
        events.append((path,body));return {'actor_version':0}
    control.online_request=request
    return m,service,app,control,events


def test_success_is_finalized_after_durable_recording_and_label(tmp_path):
    m,service,app,control,events=setup(tmp_path)
    with TestClient(app) as client:
        client.post('/api/segmented-teach/start',json={**start_payload(tmp_path),'use_model':True,'collection_model_id':'expo-online'}).raise_for_status()
        body={**version(service),'outcome':'success'}
        client.post('/api/segmented-teach/stop',json=body).raise_for_status()
        client.post('/api/segmented-teach/stop',json=body).raise_for_status()
    assert [x[0] for x in events]==['/episode/start','/episode/resume','/episode/pause','/episode/end']
    assert not control.episode_uuid and not control.episode_model
    assert m.runtime.calls==['resume','pause']


def test_learning_blocks_new_episode_before_recorder_start(tmp_path):
    m,service,app,control,events=setup(tmp_path)
    m.runtime.status=lambda:{'phase':'paused','model':{'id':'expo-online','kind':'vla','runtime_adapter':'expo_online'},'online':{'phase':'updating'}}
    with pytest.raises(DeploymentError,match='正在更新'):control.before_start(True,'expo-online')
    assert not events and not m.runtime.calls


def test_online_rpc_failure_keeps_identity_for_finish_retry(tmp_path):
    m,service,app,control,events=setup(tmp_path)
    with TestClient(app) as client:
        client.post('/api/segmented-teach/start',json={**start_payload(tmp_path),'use_model':True,'collection_model_id':'expo-online'}).raise_for_status()
        original=control.online_request
        def failed(path,body=None):
            if path=='/episode/end':raise DeploymentError('lost reply')
            return original(path,body)
        control.online_request=failed;body={**version(service),'outcome':'success'}
        reply=client.post('/api/segmented-teach/stop',json=body)
        assert reply.status_code==503 and control.episode_uuid
        control.online_request=original
        client.post('/api/segmented-teach/stop',json=body).raise_for_status()
    assert not control.episode_uuid


def test_lost_start_reply_keeps_identity_and_blocks_another_recording(tmp_path):
    m,service,app,control,events=setup(tmp_path)
    control.before_start(True,'expo-online')
    def lost(*args,**kwargs):raise DeploymentError('lost start reply')
    control.online_request=lost
    with pytest.raises(DeploymentError):control.started({'episode_uuid':'example'})
    assert control.episode_uuid=='example'
    with pytest.raises(DeploymentError,match='上一轮'):control.before_start(True,'expo-online')


def test_online_contract_requires_rtc():
    from cobot_console.execution_options import configured_model
    model={'id':'expo-online','kind':'vla','runtime_adapter':'expo_online'}
    assert configured_model(model)['execution_settings']['rtc']
    with pytest.raises(ValueError,match='RTC'):
        configured_model(model,{'enabled':True,'publish_hz':50,'rtc':False,'smoothing':True})

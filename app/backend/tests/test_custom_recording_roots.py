import json
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from segmented_capture.api import create_app
from tests.test_segmented_capture_http import FakeBridge, FakeService

def client_for(tmp_path, monkeypatch):
    mount=tmp_path/'Getea1';mount.mkdir()
    primary=mount/'jiaan/data';primary.mkdir(parents=True)
    monkeypatch.setenv('COBOT_ADDITIONAL_DATA_ROOTS',json.dumps([str(mount)]))
    service=FakeService(primary)
    client=TestClient(create_app(service=service,bridge=FakeBridge(),allowed_data_root=primary))
    return client,mount,primary,service

def test_custom_volume_browse_prepare_and_history_use_one_allowed_scope(tmp_path,monkeypatch):
    client,mount,primary,service=client_for(tmp_path,monkeypatch)
    selected=mount/'xiqing/new-recordings'
    with client:
        response=client.get('/api/segmented-teach/storage/directories',params={'path':str(mount)+'/'})
        assert response.status_code==200
        response=client.get('/api/segmented-teach/storage/directories',params={'path':str(selected)+'/'})
        assert response.status_code==200 and response.json()['exists'] is False
        response=client.post('/api/segmented-teach/storage/prepare',json={'data_root':str(selected),'task_id':'plug','model_id':'expert','dataset_round':'custom','storage_layout':'flat'})
        assert response.status_code==200,response.text
        assert response.json()['data_root']==str(selected)
        assert selected.is_dir()
        response=client.get('/api/segmented-teach/history',params={'data_root':str(selected)})
        assert response.status_code==200,response.text
        assert service.history_data_root==selected
        assert client.app.state.resolve_data_root(str(selected))==selected
        assert primary in client.app.state.allowed_data_roots()

def test_custom_scope_rejects_other_volume_and_symlink_escape(tmp_path,monkeypatch):
    client,mount,primary,_=client_for(tmp_path,monkeypatch)
    outside=tmp_path/'Getea1-other';outside.mkdir()
    (mount/'escape').symlink_to(outside,target_is_directory=True)
    with client:
        for path in [outside/'data',mount/'escape/data']:
            response=client.get('/api/segmented-teach/storage/directories',params={'path':str(path)})
            assert response.status_code==422,response.text
            response=client.post('/api/segmented-teach/storage/prepare',json={'data_root':str(path)})
            assert response.status_code==422,response.text
            assert not path.exists()

def test_web_host_roots_are_added_without_changing_default_paths(tmp_path,monkeypatch):
    from cobot_console import paths
    monkeypatch.delenv('COBOT_ADDITIONAL_DATA_ROOTS',raising=False)
    original=paths.DATA
    monkeypatch.setattr(paths,'SETTINGS',{'recording_allowed_roots':[str(tmp_path/'Getea1')]})
    defaults=paths.configure_environment()
    assert str(tmp_path/'Getea1') in json.loads(defaults['COBOT_ADDITIONAL_DATA_ROOTS'])
    assert defaults['TASK5_SEGMENTED_ALLOWED_DATA_ROOT']==str(original)

def test_rlt_storage_accepts_the_same_additional_scope(tmp_path,monkeypatch):
    from cobot_console import profile_storage
    mount=tmp_path/'Getea1';mount.mkdir()
    monkeypatch.setattr(profile_storage,'ALLOWED',mount/'jiaan/data')
    monkeypatch.setattr(profile_storage,'LEGACY_BASE',tmp_path/'legacy')
    monkeypatch.setenv('COBOT_ADDITIONAL_DATA_ROOTS',json.dumps([str(mount)]))
    assert profile_storage.validate_root(str(mount/'xiqing'))==mount/'xiqing'
    with pytest.raises(ValueError):profile_storage.validate_root(str(tmp_path/'elsewhere'))
    (mount/'escape').symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError):profile_storage.validate_root(str(mount/'escape/elsewhere'))

def test_preflight_and_historical_label_validation_accept_additional_scope(tmp_path,monkeypatch):
    from cobot_console import api
    from tests.test_console_api import FakeBridge as ConsoleBridge,FakeRecorder,FakeSegmentedService,FakeRegistry,_fresh_cache
    mount=tmp_path/'Getea1';mount.mkdir();primary=mount/'jiaan/data';primary.mkdir(parents=True)
    custom=mount/'xiqing';custom.mkdir()
    monkeypatch.setenv('COBOT_ADDITIONAL_DATA_ROOTS',json.dumps([str(mount)]))
    monkeypatch.setattr(api,'RUNTIME_ROOT',tmp_path/'runtime')
    class Offline:
        def request(self,*args,**kwargs):
            from cobot_console.rlt_proxy import RltBackendError
            raise RltBackendError('not loaded')
    app=api.create_app(cache=_fresh_cache(),bridge=ConsoleBridge(),recorder=FakeRecorder(),segmented_service=FakeSegmentedService(),backend_client=Offline(),lifecycle_registry=FakeRegistry('loading_actor'),allowed_data_root=primary,rlt_data_root=primary,monotonic=lambda:10.)
    monkeypatch.setattr(api, "evaluate_readiness", lambda *args: {"status":"not_ready", "error_code":"stale_frames"})
    with TestClient(app) as client:
        response=client.post("/api/rlt/recorder-check",json={"data_root":str(custom)})
        assert response.status_code==200,response.text
        assert response.json()["preflight"]["data_root"]==str(custom)
        assert response.json()["preflight"]["error_code"]=="stale_frames"
        config=client.get('/api/console/config').json()
        assert str(mount) in config['allowed_data_roots']
        # No sidecar exists: the request reaches label lookup rather than root rejection.
        response=client.get('/api/recordings/'+str(uuid4())+'/labels',params={'data_root':str(custom)})
        assert response.status_code==409,response.text
        assert response.json()['detail']=='recording_not_complete_or_labels_invalid'

import json,fcntl
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
import cobot_console.api as api
from cobot_console.rlt_proxy import BackendResponse
from segmented_capture.capture_service import SegmentedCaptureService
from segmented_capture.ports import CaptureGate
from capture_core.hdf5_writer import Hdf5EpisodeWriter
from capture_core.sampler import FrameSampler
from tests.test_console_api import FakeBridge,FakeRegistry,FakeRecorder,FakeSegmentedService,_fresh_cache


class Backend:
    def __init__(self):self.phase="stopped"
    def request(self,method,path,body=None):
        assert method=="GET"
        return BackendResponse(200,{"phase":self.phase,"data_phase":"warmup","policy_paused":True})


def fixture_app(project_tmp,monkeypatch):
    storage=api.load_rlt_storage()
    storage.ALLOWED=project_tmp/'data';storage.BASE=storage.ALLOWED/'rlt/plug_v2'
    storage.RUN=project_tmp/'run';storage.SETTINGS=storage.RUN/'backend/storage.json'
    monkeypatch.setattr(api,"load_rlt_storage",lambda:storage)
    monkeypatch.setenv("COBOT_DATA_PROFILE","plug_v2")
    backend=Backend()
    app=api.create_app(cache=_fresh_cache(),bridge=FakeBridge(),recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(),backend_client=backend,
        lifecycle_registry=FakeRegistry("ready_disarmed"),allowed_data_root=storage.ALLOWED,
        rlt_data_root=storage.BASE/'warmup',monotonic=lambda:10.)
    return TestClient(app),storage,backend


def test_rlt_directory_create_selection_roundtrip_and_replay_registry(project_tmp,monkeypatch):
    client,storage,backend=fixture_app(project_tmp,monkeypatch)
    target=storage.ALLOWED/'custom/warmup'
    response=client.post('/api/rlt/storage',json={'data_root':str(target)})
    assert response.status_code==200 and target.is_dir()
    assert client.get('/api/rlt/storage').json()['data_root']==str(target)
    assert storage.selected_root('warmup')==target
    assert target in storage.roots_for_phase('warmup')
    release=target/'lerobot'/str(uuid4())/'conversion.json';release.parent.mkdir(parents=True)
    release.write_text(json.dumps({'cohort':'plug_v2','source_uuid':release.parent.name,'metadata':{'data_phase':'warmup'}}))
    assert storage.releases_for_phase('warmup')==[release]


@pytest.mark.parametrize('phase',['rollout','hil','paused','recording_starting','replay_committing'])
def test_directory_change_during_active_episode_refused(project_tmp,monkeypatch,phase):
    client,storage,backend=fixture_app(project_tmp,monkeypatch);backend.phase=phase
    target=storage.ALLOWED/'new'
    assert client.post('/api/rlt/storage',json={'data_root':str(target)}).status_code==409
    assert not target.exists()


def test_storage_guard_rejects_escape_experts_and_learning_lease(project_tmp,monkeypatch):
    client,storage,backend=fixture_app(project_tmp,monkeypatch)
    for target in [project_tmp/'escape',storage.BASE/'demonstrations']:
        assert client.post('/api/rlt/storage',json={'data_root':str(target)}).status_code==422
        assert not target.exists()
    directory=storage.RUN/'learning';directory.mkdir(parents=True,exist_ok=True)
    with (directory/'operation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert client.post('/api/rlt/storage',json={'data_root':str(storage.ALLOWED/'new')}).status_code==409


def test_kept_counts_history_exclude_aborted_and_show_hil(project_tmp,monkeypatch):
    client,storage,backend=fixture_app(project_tmp,monkeypatch)
    target=storage.BASE/'warmup';target.mkdir(parents=True)
    for i,outcome in enumerate(['failure','aborted','success'],1):
        (target/('episode_%06d.rlt.json'%i)).write_text(json.dumps({'cohort':'plug_v2','episode_uuid':str(uuid4()),'episode_index':i,'outcome':outcome,'recording_enabled':True,'shadow':False,'hil_frames':10 if outcome=='success' else 0,'actor_version':-1}))
    payload=client.get('/api/rlt/history').json()
    assert payload['saved_count']==2 and [d['number'] for d in payload['episodes']]==[1,2]
    assert payload['episodes'][-1]['hil'] is True
    assert all(d['outcome']!='aborted' for d in payload['episodes'])
    counts=client.get('/api/console/status').json()['recording_counts']['warmup']
    assert counts['total']==2 and counts['aborted']==1


def test_native_normal_discard_deletes_only_current_and_releases_writer(project_tmp,monkeypatch):
    monkeypatch.delenv('COBOT_DATA_PROFILE',raising=False)
    cache=_fresh_cache();gate=CaptureGate(enabled=False)
    class Recorder(FakeRecorder):
        def start(self,request):
            self.request=request;self.state='recording'
            self.writer=Hdf5EpisodeWriter(request.data_root,request.identity,4)
            self.writer.append(FrameSampler().sample(cache.snapshot(10.),request.identity,0))
            return self.status()
        def stop(self):
            self.writer.finalize();self.state='stopped';return self.writer.final_path
    recorder=Recorder();service=SegmentedCaptureService(recorder=recorder,cache=cache,gate=gate,sidecar_root=None,monitor=False,clock=lambda:10.)
    app=api.create_app(cache=cache,bridge=FakeBridge(),recorder=recorder,segmented_service=service,
        backend_client=Backend(),lifecycle_registry=FakeRegistry(),allowed_data_root=project_tmp,
        rlt_data_root=project_tmp,monotonic=lambda:10.)
    client=TestClient(app)
    request={'data_root':str(project_tmp),'storage_layout':'flat'}
    old=client.post('/api/segmented-teach/start',json=request).json()
    version={k:old[k] for k in ['episode_uuid','generation']}
    assert client.post('/api/segmented-teach/stop',json=version).status_code==200
    before=client.get('/api/segmented-teach/episodes',params={'data_root':str(project_tmp)}).json()
    fresh=client.post('/api/segmented-teach/start',json=request).json()
    version={k:fresh[k] for k in ['episode_uuid','generation']}
    discarded_file=recorder.writer.final_path
    assert client.post('/api/segmented-teach/discard',json={**version,'generation':version['generation']-1}).status_code==409
    assert client.post('/api/segmented-teach/discard',json=version).status_code==200
    after=client.get('/api/segmented-teach/episodes',params={'data_root':str(project_tmp)}).json()
    assert len(after)==len(before)==1 and after[0]['episode_uuid']==old['episode_uuid']
    assert not discarded_file.exists()
    assert client.get('/api/console/status').json()['active_mode'] is None
    assert client.post('/api/segmented-teach/start',json=request).status_code==200
    last=service.status();client.post('/api/segmented-teach/discard',json={k:last[k] for k in ['episode_uuid','generation']})


def test_native_normal_discard_removes_zero_frame_incomplete_episode(project_tmp,monkeypatch):
    monkeypatch.delenv('COBOT_DATA_PROFILE',raising=False)
    cache=_fresh_cache();gate=CaptureGate(enabled=False)
    class EmptyRecorder(FakeRecorder):
        def start(self,request):
            self.request=request;self.state='recording'
            self.writer=Hdf5EpisodeWriter(request.data_root,request.identity,4)
            return self.status()
        def stop(self):
            path=self.writer.abort('empty_episode');self.state='stopped';return path
    recorder=EmptyRecorder();service=SegmentedCaptureService(recorder=recorder,cache=cache,gate=gate,sidecar_root=None,monitor=False,clock=lambda:10.)
    app=api.create_app(cache=cache,bridge=FakeBridge(),recorder=recorder,segmented_service=service,
        backend_client=Backend(),lifecycle_registry=FakeRegistry(),allowed_data_root=project_tmp,
        rlt_data_root=project_tmp,monotonic=lambda:10.)
    client=TestClient(app)
    started=client.post('/api/segmented-teach/start',json={'data_root':str(project_tmp),'storage_layout':'flat'}).json()
    version={k:started[k] for k in ['episode_uuid','generation']}
    incomplete=recorder.writer.incomplete_path

    response=client.post('/api/segmented-teach/discard',json=version)

    assert response.status_code==200
    assert response.json()['capture_state']=='idle'
    assert not incomplete.exists()
    assert client.get('/api/segmented-teach/episodes',params={'data_root':str(project_tmp)}).json()==[]
    assert client.get('/api/console/status').json()['active_mode'] is None

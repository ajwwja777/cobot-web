import hashlib
import fcntl
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from capture_core.deferred import metadata, defer_file, list_deferred, delete_deferred
from capture_core.history import combined_history
from capture_core.labels import LabelStore
from capture_core.api import create_app
from cobot_console.mode import RecorderModeCoordinator
from tests.test_labels import _write_episode
from tests.test_console_api import FakeRecorder
from tests.test_recorder_recovery import fixture

def expected(root,path):
    result=metadata(path);result['relative_path']=str(path.relative_to(root));return result

def test_defer_preserves_bytes_visible_pending_then_explicit_delete(tmp_path):
    path,uuid=_write_episode(tmp_path,completion_state='incomplete')
    path=path.rename(Path(str(path)+'.incomplete'))
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    row=defer_file(tmp_path,expected(tmp_path,path))
    archive=tmp_path/row['archive_relative_path']
    assert not path.exists() and hashlib.sha256(archive.read_bytes()).hexdigest()==digest
    assert row['keep_for_training'] is False and row['episode_outcome']=='unknown'
    from types import SimpleNamespace
    rows=combined_history(tmp_path,SimpleNamespace(list_episodes=lambda **kw:[]))['episodes']
    assert len(rows)==1 and rows[0]['history_format']=='deferred' and rows[0]['review_pending']
    receipt=tmp_path/row['receipt_relative_path'];assert receipt.exists()
    result=delete_deferred(tmp_path,str(uuid))
    assert result['deleted'] and not archive.exists() and list_deferred(tmp_path)==[]
    assert json.loads(receipt.read_text())['status']=='deleted'

def test_defer_refuses_open_changed_and_escaping_files(tmp_path):
    path,_=_write_episode(tmp_path);snapshot=expected(tmp_path,path)
    with path.open('rb') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='still_open'):defer_file(tmp_path,snapshot)
    snapshot['size_bytes']+=1
    with pytest.raises(ValueError,match='changed'):defer_file(tmp_path,snapshot)
    with pytest.raises(ValueError):defer_file(tmp_path,{**snapshot,'relative_path':'../episode_000000.hdf5'})
    assert path.exists() and not list_deferred(tmp_path)

def test_receipt_visible_if_final_ledger_write_was_interrupted(tmp_path,monkeypatch):
    import capture_core.deferred as module
    path,_=_write_episode(tmp_path,completion_state='incomplete')
    original=module.atomic;calls=[]
    def crash(receipt,row):
        calls.append(True)
        if len(calls)==2:raise OSError('simulated interruption')
        original(receipt,row)
    monkeypatch.setattr(module,'atomic',crash)
    with pytest.raises(OSError):module.defer_file(tmp_path,expected(tmp_path,path))
    assert len(list_deferred(tmp_path))==1

def test_historical_unlabelled_relabel_preserves_hdf_markers_and_cas(tmp_path,monkeypatch):
    path,uuid=_write_episode(tmp_path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    store=LabelStore(tmp_path)
    store.update_labels(uuid,{'episode_uuid':str(uuid),'operator_nodes':[{'frame_index':2,'node_kind':'pause'}]})
    client,backend,recorder,manager=fixture(tmp_path,monkeypatch)
    route=f'/api/recordings/{uuid}/labels?data_root={tmp_path}'
    before=client.get(route).json()
    body=dict(episode_uuid=str(uuid),outcome='success',keep_for_training=True,operator_note='reviewed',expected_label_updated_at=before['label_updated_at'])
    response=client.put(route,json=body)
    assert response.status_code==200,response.text
    saved=response.json()
    assert saved['operator_nodes']==before['operator_nodes'] and saved['replay_modified'] is False
    assert saved['episode_outcome']=='success' and saved['keep_for_training']=='true'
    assert client.put(route,json=body).status_code==409
    body.update(outcome='unknown',expected_label_updated_at=saved['label_updated_at'])
    changed=client.put(route,json=body)
    assert changed.status_code==200,changed.text
    assert changed.json()['keep_for_training']=='false' and changed.json()['episode_outcome']=='unknown'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest and not backend.calls
    assert recorder.state=='idle' and manager.operation is None

def recorder_client(root,*,stuck=False):
    modes=RecorderModeCoordinator(initial_mode='rlt');recorder=FakeRecorder()
    recorder.check_ready=lambda root:Path(root)
    recorder.status=lambda:{'state':recorder.state,'path':str(root/'episode_000000.hdf5') if recorder.request else None,
        'publication_status':'committed' if recorder.state=='stopped' else 'open',
        'completion_state':'complete','writer_thread_alive':stuck and recorder.state=='stopped'}
    app=create_app(recorder=recorder,label_store=LabelStore(root),writer_coordinator=modes,require_previous_labels=False)
    return TestClient(app),modes,recorder

@pytest.mark.parametrize('missing_uuid',[False,True])
def test_defer_matches_owned_start_even_after_response_lost(tmp_path,missing_uuid):
    client,modes,recorder=recorder_client(tmp_path)
    identity=dict(data_root=str(tmp_path),task_id='test',model_id='rlt',checkpoint_id='4999',dataset_round='online')
    started=client.post('/api/episodes/start',json={**identity,'storage_layout':'flat','episode_index':0})
    assert started.status_code==200,started.text
    uuid=started.json()['episode_uuid']
    body={**identity,'episode_index':0,'episode_uuid':None if missing_uuid else uuid}
    wrong=client.post('/api/episodes/defer',json={**body,'episode_uuid':None,'episode_index':1})
    assert wrong.status_code==409 and modes.snapshot().active_mode=='rlt'
    response=client.post('/api/episodes/defer',json=body)
    assert response.status_code==200,response.text
    assert response.json()['deferred'] and response.json()['replay_inserted'] is False
    assert modes.snapshot().active_mode is None
    assert client.post('/api/episodes/defer',json=body).json()==response.json()

def test_defer_never_releases_stuck_worker(tmp_path):
    client,modes,recorder=recorder_client(tmp_path,stuck=True)
    identity=dict(data_root=str(tmp_path),task_id='test',model_id='rlt',checkpoint_id='4999',dataset_round='online')
    started=client.post('/api/episodes/start',json={**identity,'storage_layout':'flat'})
    response=client.post('/api/episodes/defer',json={**identity,'episode_uuid':started.json()['episode_uuid']})
    assert response.status_code==409 and modes.snapshot().active_mode=='rlt'

def test_minimal_outcome_autosave_preserves_existing_metadata(tmp_path,monkeypatch):
    path,uuid=_write_episode(tmp_path)
    store=LabelStore(tmp_path)
    store.update_labels(uuid,{'episode_uuid':str(uuid),'operator_note':'existing note','keep_for_training':'true'})
    client,backend,recorder,manager=fixture(tmp_path,monkeypatch)
    route=f'/api/recordings/{uuid}/labels?data_root={tmp_path}'
    current=client.get(route).json()
    result=client.put(route,json=dict(episode_uuid=str(uuid),outcome='failure',expected_label_updated_at=current['label_updated_at']))
    assert result.status_code==200,result.text
    assert result.json()['operator_note']=='existing note'
    assert result.json()['keep_for_training']=='true'
    assert not backend.calls

def test_historical_alias_matches_history_root_and_saves_only_real_sidecar(tmp_path,monkeypatch):
    from cobot_console import paths
    actual=tmp_path/'new'; actual.mkdir()
    source,uuid=_write_episode(actual)
    store=LabelStore(actual); store.set_outcome(uuid,'success')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    old=tmp_path/'old'
    monkeypatch.setitem(paths.SETTINGS,'data_root_aliases',{str(old):str(actual)})
    client,_,_,_=fixture(tmp_path,monkeypatch)
    route=f'/api/recordings/{uuid}/labels?data_root={old}'
    initial=client.get(route);assert initial.status_code==200,initial.text
    assert initial.json()['episode_outcome']=='success'
    saved=client.put(route,json=dict(episode_uuid=str(uuid),outcome='failure',expected_label_updated_at=initial.json()['label_updated_at']))
    assert saved.status_code==200,saved.text
    assert store.get_labels(uuid)['episode_outcome']=='failure'
    assert not old.exists() and hashlib.sha256(source.read_bytes()).hexdigest()==digest

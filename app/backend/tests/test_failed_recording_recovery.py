import json,hashlib
from uuid import uuid4
from pathlib import Path
import h5py,pytest
from capture_core.recovery import quarantine_failed_files
from capture_core.storage import SeriesIdentity,prepare_series
from capture_core.series_inspection import inspect_prepared_series
from capture_core.labels import LabelStore,LabelValidationError
from tests.test_rollout_ui_contract import fixture_app

SERIES=SeriesIdentity('plug_insertion','plug_v2','plug_v2',storage_layout='flat')
def failed(root,index=4,state='error',model='plug_v2'):
    root.mkdir(parents=True,exist_ok=True);p=root/('episode_%06d.hdf5.incomplete'%index)
    with h5py.File(p,'w') as h:
        h.attrs.update(task_id='plug_insertion',model_id=model,dataset_round='plug_v2',episode_uuid=str(uuid4()),episode_index=index,completion_state=state,failure_reason='shutdown_timeout')
        h.create_dataset('source_facts',data=[1,2,3])
    return p


def test_failed_file_archived_intact_reserved_and_not_counted(project_tmp):
    p=failed(project_tmp);digest=hashlib.sha256(p.read_bytes()).hexdigest()
    result=quarantine_failed_files(project_tmp,SERIES)
    assert len(result)==1 and not p.exists()
    archive=Path(result[0]['archive']);assert hashlib.sha256(archive.read_bytes()).hexdigest()==digest
    assert result[0]['keep_for_training'] is False
    prepared=prepare_series(project_tmp,SERIES);assert prepared.next_episode_index==5
    status=inspect_prepared_series(LabelStore(project_tmp),prepared,SERIES)
    assert status['episode_count']==0 and status['label_blocked'] is False
    assert quarantine_failed_files(project_tmp,SERIES)==[]


@pytest.mark.parametrize('state,model',[('recording','plug_v2'),('error','other_model')])
def test_unknown_or_other_model_incomplete_stays_blocked(project_tmp,state,model):
    p=failed(project_tmp,state=state,model=model)
    assert quarantine_failed_files(project_tmp,SERIES)==[] and p.exists()
    with pytest.raises(LabelValidationError,match='latest_episode_incomplete'):
        inspect_prepared_series(LabelStore(project_tmp),prepare_series(project_tmp,SERIES),SERIES)


def test_archive_symlink_refused_without_moving_source(project_tmp):
    p=failed(project_tmp);target=project_tmp/'elsewhere';target.mkdir();(project_tmp/'.failed').symlink_to(target,target_is_directory=True)
    with pytest.raises(LabelValidationError,match='symlink'):quarantine_failed_files(project_tmp,SERIES)
    assert p.exists() and not list(target.iterdir())


def test_flat_prepare_after_failed_writer_and_api_restart_is_ready(project_tmp,monkeypatch):
    client,storage,backend=fixture_app(project_tmp,monkeypatch);root=storage.BASE/'warmup';p=failed(root)
    request={'data_root':str(root),'task_id':'plug_insertion','model_id':'plug_v2','checkpoint_id':'3999','dataset_round':'plug_v2','storage_layout':'flat'}
    for _ in range(2):
        response=client.post('/api/rlt-recorder/api/storage/prepare',json=request)
        assert response.status_code==200,response.text
        assert response.json()['next_episode_index']==5 and response.json()['label_blocked'] is False
    assert not p.exists() and len(list((root/'.failed').glob('*/*.incomplete')))==1
    assert client.get('/api/rlt/history').json()['saved_count']==0

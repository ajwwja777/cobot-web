import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from segmented_capture.capture_service import SegmentedCaptureService
from segmented_capture.ports import CaptureGate
from segmented_capture.node_outcome import write_node_outcome
from segmented_capture.api import create_app
from tests.test_segmented_capture_service import FakeRecorder, ready_cache, identity
from tests.test_segmented_capture_http import FakeBridge, start_payload
from capture_core.history import combined_history


def service_for(root):
    return SegmentedCaptureService(recorder=FakeRecorder(), cache=ready_cache('policy'),
        gate=CaptureGate(enabled=False), sidecar_root=None, clock=lambda:10., monitor=False)


@pytest.mark.parametrize('outcome',['failure','success','unknown'])
def test_zero_hil_result_is_durable_and_visible_without_fake_hdf5(tmp_path,outcome):
    service=service_for(tmp_path);started=service.start(identity(),data_root=tmp_path)
    result=service.stop();before=json.loads(result.sidecar.read_text())
    service.label_outcome(outcome);first=result.sidecar.read_bytes();service.label_outcome(outcome)
    assert result.sidecar.read_bytes()==first
    saved=json.loads(first);assert saved['episode_outcome']==outcome
    assert saved['nodes']==before['nodes'] and saved['training_frame_count']==0
    assert not list(tmp_path.rglob('*.hdf5'))
    rows=combined_history(tmp_path,service)['episodes'];assert len(rows)==1
    assert rows[0]['episode_outcome']==outcome and rows[0]['has_hil'] is False
    assert rows[0]['labelable'] is False  # HDF5 label editor remains unavailable.


@pytest.mark.parametrize('change',[{'training_frame_count':1},{'commit_state':'recording'},
    {'episode_uuid':'00000000-0000-0000-0000-000000000000'},{'training_intervals':[{}]},
    {'active_segments':[{}]}])
def test_node_outcome_rejects_unproven_or_nonempty_episode(tmp_path,change):
    service=service_for(tmp_path);started=service.start(identity(),data_root=tmp_path);result=service.stop()
    value=json.loads(result.sidecar.read_text());value.update(change);result.sidecar.write_text(json.dumps(value))
    before=result.sidecar.read_bytes()
    with pytest.raises(ValueError):write_node_outcome(result.sidecar,started['episode_uuid'],'failure')
    assert result.sidecar.read_bytes()==before


def test_zero_hil_http_failure_reaches_online_finalize_after_durable_label(tmp_path):
    service=service_for(tmp_path);events=[]
    def finalized(outcome,uuid):
        rows=combined_history(tmp_path,service)['episodes']
        assert rows[0]['episode_uuid']==str(uuid) and rows[0]['episode_outcome']==outcome=='failure'
        events.append('online_end')
    controller=SimpleNamespace(before_start=lambda *_:None,started=lambda *_:None,
        action=lambda name:events.append(name),finalized=finalized,finished=lambda:events.append('finished'),
        status=lambda:{'enabled':True,'running':False,'session_active':True})
    app=create_app(service=service,bridge=FakeBridge(),allowed_data_root=tmp_path)
    app.state.collection_model=controller
    with TestClient(app) as client:
        response=client.post('/api/segmented-teach/start',json={**start_payload(tmp_path),'use_model':True,'collection_model_id':'expo-online'})
        response.raise_for_status();value=response.json()
        result=client.post('/api/segmented-teach/stop',json={**{k:value[k] for k in ('episode_uuid','generation')},'outcome':'failure'})
        assert result.status_code==200,result.text
        assert result.json()['capture_state']=='committed'
    assert events==['start','stop','online_end','finished']


def test_node_result_does_not_hide_missing_hil_frames(tmp_path):
    service=service_for(tmp_path);started=service.start(identity(),data_root=tmp_path);result=service.stop()
    value=json.loads(result.sidecar.read_text());value['nodes'][0]['teach_mask']['left']=True
    result.sidecar.write_text(json.dumps(value));before=result.sidecar.read_bytes()
    with pytest.raises(ValueError):service.label_outcome('failure')
    assert result.sidecar.read_bytes()==before


def test_node_label_atomic_failure_preserves_original(tmp_path,monkeypatch):
    import segmented_capture.node_outcome as module
    service=service_for(tmp_path);service.start(identity(),data_root=tmp_path);result=service.stop()
    before=result.sidecar.read_bytes()
    def fail(*args):raise OSError('disk failed')
    monkeypatch.setattr(module.os,'replace',fail)
    with pytest.raises(OSError):service.label_outcome('failure')
    assert result.sidecar.read_bytes()==before
    assert not list(result.sidecar.parent.glob('.outcome-*.tmp'))

"""Collection model controls never create evaluation records or move on prepare."""
from threading import RLock
from types import SimpleNamespace

import pytest

from cobot_console.collection_model import CollectionModel
from cobot_console.deployment import DeploymentError, DeploymentManager


class Runtime:
    def __init__(self):
        self.calls = []
        self.phase = 'ready'
    def status(self):
        return {'phase': self.phase, 'model': {'id': 'dagger', 'kind': 'pi05'}}
    def action(self, name):
        self.calls.append(name)
        self.phase = 'running' if name in {'start','resume'} else 'paused'


def manager():
    runtime = Runtime()
    return SimpleNamespace(lock=RLock(), operation=None, active=None, runtime=runtime,
        refresh=lambda: None, status=runtime.status, busy=lambda: False)


def test_prepare_does_not_move_and_inference_is_explicit():
    m=manager(); control=CollectionModel(m)
    control.before_start(True,'dagger')
    assert m.runtime.calls == [] and m.collection_session
    control.action('start')
    control.action('pause')
    control.action('resume')
    control.action('stop')
    control.finished()
    assert m.runtime.calls == ['resume','pause','resume','pause']
    assert not control.status()['enabled'] and m.active is None


def test_session_start_stop_only_pause_and_keep_model(tmp_path):
    m=manager()
    m.allowed_root=tmp_path
    DeploymentManager.perform(m, 'collection_session_start')
    assert m.collection_session and m.runtime.calls == ['pause']
    DeploymentManager.perform(m, 'collection_session_stop')
    assert not m.collection_session and m.runtime.calls == ['pause','pause']
    m.busy=lambda: True
    with pytest.raises(DeploymentError):
        DeploymentManager.perform(m, 'collection_session_stop')


def test_mismatched_or_running_model_cannot_start():
    m=manager(); control=CollectionModel(m)
    with pytest.raises(DeploymentError): control.before_start(True, 'wrong')
    m.runtime.phase='running'
    with pytest.raises(DeploymentError): control.before_start(False)
    assert m.runtime.calls == []


def test_http_start_pause_resume_stop_coordinates_recorder(tmp_path):
    from fastapi.testclient import TestClient
    from segmented_capture.api import create_app
    from tests.test_segmented_capture_http import FakeService, FakeBridge, start_payload, version
    m=manager(); service=FakeService(tmp_path)
    app=create_app(service=service, bridge=FakeBridge(), allowed_data_root=tmp_path)
    app.state.collection_model=CollectionModel(m)
    with TestClient(app) as client:
        response=client.post('/api/segmented-teach/start',json={**start_payload(tmp_path),'use_model':True,'collection_model_id':'dagger'})
        assert response.status_code == 200, response.text
        assert response.json()['buttons']['pause']  # HIL-only recorder paused, inference running.
        paused=client.post('/api/segmented-teach/pause',json=version(service))
        assert paused.status_code == 200 and paused.json()['buttons']['resume']
        resumed=client.post('/api/segmented-teach/resume',json=version(service))
        assert resumed.status_code == 200 and resumed.json()['capture_state']=='recording'
        stopped=client.post('/api/segmented-teach/stop',json=version(service))
        assert stopped.status_code == 200 and stopped.json()['capture_state']=='committed'
    assert m.runtime.calls == ['resume','pause','resume','pause'] and m.active is None


@pytest.mark.parametrize('outcome',['success','failure','unknown',None])
def test_normal_collection_result_labels_are_optional(tmp_path,outcome):
    from fastapi.testclient import TestClient
    from segmented_capture.api import create_app
    from tests.test_segmented_capture_http import FakeService, FakeBridge, start_payload, version
    service=FakeService(tmp_path)
    app=create_app(service=service,bridge=FakeBridge(),allowed_data_root=tmp_path)
    with TestClient(app) as client:
        client.post('/api/segmented-teach/start',json=start_payload(tmp_path)).raise_for_status()
        request={**version(service),'outcome':outcome}
        reply=client.post('/api/segmented-teach/stop',json=request)
        assert reply.status_code==200,reply.text
        assert getattr(service,'outcome',None)==outcome
        assert client.post('/api/segmented-teach/stop',json=request).status_code==200


def test_real_saved_episode_labels_and_history_summary(tmp_path):
    from tests.test_labels import _write_episode
    from segmented_capture.capture_service import SegmentedCaptureService
    from capture_core.labels import LabelStore
    path,uuid=_write_episode(tmp_path)
    service=object.__new__(SegmentedCaptureService)
    service._lock=RLock();service._identity=SimpleNamespace(episode_uuid=uuid)
    service._recording_data_root=tmp_path;service.finalized_successfully=lambda: True
    service.label_outcome('failure')
    assert LabelStore(tmp_path).get_labels(uuid)['episode_outcome']=='failure'
    payload={'episode_uuid':str(uuid),'source_hdf5_relative':str(path.relative_to(tmp_path))}
    assert service._outcome_summary(payload,tmp_path)['episode_outcome']=='failure'
    service.label_outcome('unknown')
    assert LabelStore(tmp_path).get_labels(uuid)['keep_for_training']=='false'

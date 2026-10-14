import json
import time
from types import SimpleNamespace

import cv2
import pytest
from fastapi.testclient import TestClient
from segmented_capture.api import create_app
from segmented_capture.review_video import ReviewVideo, read_review_video
from tests.test_expo_node_only_outcome import service_for
from tests.test_segmented_capture_http import FakeBridge, start_payload
from tests.test_segmented_capture_service import identity, ready_cache


@pytest.fixture
def real_service(tmp_path):
    from capture_core.config import RecorderConfig
    from capture_core.recorder import RolloutRecorder
    from segmented_capture.ports import CaptureGate
    from segmented_capture.capture_service import SegmentedCaptureService
    from tests.test_segmented_capture_service import _full_ready_cache, _keep_cache_fresh
    cache = _full_ready_cache('policy')
    stop, thread = _keep_cache_fresh(cache)
    gate = CaptureGate(enabled=False)
    recorder = RolloutRecorder(RecorderConfig(data_root=tmp_path, sample_rate_hz=30,
        min_free_disk_bytes=1, stop_free_disk_bytes=1), cache, capture_gate=gate)
    service = SegmentedCaptureService(recorder=recorder, cache=cache, gate=gate, sidecar_root=None, monitor=False)
    try:
        yield service
    finally:
        if service.active:
            service.stop()
        stop.set(); thread.join(2)


def test_two_consecutive_online_failures_have_decodable_review_without_hil(tmp_path, real_service):
    service = real_service
    events = []
    controller = SimpleNamespace(online=True, before_start=lambda *_: None,
        started=lambda result: events.append(('start', result['episode_uuid'])),
        action=lambda _: None, finalized=lambda outcome, uuid: events.append((outcome, uuid)),
        finished=lambda: None, status=lambda: {'enabled': True, 'running': False, 'session_active': True})
    app = create_app(service=service, bridge=FakeBridge(), allowed_data_root=tmp_path)
    app.state.collection_model = controller
    ids = []
    with TestClient(app) as client:
        for index in (1, 2):
            body = dict(start_payload(tmp_path), storage_layout='flat', use_model=True, collection_model_id='expo-online')
            prepared = client.post('/api/segmented-teach/storage/prepare', json=body)
            assert prepared.status_code == 200, prepared.text
            assert prepared.json()['next_episode_index'] == index
            started = client.post('/api/segmented-teach/start', json=body)
            assert started.status_code == 200, started.text
            value = started.json(); ids.append(value['episode_uuid'])
            time.sleep(.4)
            stopped = client.post('/api/segmented-teach/stop', json={**{k: value[k] for k in ('episode_uuid', 'generation')}, 'outcome': 'failure'})
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()['capture_state'] == 'committed'
            assert not service.gate.is_open() and service._frame_count() == 0
            prefix = '/api/segmented-teach/episodes/' + ids[-1]
            query = {'data_root': str(tmp_path)}
            preview = client.get(prefix + '/preview', params=query)
            assert preview.status_code == 200, preview.text
            video = preview.json()
            assert video['state'] == 'ready', video
            assert video['review_only'] and video['source_frame_count'] >= 3
            assert video['missing_camera_frames'] == 0
            response = client.get(prefix + '/preview.mp4', params=query)
            assert response.status_code == 200 and response.headers['content-type'] == 'video/mp4'
            path = service.review_video_path(ids[-1], data_root=tmp_path)
            cap = cv2.VideoCapture(str(path)); count = 0
            while True:
                ok, image = cap.read()
                if not ok: break
                assert image.shape == (240, 960, 3)
                count += 1
            cap.release()
            assert count == video['source_frame_count']
            sidecar = client.get(prefix, params=query).json()
            assert sidecar['training_frame_count'] == 0 and not sidecar['training_intervals']
            assert sidecar['episode_outcome'] == 'failure'
            history = client.get('/api/segmented-teach/history', params=query).json()['episodes']
            assert len(history) == index and history[0]['video_available']
            assert history[0]['labelable'] is False
            assert client.get(prefix + '/review', params=query).status_code == 200
        assert len(set(ids)) == 2
        assert client.post('/api/segmented-teach/storage/prepare', json=body).json()['next_episode_index'] == 3
    assert [kind for kind, _ in events] == ['start', 'failure', 'start', 'failure']
    assert not list(tmp_path.glob('*.hdf5'))


def test_old_node_only_preview_is_explicitly_unavailable(tmp_path):
    service = service_for(tmp_path)
    started = service.start(identity(), data_root=tmp_path)
    service.stop(); service.label_outcome('failure')
    with TestClient(create_app(service=service, bridge=FakeBridge(), allowed_data_root=tmp_path)) as client:
        response = client.get('/api/segmented-teach/episodes/' + started['episode_uuid'] + '/preview', params={'data_root': str(tmp_path)})
        assert response.status_code == 200
        assert response.json()['state'] == 'unavailable'


def test_encoder_failure_is_visible_and_does_not_prevent_next_episode(tmp_path, monkeypatch):
    service = service_for(tmp_path)
    def failed_encoder(self):
        self.error = 'encoder_failed'
        self.process.stdin.close()
    monkeypatch.setattr(ReviewVideo, '_run', failed_encoder)
    started = service.start(identity(), data_root=tmp_path, review_video=True)
    service.stop(); service.label_outcome('failure')
    video = service.read_episode(started['episode_uuid'], data_root=tmp_path)['review_video']
    assert video['state'] == 'error'
    assert service.finalized_successfully()


def test_video_identity_and_missing_file_are_not_ready(tmp_path):
    root = tmp_path / 'episode'; root.mkdir()
    metadata = root / 'review-video.json'
    metadata.write_text(json.dumps({'episode_uuid': 'wrong', 'state': 'ready'}))
    assert read_review_video(root) is None
    metadata.write_text(json.dumps({'episode_uuid': 'episode', 'state': 'ready'}))
    assert read_review_video(root)['state'] == 'error'


def test_review_continues_across_policy_and_hil_without_changing_training_mask(tmp_path, real_service):
    service = real_service
    started = service.start(identity(), data_root=tmp_path, review_video=True)
    time.sleep(.2)
    assert service._frame_count() == 0
    service.observe_mode('manual:left')
    time.sleep(.25)
    assert service._frame_count() > 0
    service.observe_mode('policy')
    training_frames = service._frame_count()
    time.sleep(.2)
    service.stop()
    sidecar = service.read_episode(started['episode_uuid'], data_root=tmp_path)
    assert sidecar['training_frame_count'] == training_frames
    assert sidecar['review_video']['state'] == 'ready'
    assert sidecar['review_video']['source_frame_count'] > training_frames
    assert sidecar['training_intervals']


@pytest.mark.parametrize('damage',[None,'frame_dataset','wrong_source','uncommitted','missing_outcome'])
def test_closed_zero_hil_episode_is_not_an_unfinished_recording(tmp_path, real_service, damage):
    import h5py
    from capture_core.schema import EpisodeIdentity
    from capture_core.storage import SeriesIdentity, prepare_series
    from capture_core.series_inspection import inspect_prepared_series
    from capture_core.labels import LabelStore, LabelValidationError
    service = real_service
    ident = EpisodeIdentity('plug_cycle','expert','manual','expert80',episode_index=1,storage_layout='flat')
    started = service.start(ident, data_root=tmp_path)
    result = service.stop(); service.label_outcome('failure')
    hdf5 = tmp_path / 'episode_000001.hdf5.incomplete'
    before = hdf5.read_bytes()
    if damage == 'frame_dataset':
        with h5py.File(hdf5,'r+') as h: h.create_dataset('action',data=[[0.]])
    elif damage:
        payload=json.loads(result.sidecar.read_text())
        if damage=='wrong_source':payload['source_hdf5_relative']='episode_000099.hdf5'
        elif damage=='uncommitted':payload['capture_state']='paused'
        elif damage=='missing_outcome':payload.pop('outcome_storage')
        result.sidecar.write_text(json.dumps(payload))
    series=SeriesIdentity('recording','shared','collection',storage_layout='flat')
    prepared=prepare_series(tmp_path,series)
    if damage:
        with pytest.raises(LabelValidationError,match='latest_episode_incomplete'):
            inspect_prepared_series(LabelStore(tmp_path),prepared,series)
    else:
        inspected=inspect_prepared_series(LabelStore(tmp_path),prepared,series)
        assert inspected['latest_node_only'] and inspected['latest_labels_complete']
        assert not inspected['label_blocked'] and prepared.next_episode_index==2
        assert hdf5.read_bytes()==before
        assert not (tmp_path/'episode_000001.hdf5').exists()

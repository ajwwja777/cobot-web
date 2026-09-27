import numpy as np
import pytest

from cobot_console.camera_sync import CameraFrameUnavailable, SynchronizedCameraPreview
from capture_core.ros_cache import LatestMessageCache

KEYS = ('camera_high', 'camera_left', 'camera_right')

def put_triplet(cache, source=(10.0, 10.02, 10.04), arrival=(1.0, 1.01, 1.02), value=1):
    for index, key in enumerate(KEYS):
        image = np.full((4, 6, 3), value + index, dtype=np.uint8)
        cache.put(key, image, source_stamp=source[index], arrival_stamp=arrival[index])

def encoder(image):
    return bytes([int(image[0, 0, 0])])

def test_cache_sequence_advances_per_stream_and_snapshot_is_stable():
    cache = LatestMessageCache()
    put_triplet(cache)
    first = cache.snapshot(1.03)
    assert [first.sequence(key) for key in KEYS] == [1, 1, 1]
    cache.put('camera_left', np.zeros((4, 6, 3), np.uint8), 10.05, 1.04)
    second = cache.snapshot(1.05)
    assert [second.sequence(key) for key in KEYS] == [1, 2, 1]
    assert first.sequence('camera_left') == 1

def test_refresh_encodes_one_atomic_generation_and_does_not_repeat_unchanged_frames():
    cache = LatestMessageCache(); put_triplet(cache)
    preview = SynchronizedCameraPreview(cache, clock=lambda: 1.03, encoder=encoder)
    first = preview.refresh()
    assert first['status'] == 'ready'
    assert first['generation'] == 1
    assert first['sequences'] == {'camera_high': 1, 'camera_left': 1, 'camera_right': 1}
    assert first['skew_ms'] == pytest.approx(40.0)
    assert first['resolution']['camera_left'] == [6, 4]
    assert first['jpeg_quality'] == 82
    assert preview.image('camera_left', generation=1) == b'\x02'
    second = preview.refresh()
    assert second['generation'] == 1

def test_malformed_source_timestamps_fall_back_to_arrival_clock():
    cache = LatestMessageCache()
    put_triplet(cache, source=(float('nan'), float('nan'), float('nan')), arrival=(2.0, 2.02, 2.04))
    preview = SynchronizedCameraPreview(cache, clock=lambda: 2.07, encoder=encoder)
    state = preview.refresh()
    assert state['status'] == 'ready'
    assert state['timestamp_clock'] == 'arrival'
    assert state['skew_ms'] == pytest.approx(40.0)

def test_desync_and_stale_do_not_publish_a_mixed_generation():
    cache = LatestMessageCache(); put_triplet(cache)
    now = [1.03]
    preview = SynchronizedCameraPreview(cache, clock=lambda: now[0], encoder=encoder, max_skew_seconds=.12)
    assert preview.refresh()['generation'] == 1
    put_triplet(cache, source=(20.0, 20.02, 20.40), arrival=(1.10, 1.11, 1.12), value=5)
    now[0] = 1.13
    state = preview.refresh()
    assert state['status'] == 'desynced'
    assert state['generation'] == 1
    now[0] = 2.0
    state = preview.refresh()
    assert state['status'] == 'stale'
    assert set(state['stale_keys']) == set(KEYS)

def test_unchanged_triplet_becomes_frozen_and_old_generation_is_rejected():
    cache = LatestMessageCache(); put_triplet(cache)
    now = [1.03]
    preview = SynchronizedCameraPreview(cache, clock=lambda: now[0], encoder=encoder, freeze_seconds=.5)
    assert preview.refresh()['status'] == 'ready'
    now[0] = 1.60
    state = preview.refresh()
    assert state['status'] == 'frozen'
    assert state['last_advance_age_sec'] == pytest.approx(.57)
    with pytest.raises(CameraFrameUnavailable, match='generation'):
        preview.image('camera_high', generation=0)

def test_encoder_failure_is_bounded_and_keeps_previous_generation():
    cache = LatestMessageCache(); put_triplet(cache)
    calls = []
    def failing(image):
        calls.append(1)
        if len(calls) == 2: raise RuntimeError('jpeg failed')
        return b'ok'
    preview = SynchronizedCameraPreview(cache, clock=lambda: 1.03, encoder=failing)
    state = preview.refresh()
    assert state['status'] == 'unavailable'
    assert state['error_code'] == 'camera_encode_failed'
    assert state['generation'] == 0
    assert len(calls) == 2

from fastapi.testclient import TestClient
from cobot_console.api import create_app
from tests.test_console_api import FakeBackend, FakeBridge, FakeRecorder, FakeRegistry, FakeSegmentedService, _fresh_cache

class FakeCameraPreview:
    def state(self):
        return {'status':'ready','generation':7,'skew_ms':12.0,'sequences':{key:3 for key in KEYS}}
    def image(self, key, generation=None):
        assert generation == 7
        return b'jpeg-' + key.encode()

def test_console_camera_routes_expose_one_generation(tmp_path):
    app = create_app(cache=_fresh_cache(), bridge=FakeBridge(), recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(), backend_client=FakeBackend(),
        lifecycle_registry=FakeRegistry('ready_disarmed'), allowed_data_root=tmp_path,
        rlt_data_root=tmp_path, monotonic=lambda:10.0, camera_preview=FakeCameraPreview())
    with TestClient(app) as client:
        state = client.get('/api/console/cameras')
        image = client.get('/api/console/cameras/camera_left.jpg?generation=7')
        wrong = client.get('/api/console/cameras/not-a-camera.jpg?generation=7')
    assert state.status_code == 200 and state.json()['generation'] == 7
    assert image.status_code == 200 and image.content == b'jpeg-camera_left'
    assert image.headers['cache-control'] == 'no-store'
    assert wrong.status_code == 404

def test_preview_encoding_at_fifteen_hz_skips_to_latest_triplet():
    cache = LatestMessageCache(); put_triplet(cache)
    now=[1.03]; calls=[]
    preview=SynchronizedCameraPreview(cache,clock=lambda:now[0],encoder=lambda image:(calls.append(1) or b'x'),preview_fps=15.0)
    assert preview.refresh()['generation']==1
    put_triplet(cache,source=(11,11.01,11.02),arrival=(1.05,1.05,1.05),value=3)
    now[0]=1.06
    assert preview.refresh()['generation']==1
    assert len(calls)==3
    now[0]=1.16
    assert preview.refresh()['generation']==2
    assert len(calls)==6

from pathlib import Path
from uuid import uuid4

import h5py
import pytest
from fastapi.testclient import TestClient

from capture_core.api import create_app
from capture_core.labels import LabelStore
from capture_core.deletion import read_deletion_records
from segmented_capture.ports import CaptureGate
from tests.test_api import FakeRecorder, _start_payload


class DiskRecorder(FakeRecorder):
    def __init__(self, empty=False):
        super().__init__()
        self.empty = empty

    def start(self, request):
        result = super().start(request)
        ident = request.identity
        directory = Path(request.data_root)
        if ident.storage_layout != 'flat':
            directory = directory / ident.task_id / ident.model_id / ident.dataset_round
        self.path = directory / f'episode_{ident.episode_index:06d}.hdf5'
        if self.empty:
            self.path = Path(str(self.path) + '.incomplete')
        with h5py.File(self.path, 'w') as stream:
            stream.attrs.update(episode_uuid=str(ident.episode_uuid), episode_index=ident.episode_index,
                                completion_state='error' if self.empty else 'complete')
            if self.empty:
                stream.attrs['failure_reason'] = 'empty_episode'
        return result


@pytest.mark.parametrize('empty', [True, False])
@pytest.mark.parametrize('layout', ['legacy', 'flat'])
def test_rlt_discard_removes_artifacts_and_identity_without_ledger(tmp_path, empty, layout):
    recorder = DiskRecorder(empty)
    gate = CaptureGate(enabled=False)
    client = TestClient(create_app(recorder=recorder, label_store=LabelStore(tmp_path), capture_gate=gate))
    response = client.post('/api/episodes/start', json={**_start_payload(), 'storage_layout': layout})
    assert response.status_code == 200, response.text
    uuid = response.json()['episode_uuid']
    preview = recorder.path.parent / '.previews' / uuid
    preview.mkdir(parents=True)
    (preview/'preview.mp4').write_bytes(b'video')
    if not empty:
        recorder.path.with_suffix('.labels.json').write_text('{}')
    bad = client.post('/api/episodes/discard', json={'episode_uuid': str(uuid4())})
    assert bad.status_code == 409 and recorder.path.exists()
    discarded = client.post('/api/episodes/discard', json={'episode_uuid': uuid})
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()['discarded']
    assert not gate.is_open()
    assert not [p for p in tmp_path.rglob('*') if p.is_file()]
    assert read_deletion_records(recorder.path.parent) == ()
    assert client.get('/api/episodes').json() == []
    assert client.post('/api/episodes/discard', json={'episode_uuid': uuid}).json() == discarded.json()
    # Reuse of the numerical index cannot let a stale discard affect a new UUID.
    started = client.post('/api/episodes/start', json={**_start_payload(), 'storage_layout': layout})
    assert started.status_code == 200, started.text
    assert started.json()['episode_uuid'] != uuid
    assert client.post('/api/episodes/discard', json={'episode_uuid': uuid}).status_code == 200
    assert recorder.path.exists()


def test_normal_discard_removes_segment_nodes_without_deletion_record(project_tmp):
    from tests.test_segmented_capture_service import FakeRecorder as CaptureRecorder, ready_cache, identity
    from segmented_capture.capture_service import SegmentedCaptureService
    recorder = CaptureRecorder()
    root = project_tmp/'data'
    service = SegmentedCaptureService(recorder=recorder, cache=ready_cache('manual:left'),
        gate=CaptureGate(enabled=False), sidecar_root=root/'.segments', clock=lambda: 10.0, monitor=False)
    ident = identity()
    service.start(ident, data_root=root)
    directory = root/ident.task_id/ident.model_id/ident.dataset_round
    directory.mkdir(parents=True, exist_ok=True)
    episode = directory/f'episode_{ident.episode_index:06d}.hdf5'
    with h5py.File(episode,'w') as stream:
        stream.attrs.update(episode_uuid=str(ident.episode_uuid), episode_index=ident.episode_index, completion_state='complete')
    # Discard must not depend on obtaining a fresh terminal camera snapshot.
    service._capture_node_snapshot = lambda *_: (_ for _ in ()).throw(RuntimeError('camera unavailable'))
    assert service.discard()['capture_state'] == 'idle'
    assert not [p for p in root.rglob('*') if p.is_file()]
    assert read_deletion_records(directory) == ()


def test_real_writer_discard_stops_and_clears_frames_and_all_files(project_tmp):
    from tests.test_segmented_capture_service import (_full_ready_cache, _keep_cache_fresh,
        _wait_written, identity, DiskUsage, GIB)
    from capture_core.config import RecorderConfig
    from capture_core.recorder import RolloutRecorder
    from segmented_capture.capture_service import SegmentedCaptureService
    cache = _full_ready_cache('manual:left')
    stop, thread = _keep_cache_fresh(cache)
    root = project_tmp/'raw'
    gate = CaptureGate(enabled=False)
    recorder = RolloutRecorder(RecorderConfig(data_root=root, sample_rate_hz=30,
        max_duration_seconds=2, min_free_disk_bytes=1, stop_free_disk_bytes=1), cache,
        capture_gate=gate, disk_usage=lambda _: DiskUsage(100*GIB,50*GIB,50*GIB))
    service = SegmentedCaptureService(recorder=recorder, cache=cache, gate=gate,
        sidecar_root=root/'.segments', monitor=False)
    try:
        service.start(identity(), data_root=root)
        _wait_written(recorder, 3)
        service.discard()
        assert not [p for p in root.rglob('*') if p.is_file()]
        status = recorder.status()
        assert status['state'] == 'idle' and status['path'] is None
        assert status['frames_written'] == status['frames_sampled'] == 0
    finally:
        stop.set()
        thread.join(timeout=1)

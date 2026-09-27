from dataclasses import replace
from pathlib import Path
import json
import h5py
from fastapi.testclient import TestClient
from capture_core.schema import EpisodeIdentity
from capture_core.storage import SeriesIdentity, prepare_series
from capture_core.hdf5_writer import Hdf5EpisodeWriter
from capture_core.labels import LabelStore
from capture_core.exact_labels import ExactEpisodeLabelStore
from capture_core.series_inspection import inspect_prepared_series
from segmented_capture.api import create_app
from segmented_capture.capture_service import SegmentedCaptureService
from segmented_capture.ports import CaptureGate
from tests.test_hdf5_writer import _frame
from tests.test_segmented_capture_service import FakeRecorder, ready_cache
from tests.test_segmented_capture_http import FakeService, FakeBridge

def test_directory_only_request_prepares_and_starts_flat(project_tmp):
    service=FakeService(project_tmp)
    with TestClient(create_app(service=service,bridge=FakeBridge(),allowed_data_root=project_tmp)) as client:
        body={"data_root":str(project_tmp),"storage_layout":"flat"}
        p=client.post("/api/segmented-teach/storage/prepare",json=body)
        assert p.status_code==200 and p.json()["episode_directory"]==str(project_tmp)
        p=client.post("/api/segmented-teach/start",json=body)
        assert p.status_code==200 and service.identity.storage_layout=="flat"
        assert not (project_tmp/"plug").exists()
        assert client.post("/api/segmented-teach/storage/prepare",json={**body,"data_root":str(project_tmp.parent)}).status_code==422

def test_flat_writer_labels_exact_lookup_and_collision(project_tmp):
    identity=EpisodeIdentity("plug","expert","manual","v1",episode_index=1,storage_layout="flat")
    writer=Hdf5EpisodeWriter(project_tmp,identity,4,wall_clock=lambda:100.0)
    writer.append(_frame(identity,0))
    path=writer.finalize(end_timestamp=101.0)
    assert path==project_tmp/"episode_000001.hdf5"
    store=LabelStore(project_tmp)
    record=store._read_record(path)
    assert record is not None
    assert ExactEpisodeLabelStore(project_tmp,path.name)._find_record(identity.episode_uuid).path==path
    series=SeriesIdentity("plug","new_model","v2","flat")
    prepared=prepare_series(project_tmp,series)
    assert prepared.episode_directory==project_tmp and prepared.next_episode_index==2
    assert inspect_prepared_series(store,prepared,series)["latest_episode_uuid"]==str(identity.episode_uuid)
    with h5py.File(path,"r+") as f:f.attrs["storage_layout"]="legacy"
    assert store._read_record(path) is None

def test_flat_capture_history_nodes_review_and_delete(project_tmp):
    identity=EpisodeIdentity("plug","expert","manual","v1",episode_index=1,storage_layout="flat")
    recorder=FakeRecorder()
    service=SegmentedCaptureService(recorder=recorder,cache=ready_cache("manual:left"),gate=CaptureGate(enabled=False),sidecar_root=None,clock=lambda:10.0,monitor=False)
    service.start(identity,data_root=project_tmp)
    recorder.frames_sampled=recorder.frames_written=1
    writer=Hdf5EpisodeWriter(project_tmp,identity,4,wall_clock=lambda:100.0);writer.append(_frame(identity,0));writer.finalize(end_timestamp=101.0)
    result=service.stop()
    uuid=str(identity.episode_uuid)
    assert result.episode_root==project_tmp/".segments"/uuid
    assert service.read_episode(uuid,data_root=project_tmp)["source_hdf5_relative"]=="episode_000001.hdf5"
    assert service.list_episodes(data_root=project_tmp)[0]["episode_uuid"]==uuid
    assert service.load_review(uuid,data_root=project_tmp)["review_revision"]==0
    assert service.replay_artifacts(uuid,data_root=project_tmp).episode_path==project_tmp/"episode_000001.hdf5"
    service.delete_episode(uuid,data_root=project_tmp)
    assert not (project_tmp/"episode_000001.hdf5").exists()
    assert not (project_tmp/".segments"/uuid).exists()
    assert prepare_series(project_tmp,SeriesIdentity("plug","expert","v1","flat"),reuse_deleted_indices=False).next_episode_index==2
